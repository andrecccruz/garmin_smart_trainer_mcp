# Imagem mínima para rodar o MCP isolado (Podman rootless ou Docker).
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
 && rm -rf /root/.cache

RUN useradd --system --no-create-home --uid 10001 mcp
USER mcp

ENTRYPOINT ["/opt/venv/bin/intervals-mcp"]
