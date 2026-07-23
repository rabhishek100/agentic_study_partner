FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-dev --no-install-project
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libmagic1 \
        libxcb1 \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY api ./api
COPY parsing ./parsing
COPY retrieval/__init__.py retrieval/chunking.py retrieval/langchain.py \
    retrieval/models.py retrieval/postgres.py retrieval/reranker.py \
    retrieval/search.py retrieval/vector.py ./retrieval/
COPY storage/__init__.py storage/database.py storage/postgres.py ./storage/
COPY study ./study

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
