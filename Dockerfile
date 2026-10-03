FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
ARG INSTALL_VOICE=false
RUN if [ "$INSTALL_VOICE" = "true" ]; then \
      uv sync --frozen --no-cache --no-dev --no-install-project --extra voice; \
    else uv sync --frozen --no-cache --no-dev --no-install-project; fi
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libmagic1 \
        libxcb1 \
        ffmpeg \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN /app/.venv/bin/playwright install --with-deps chromium

COPY api ./api
COPY observability.py operations_telemetry.py model_routing.py ./
COPY decks ./decks
COPY ingestion ./ingestion
COPY interviews ./interviews
COPY narration ./narration
COPY notifications ./notifications
COPY parsing ./parsing
COPY retrieval ./retrieval
COPY revision_sheets ./revision_sheets
# The whole package: enumerating individual modules here means a new one is
# missing from the image until someone remembers to add it, which fails at
# import time on the deployed service rather than in CI.
COPY storage ./storage
COPY study ./study
COPY video ./video
COPY worker ./worker
# Operational commands the runbook refers to, and the parse benchmark, need
# to be runnable inside the deployed image rather than only from a laptop.
COPY scripts/__init__.py scripts/benchmark_parse.py scripts/compare_extraction.py \
    scripts/migrate_video_media_to_s3.py scripts/upgrade_video_course.py \
    scripts/serve.py scripts/check_langsmith_tracing.py ./scripts/

ENV PATH="/app/.venv/bin:$PATH"

# What this image was built from, reported by /api/health and logged by the
# worker at startup. Deployment drift is invisible without it: the worker once
# ran five commits behind the repository for hours, and the only way to notice
# was comparing a deployment timestamp against a git log by eye.
#
# Railway does not inject these, so a deploy that wants them must pass them:
#   railway up --service worker  (leaves them unknown, which is honest)
#   docker build --build-arg BUILD_REVISION=$(git rev-parse --short HEAD) ...
ARG BUILD_REVISION=unknown
ARG BUILD_TIME=unknown
ENV BUILD_REVISION=$BUILD_REVISION \
    BUILD_TIME=$BUILD_TIME

# The API and the worker share one image and differ only by command. Splitting
# them into a slim API image and a parser/OCR worker image is deferred; the
# worker's extra toolchain is the only difference in content.
EXPOSE 8000

# One image, three roles. START_COMMAND selects the worker
# (`python -m worker.main`) or the combined service (`python -m scripts.serve`,
# which runs the API and worker together so they share one media volume);
# without it the container serves the API alone. Shell form so a
# platform-injected $PORT is honoured, and exec so the process still receives
# SIGTERM directly, which the worker relies on to stop claiming new jobs.
CMD ["sh", "-c", "if [ -n \"$START_COMMAND\" ]; then exec sh -c \"$START_COMMAND\"; fi; exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
