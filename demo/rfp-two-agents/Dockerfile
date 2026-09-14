# Build context = repo root (Railway dockerfilePath: demo/rfp-two-agents/Dockerfile)
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY demo/rfp-two-agents/requirements.txt /tmp/demo-requirements.txt

# Full backend deps (demo imports production app). Skip Playwright browsers — not used by this demo.
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt \
    && pip install --no-cache-dir -r /tmp/demo-requirements.txt

COPY backend/app ./backend/app
COPY demo/rfp-two-agents ./demo/rfp-two-agents

WORKDIR /app/demo/rfp-two-agents

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
