from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from app.container import Container, get_container
from app.errors import InvalidRequest, NotFound
from app.schemas import SimulatePayment, WebhookEvent
from app.services import payments

router = APIRouter(tags=["payments"])


@router.post("/payments/webhook", status_code=status.HTTP_202_ACCEPTED)
async def payment_webhook(request: Request, ctx: Container = Depends(get_container)):
    """Gateway callback. Verified and queued here; applied by the stream consumer."""
    body = await request.body()
    payments.verify_signature(ctx.settings.webhook_secret, body, request.headers.get(payments.SIGNATURE_HEADER))
    try:
        event = WebhookEvent.model_validate_json(body)
    except ValidationError as exc:
        raise InvalidRequest("Malformed webhook payload", errors=exc.errors(include_url=False)) from None
    message_id = await payments.enqueue_webhook(ctx, body)
    return {"queued": True, "event_id": event.event_id, "message_id": message_id}


@router.post("/dev/gateway/simulate", status_code=status.HTTP_202_ACCEPTED, tags=["dev"])
async def simulate_gateway(body: SimulatePayment, ctx: Container = Depends(get_container)):
    """Dev only: sign and queue the webhook the mock gateway would send for an order."""
    if not ctx.settings.enable_dev_gateway:
        raise NotFound("Not found")
    raw = await payments.build_simulated_event(ctx, body.gateway_order_id, body.outcome)
    message_id = await payments.enqueue_webhook(ctx, raw)
    return {"queued": True, "message_id": message_id, "event": WebhookEvent.model_validate_json(raw)}
