# syntax=docker/dockerfile:1
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ARG GIT_COMMIT=dev
RUN GIT_COMMIT=$GIT_COMMIT npm run build

FROM python:3.12-slim
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static
# `spend enroll` etc. inside the container
RUN printf '#!/bin/sh\nexec python -m app.cli "$@"\n' > /usr/local/bin/spend && chmod 755 /usr/local/bin/spend \
 && useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data /backups && chown app:app /data /backups
ARG GIT_COMMIT=dev
ENV DB_PATH=/data/spend.db STATIC_DIR=/srv/static GIT_COMMIT=$GIT_COMMIT PYTHONUNBUFFERED=1 \
    GOOGLE_SA_FILE=/run/secrets/google-sa.json
# uid 1000 = maize on mysites, so the chmod 600 key file owned by maize is readable read-only.
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
