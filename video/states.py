"""Stable lifecycle values for the independent video-ingestion queue."""

from enum import StrEnum


class Status(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Stage(StrEnum):
    ACQUIRE_SOURCE = "acquire_source"
    MEDIA_METADATA = "media_metadata"
    TRANSCRIPT = "transcript"
    RESOURCES = "resources"
    FRAME_SELECTION = "frame_selection"
    OCR = "ocr"
    VISUAL_ANALYSIS = "visual_analysis"
    SPATIAL_REGIONS = "spatial_regions"
    INDEXING = "indexing"
    QUALITY_GATES = "quality_gates"
    PUBLISH = "publish"


CLAIMABLE = frozenset({Status.QUEUED, Status.RETRY_SCHEDULED})
TERMINAL = frozenset({Status.READY, Status.FAILED, Status.CANCELLED})
