# RadarPrice API — imagen de producción.
#   docker build -t radarprice-api .
#   docker run -p 8000:8000 -v radarprice-data:/data --env-file .env radarprice-api
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    # SQLite en un volumen persistente (/data). Producción: datos reales, sin demo.
    RADARPRICE_DATABASE_URL=sqlite+aiosqlite:////data/radarprice.db \
    RADARPRICE_DEMO_DATA=false

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Usuario sin privilegios; /data es el único directorio escribible.
RUN useradd --create-home --uid 10001 radarprice && mkdir -p /data && chown radarprice:radarprice /data
USER radarprice
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"

# Un solo worker: el sync periódico vive en el proceso (ver README). Detrás de un proxy HTTPS.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
