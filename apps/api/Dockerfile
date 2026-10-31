FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# Development default: migrate, then serve. Production runs migrations as a separate
# one-shot service and overrides this command (deploy/docker-compose.prod.yml).
# FORWARDED_ALLOW_IPS must list only the web container (never "*"); see docs/DEPLOYMENT.md.
# One worker on purpose: rate-limit state is per process (docs/SECURITY.md).
ENV UVICORN_CMD="uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log --proxy-headers --timeout-graceful-shutdown 20"
CMD ["sh", "-c", "alembic upgrade head && exec $UVICORN_CMD --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1}"]
