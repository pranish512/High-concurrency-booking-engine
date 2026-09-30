FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "uvicorn[standard]"
COPY app ./app
COPY sql ./sql
COPY scripts ./scripts

EXPOSE 8000
# Apply pending migrations (tracked, so restarts are no-ops), then serve.
CMD ["sh", "-c", "python -m app.migrate --seed && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
