# =============================================================================
# Stage 1 — Builder: install deps into an isolated layer
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for psycopg2 / asyncpg compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

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
COPY --chown=app:app . .

# Create runtime directories
RUN mkdir -p /app/logs && chown app:app /app/logs

USER app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Gunicorn-style multi-worker production startup
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
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

FROM nginx:1.27-alpine AS web
COPY --from=dashboard-build /dashboard/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/nginx.conf
