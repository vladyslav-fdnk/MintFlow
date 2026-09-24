FROM ghcr.io/astral-sh/uv:0.11.12 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN uv sync --frozen --no-dev

# Everything runs as an unprivileged user that owns nothing in the image (operations design, O1).
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mintflow
USER mintflow

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"]

# Forwarding headers are the application's decision (MINTFLOW_TRUSTED_PROXIES), never uvicorn's.
CMD ["uvicorn", "--factory", "mintflow.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
