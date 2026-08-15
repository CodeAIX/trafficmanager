FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
RUN addgroup --system fleet && adduser --system --ingroup fleet fleet \
    && mkdir -p /data /app/backend/app/static \
    && chown -R fleet:fleet /data /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
COPY --from=frontend-build /build/frontend/dist/ /app/backend/app/static/
USER fleet
EXPOSE 2096
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2096/health', timeout=3)"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn backend.app.main:app --host 0.0.0.0 --port 2096 --workers 1"]
