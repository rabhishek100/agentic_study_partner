FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-cache --no-dev --no-install-project

COPY api ./api
COPY parsing ./parsing
COPY retrieval ./retrieval
COPY storage ./storage
COPY study ./study

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
