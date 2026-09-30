"""Background loops that run inside every API process.

Both are safe to run N times in parallel: the sweeper uses SKIP LOCKED, and the
webhook consumers share one Redis consumer group, so each message goes to one worker.
"""

import asyncio
import logging

from pydantic import ValidationError
from redis.exceptions import ResponseError

from app.container import Container
from app.schemas import WebhookEvent
from app.services.bookings import expire_due_holds
from app.services.payments import process_webhook_event

log = logging.getLogger(__name__)

CLAIM_IDLE_MS = 30_000  # a message unacked this long is assumed orphaned by a dead worker


async def run_hold_sweeper(ctx: Container, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if expired := await expire_due_holds(ctx):
                log.info("expired %d holds", expired)
        except Exception:
            log.exception("hold sweeper iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), ctx.settings.sweeper_interval_seconds)
        except TimeoutError:
            pass


async def ensure_consumer_group(ctx: Container) -> None:
    try:
        await ctx.redis.xgroup_create(ctx.settings.webhook_stream, ctx.settings.webhook_group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def consume_webhooks_once(
    ctx: Container, consumer: str, *, block_ms: int | None, reclaim: bool = False
) -> int:
    """Process one batch from the stream. Returns the number of messages handled."""
    stream, group = ctx.settings.webhook_stream, ctx.settings.webhook_group
    messages = []
    if reclaim:
        try:
            _, claimed, *_ = await ctx.redis.xautoclaim(
                stream, group, consumer, min_idle_time=CLAIM_IDLE_MS, start_id="0-0", count=50
            )
            messages.extend(claimed)
        except ResponseError as exc:
            if "NOGROUP" not in str(exc):
                raise
    try:
        response = await ctx.redis.xreadgroup(group, consumer, {stream: ">"}, count=50, block=block_ms)
    except ResponseError as exc:
        if "NOGROUP" not in str(exc):
            raise
        # Redis lost the stream/group (restart without persistence): recreate from id 0,
        # so anything queued since is still delivered.
        await ensure_consumer_group(ctx)
        response = await ctx.redis.xreadgroup(group, consumer, {stream: ">"}, count=50, block=block_ms)
    for _, batch in response or []:
        messages.extend(batch)

    for message_id, fields in messages:
        if not fields:  # entry trimmed from the stream while pending
            await ctx.redis.xack(stream, group, message_id)
            continue
        try:
            event = WebhookEvent.model_validate_json(fields["body"])
        except ValidationError:
            log.error("dropping malformed webhook message %s", message_id)
            await ctx.redis.xack(stream, group, message_id)
            continue
        try:
            outcome = await process_webhook_event(ctx, event)
        except Exception:
            # Left unacked: stays pending and is retried via XAUTOCLAIM.
            log.exception("webhook %s failed, will retry", event.event_id)
            continue
        await ctx.redis.xack(stream, group, message_id)
        log.info("webhook %s -> %s", event.event_id, outcome)
    return len(messages)


async def run_webhook_consumer(ctx: Container, consumer: str, stop: asyncio.Event) -> None:
    await ensure_consumer_group(ctx)
    loop = asyncio.get_running_loop()
    last_reclaim = 0.0
    while not stop.is_set():
        reclaim = loop.time() - last_reclaim > CLAIM_IDLE_MS / 2000
        try:
            await consume_webhooks_once(ctx, consumer, block_ms=1000, reclaim=reclaim)
            if reclaim:
                last_reclaim = loop.time()
        except Exception:
            log.exception("webhook consumer iteration failed")
            await asyncio.sleep(1)
