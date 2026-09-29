FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_HEADLESS=true \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    APP_DATA_DIR=/app/unifi_app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        tini \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Python running as PID 1 never reaps the processes Chrome orphans (crashpad handler,
# killed renderers...). They pile up as zombies over days until Chrome can no longer
# start; tini reaps them and forwards stop signals.
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["python", "-m", "unifi_backup_app.runner"]
