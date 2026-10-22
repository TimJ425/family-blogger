# Base image pinned by digest for reproducible builds (python:3.12-slim).
# Dependabot/Renovate or a manual `docker buildx imagetools inspect
# python:3.12-slim` can refresh it.
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

# tzdata so TZ (set in compose.yaml) gives correct local diary dates
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FB_INSTANCE_DIR=/data

# Normal user (uid 1000) and a data folder it owns. Compose can still
# override the user with PUID/PGID.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY config.py run.py LICENCE.md ./
COPY family_blogger ./family_blogger

USER app

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/login', timeout=4)" || exit 1

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8765"]
