"""The ingestion job state machine.

Every status change goes through :func:`validate_transition`. API handlers and
worker stages never write an arbitrary status, so an illegal move is a caught
programming error rather than a job stuck in an impossible state.

``status`` is lifecycle; ``stage`` is the current unit of work. They are
recorded separately so ``retry_scheduled`` can remember that the failure
happened during ``build_embeddings``.
"""

from enum import StrEnum


class Status(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED = "queued"
    VALIDATING = "validating"
    PARSING = "parsing"
    PERSISTING = "persisting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    VERIFYING = "verifying"
    READY = "ready"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Reserved for the scanned/TOC-less workflow.
    CLASSIFYING = "classifying"
    OCR = "ocr"
    NEEDS_TOC_REVIEW = "needs_toc_review"


class Stage(StrEnum):
    VERIFY_UPLOAD = "verify_upload"
    DOWNLOAD_SOURCE = "download_source"
    PREFLIGHT = "preflight"
    PARSE_PAGES = "parse_pages"
    PERSIST_CANONICAL = "persist_canonical"
    BUILD_CHUNKS = "build_chunks"
    BUILD_EMBEDDINGS = "build_embeddings"
    VERIFY_BOOK = "verify_book"
    CLASSIFY = "classify"
    OCR_PAGES = "ocr_pages"
    PROPOSE_TOC = "propose_toc"


TERMINAL_STATUSES = frozenset({Status.READY, Status.FAILED, Status.CANCELLED})

# Statuses in which the worker holds a lease and is doing real work.
PROCESSING_STATUSES = frozenset(
    {
        Status.VALIDATING,
        Status.PARSING,
        Status.PERSISTING,
        Status.CHUNKING,
        Status.EMBEDDING,
        Status.VERIFYING,
        Status.CLASSIFYING,
        Status.OCR,
    }
)

# Statuses a polling worker may claim.
CLAIMABLE_STATUSES = frozenset({Status.QUEUED, Status.RETRY_SCHEDULED})

# The ordered structured pipeline. Each entry is the status a worker enters and
# the stage it starts with.
PIPELINE: tuple[tuple[Status, Stage], ...] = (
    (Status.VALIDATING, Stage.VERIFY_UPLOAD),
    (Status.PARSING, Stage.PARSE_PAGES),
    (Status.PERSISTING, Stage.PERSIST_CANONICAL),
    (Status.CHUNKING, Stage.BUILD_CHUNKS),
    (Status.EMBEDDING, Stage.BUILD_EMBEDDINGS),
    (Status.VERIFYING, Stage.VERIFY_BOOK),
)

# Any processing status may fail, be scheduled for retry, or be cancelled at a
# safe boundary, so those edges are added to every processing state below.
# ``validating`` is included as well: a resumed attempt whose earlier work is
# not recoverable restarts from the beginning rather than continuing on top of
# state it cannot verify.
_INTERRUPTIONS = frozenset(
    {
        Status.FAILED,
        Status.RETRY_SCHEDULED,
        Status.CANCELLED,
        Status.VALIDATING,
    }
)

_ALLOWED: dict[Status, frozenset[Status]] = {
    Status.AWAITING_UPLOAD: frozenset({Status.QUEUED, Status.CANCELLED, Status.FAILED}),
    Status.QUEUED: frozenset({Status.VALIDATING, Status.CANCELLED, Status.FAILED}),
    Status.VALIDATING: frozenset({Status.PARSING, Status.READY})
    | (_INTERRUPTIONS - {Status.VALIDATING}),
    Status.PARSING: frozenset({Status.PERSISTING}) | _INTERRUPTIONS,
    Status.PERSISTING: frozenset({Status.CHUNKING}) | _INTERRUPTIONS,
    Status.CHUNKING: frozenset({Status.EMBEDDING}) | _INTERRUPTIONS,
    # Chunks and embeddings are derived data and always rebuildable, so a
    # resumed attempt may drop back to rebuild them rather than trust partial
    # output it cannot verify. Canonical content is never re-entered this way.
    Status.EMBEDDING: frozenset({Status.VERIFYING, Status.CHUNKING})
    | _INTERRUPTIONS,
    Status.VERIFYING: frozenset({Status.READY, Status.CHUNKING}) | _INTERRUPTIONS,
    # A scheduled retry resumes at whichever stage failed, so it may re-enter
    # any processing status directly.
    Status.RETRY_SCHEDULED: frozenset(PROCESSING_STATUSES)
    | frozenset({Status.QUEUED, Status.CANCELLED, Status.FAILED}),
    # Manual retry is an immediately scheduled retry: it keeps the recorded
    # stage so the claim resumes from valid checkpoints. Sending the job back
    # to ``queued`` instead would strand that stage, because ``queued`` may
    # only ever enter the pipeline at validation.
    Status.FAILED: frozenset({Status.RETRY_SCHEDULED}),
    Status.READY: frozenset(),
    Status.CANCELLED: frozenset(),
    Status.CLASSIFYING: frozenset({Status.OCR, Status.NEEDS_TOC_REVIEW, Status.PARSING})
    | _INTERRUPTIONS,
    Status.OCR: frozenset({Status.NEEDS_TOC_REVIEW, Status.PARSING}) | _INTERRUPTIONS,
    Status.NEEDS_TOC_REVIEW: frozenset({Status.PARSING, Status.CANCELLED, Status.FAILED}),
}

# ``validating`` may go straight to ``ready`` for a duplicate upload: the owner
# already has a ready book with this file hash, so no work is repeated.


class InvalidTransitionError(RuntimeError):
    """A status change that the lifecycle does not permit."""


def is_terminal(status: Status | str) -> bool:
    return Status(status) in TERMINAL_STATUSES


def can_transition(current: Status | str, target: Status | str) -> bool:
    """Return whether ``current`` may move to ``target``."""

    return Status(target) in _ALLOWED[Status(current)]


def validate_transition(current: Status | str, target: Status | str) -> Status:
    """Return the validated target status or raise."""

    current_status, target_status = Status(current), Status(target)
    if not can_transition(current_status, target_status):
        raise InvalidTransitionError(
            f"cannot move ingestion job from {current_status} to {target_status}"
        )
    return target_status


def next_pipeline_step(status: Status | str) -> tuple[Status, Stage] | None:
    """Return the status/stage that follows ``status``, or None at the end."""

    current = Status(status)
    if current is Status.QUEUED:
        return PIPELINE[0]
    for index, (pipeline_status, _) in enumerate(PIPELINE):
        if pipeline_status is current:
            if index + 1 < len(PIPELINE):
                return PIPELINE[index + 1]
            return None
    return None


def resume_step(stage: Stage | str | None) -> tuple[Status, Stage]:
    """Return where a retried job resumes for a recorded last stage.

    Resuming re-runs the whole stage rather than trusting partial output: every
    stage is idempotent, and repeating one is much cheaper than reasoning about
    how far a crashed process actually got.
    """

    if stage is None:
        return PIPELINE[0]
    target = Stage(stage)
    for status, pipeline_stage in PIPELINE:
        if pipeline_stage is target:
            return status, pipeline_stage
    # Stages inside the validating status share its entry point.
    if target in {Stage.VERIFY_UPLOAD, Stage.DOWNLOAD_SOURCE, Stage.PREFLIGHT}:
        return Status.VALIDATING, target
    return PIPELINE[0]
