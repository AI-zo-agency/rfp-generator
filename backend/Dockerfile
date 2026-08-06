FROM python:3.11-slim

WORKDIR /app

# System libs for Playwright Chromium + build tools for some Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# Persist JustWin login session on a Railway volume mounted at /data
RUN mkdir -p /data
ENV JUSTWIN_HEADLESS=true
ENV JUSTWIN_SESSION_PATH=/data/justwin-session.json

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 75 --proxy-headers --forwarded-allow-ips='*'"]
