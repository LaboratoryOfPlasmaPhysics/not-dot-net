FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY not_dot_net ./not_dot_net

RUN uv sync --no-dev

RUN useradd --system --uid 1000 --home /data notdotnet \
    && mkdir -p /data /secrets \
    && chown -R notdotnet:notdotnet /data /secrets /app

USER notdotnet
WORKDIR /data
VOLUME ["/data", "/secrets"]

EXPOSE 8088

ENTRYPOINT ["python", "-m", "not_dot_net.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8088", "--secrets-file", "/secrets/secrets.key"]
