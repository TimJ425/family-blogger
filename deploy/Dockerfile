FROM python:3.12-slim

# tzdata so TZ (set in compose.yaml) gives correct local diary dates
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FB_INSTANCE_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py run.py LICENCE.md ./
COPY family_blogger ./family_blogger

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/login', timeout=4)" || exit 1

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8765"]
