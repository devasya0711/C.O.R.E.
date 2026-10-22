FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1

# The runtime is intentionally self-contained because the execution container
# is started with --network=none. Repository-specific dependencies must be
# included in the approved image before it is used by a migration worker.
RUN pip install --no-cache-dir --disable-pip-version-check \
    fastapi httpx pydantic pytest sqlalchemy

RUN groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid sandbox --home-dir /home/sandbox \
        --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /workspace

USER sandbox
