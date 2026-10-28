# =============================================================================
# Stage 1 — Builder: install deps into an isolated layer
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

# Runtime dependencies provide wheels on the supported Linux x86-64 target.
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# =============================================================================
# Stage 2 — Runtime: minimal image, no build tools
# =============================================================================
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Non-root user for security
RUN groupadd --system app && useradd --system --gid app app

# Application source
COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app scripts/check_production.py ./scripts/check_production.py

# Create runtime directories
RUN mkdir -p /app/logs /app/storage && chown -R app:app /app/logs /app/storage

USER app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Call ownership and active streams are process-local: keep one worker.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-graceful-shutdown", "120", \
     "--loop", "uvloop", \
     "--http", "h11", \
     "--no-access-log", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]

# Build the existing React dashboard as static assets for the public proxy.
FROM node:22-alpine AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN VITE_API_BASE_URL= npm run build

FROM nginx:stable-alpine AS web
COPY --from=dashboard-build /dashboard/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/nginx.conf
