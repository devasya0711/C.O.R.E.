FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /build

COPY pyproject.toml ./
COPY app ./app
RUN mkdir -p /opt/tiktoken-cache \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir . \
    && /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates docker.io git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system core \
    && useradd --system --gid core --home-dir /nonexistent --shell /usr/sbin/nologin core

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/tiktoken-cache /opt/tiktoken-cache
COPY --from=builder --chown=core:core /build/app ./app
COPY --chown=core:core alembic.ini ./
COPY --chown=core:core alembic ./alembic
COPY --chown=core:core docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod 0755 /app/docker-entrypoint.sh

USER core

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
