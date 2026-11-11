# Pairverse / Liquidity Universe
# Build from the repo root:
#   docker build -f deploy/Dockerfile -t pairverse:local .
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PAIRS_DB=/data/pairs.db \
    PAIRS_CACHE=/data/cache \
    PORT=8765

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates sqlite3 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code only — the SQLite DB and cache live on a volume at /data
COPY api.py public.py db.py poller.py enrich.py seed.py http_util.py helius.py images.py cli.py /app/
COPY site/ /app/site/
COPY static/ /app/static/
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /data/cache /app/logs \
    && useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin pairverse \
    && chown -R pairverse:pairverse /app /data

USER pairverse
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).read()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
