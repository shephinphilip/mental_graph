FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend-core:/app/backend-agent

WORKDIR /app

RUN addgroup --system zenark && adduser --system --ingroup zenark --home /app zenark

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=zenark:zenark backend-core /app/backend-core
COPY --chown=zenark:zenark backend-agent /app/backend-agent

USER zenark

WORKDIR /app/backend-agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
