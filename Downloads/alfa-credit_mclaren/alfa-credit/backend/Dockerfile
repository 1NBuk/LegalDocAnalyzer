FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --system --uid 10001 --no-create-home app && mkdir -p /data && chown app /data
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app app
COPY credit_ds credit_ds
COPY artifacts artifacts
# демо-режим работает от root (совместимо с уже созданными томами); в docker-compose.prod.yml задан user 10001
ENV DB_PATH=/data/app.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request as u;u.urlopen('http://localhost:8000/api/ready')"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers ${WEB_CONCURRENCY:-1}"]
