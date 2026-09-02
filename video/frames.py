"""Deterministic frame candidates and inexpensive OCR for video ingestion.

Frame selection is intentionally independent from OCR.  Selecting candidates
does no model or subprocess work, so the derived frame set can be rebuilt and
inspected before any OCR or VLM cost is incurred.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Protocol

import cv2
import numpy as np

from video.acquisition import Chapter


DEFAULT_SAMPLE_SECONDS = 2.0
DEFAULT_CHANGE_THRESHOLD = 0.115
DEFAULT_SAFEGUARD_SECONDS = 30.0
DEFAULT_MAXIMUM_PER_HOUR = 260
MAXIMUM_CAPPED_TIMELINE_GAP_MS = 240_000
DEFAULT_OCR_TIMEOUT_SECONDS = 60
MAXIMUM_TSV_CHARACTERS = 10 * 1024 * 1024
LANGUAGE = re.compile(r"^[A-Za-z0-9_+.-]{1,64}$")


class FrameExtractionError(RuntimeError):
    """The source video or a derived JPEG could not be read safely."""


class OcrError(RuntimeError):
    """Tesseract could not produce a trustworthy OCR result."""


class Capture(Protocol):
    def isOpened(self) -> bool: ...

    def get(self, property_id: int) -> float: ...

    def set(self, property_id: int, value: float) -> bool: ...

    def read(self) -> tuple[bool, np.ndarray]: ...

    def release(self) -> None: ...


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


CaptureFactory = Callable[[str], Capture]
CommandRunner = Callable[..., CommandResult]


@dataclass(frozen=True)
class FrameCandidate:
    """A selected frame whose paths point only into the caller's staging root."""

    frame_index: int
    timestamp_ms: int
    selection_reasons: tuple[str, ...]
    full_path: Path
    preview_path: Path
    full_content_hash: str
    preview_content_hash: str
    perceptual_hash: str
    width: int
    height: int
    difference_score: float
    chapter_index: int | None


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float
    word_count: int


def select_frames(
    video_path: Path,
    staging_dir: Path,
    *,
    chapters: Iterable[Chapter] = (),
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    safeguard_seconds: float = DEFAULT_SAFEGUARD_SECONDS,
    maximum_per_hour: int = DEFAULT_MAXIMUM_PER_HOUR,
    capture_factory: CaptureFactory = cv2.VideoCapture,
) -> tuple[FrameCandidate, ...]:
    """Select chronological visual-change and safeguard candidates.

    Full-resolution and bandwidth-friendly preview JPEGs are written below a
    job-specific staging directory.  The result contains no source URL,
    storage key, or path supplied by video metadata.
    """

    _validate_selection_options(
        sample_seconds=sample_seconds,
        change_threshold=change_threshold,
        safeguard_seconds=safeguard_seconds,
        maximum_per_hour=maximum_per_hour,
    )
    source = Path(video_path)
    if source.is_symlink() or not source.is_file():
        raise FrameExtractionError("frame source must be a regular video file")
    root = _prepare_staging_root(Path(staging_dir))
    frames_dir = _prepare_child(root, "frames")
    previews_dir = _prepare_child(root, "previews")
    ordered_chapters = _validate_chapters(tuple(chapters))

    capture = capture_factory(str(source))
    if not capture.isOpened():
        capture.release()
        raise FrameExtractionError("could not open frame source")
    try:
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = capture.get(cv2.CAP_PROP_FPS)
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if not all(math.isfinite(value) for value in (frame_count, fps, width, height)):
            raise FrameExtractionError("video metadata is incomplete")
        if frame_count <= 0 or fps <= 0 or width <= 0 or height <= 0:
            raise FrameExtractionError("video metadata is incomplete")
        duration_ms = max(1, round(frame_count / fps * 1000))
        step_ms = max(500, round(sample_seconds * 1000))
        safeguard_ms = round(safeguard_seconds * 1000)

        candidates: list[FrameCandidate] = []
        previous_small: np.ndarray | None = None
        previous_kept_hash: int | None = None
        previous_chapter: int | None = None
        last_kept_ms = -safeguard_ms
        for timestamp_ms in range(0, duration_ms, step_ms):
            capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
            ok, frame = capture.read()
            if not ok:
                continue
            if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.size == 0:
                raise FrameExtractionError("video decoder returned an invalid frame")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
            difference = (
                1.0
                if previous_small is None
                else float(np.mean(cv2.absdiff(previous_small, small))) / 255.0
            )
            perceptual = difference_hash(small)
            chapter_index = chapter_at(ordered_chapters, timestamp_ms)
            reasons: list[str] = []
            if previous_small is None:
                reasons.append("first_frame")
            if chapter_index != previous_chapter:
                reasons.append("chapter_boundary")
            if difference >= change_threshold:
                reasons.append("visual_change")
            if timestamp_ms - last_kept_ms >= safeguard_ms:
                reasons.append("periodic_safeguard")

            if reasons:
                # Cursor movement and codec noise can cross the pixel threshold
                # while leaving rendered content unchanged.
                if (
                    previous_kept_hash is not None
                    and hamming_distance(previous_kept_hash, perceptual) <= 2
                    and reasons == ["visual_change"]
                ):
                    previous_small = small
                    previous_chapter = chapter_index
                    continue
                candidates.append(
                    _write_candidate(
                        frame,
                        frames_dir=frames_dir,
                        previews_dir=previews_dir,
                        frame_index=len(candidates),
                        timestamp_ms=timestamp_ms,
                        reasons=tuple(reasons),
                        difference=difference,
                        chapter_index=chapter_index,
                        perceptual_hash=perceptual,
                    )
                )
                last_kept_ms = timestamp_ms
                previous_kept_hash = perceptual
            previous_small = small
            previous_chapter = chapter_index
    finally:
        capture.release()

    return _apply_hourly_limit(
        candidates,
        duration_ms=duration_ms,
        maximum_per_hour=maximum_per_hour,
    )


def run_tesseract(
    image_path: Path,
    *,
    runner: CommandRunner | None = None,
    language: str = "eng",
    timeout_seconds: int | None = None,
) -> OcrResult:
    """Run Tesseract's sparse-text mode and parse its word-level TSV."""

    image = Path(image_path)
    if image.is_symlink() or not image.is_file():
        raise OcrError("OCR source must be a regular image file")
    if not LANGUAGE.fullmatch(language):
        raise ValueError("invalid OCR language")
    selected_timeout = (
        _configured_timeout() if timeout_seconds is None else timeout_seconds
    )
    if selected_timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    binary = os.getenv("VIDEO_TESSERACT_BINARY", "tesseract").strip() or "tesseract"
    argv = (
        binary,
        str(image),
        "stdout",
        "-l",
        language,
        "--psm",
        "11",
        "tsv",
    )
    command_runner = runner or run_command
    try:
        completed = command_runner(argv, timeout_seconds=selected_timeout)
    except subprocess.TimeoutExpired as error:
        raise OcrError("tesseract timed out") from error
    except OSError as error:
        raise OcrError("tesseract could not be started") from error
    if completed.returncode != 0:
        # Stderr can include local paths.  Do not place it in a persisted error.
        raise OcrError("tesseract failed")
    if not isinstance(completed.stdout, str):
        raise OcrError("tesseract returned invalid TSV")
    return parse_tesseract_tsv(completed.stdout)


def run_command(
    argv: Sequence[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=timeout_seconds,
    )


def parse_tesseract_tsv(tsv: str) -> OcrResult:
    """Return word text and mean confidence from Tesseract TSV output."""

    if not isinstance(tsv, str) or len(tsv) > MAXIMUM_TSV_CHARACTERS:
        raise OcrError("tesseract returned invalid TSV")
    lines = tsv.splitlines()
    if not lines:
        raise OcrError("tesseract returned invalid TSV")
    header = lines[0].lstrip("\ufeff").split("\t")
    if len(header) < 12 or header[10:12] != ["conf", "text"]:
        raise OcrError("tesseract returned invalid TSV")

    words: list[str] = []
    confidences: list[float] = []
    for line in lines[1:]:
        columns = line.split("\t", 11)
        if len(columns) != 12:
            continue
        text = " ".join(columns[11].split())
        if not text:
            continue
        try:
            confidence = float(columns[10])
        except ValueError:
            continue
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            continue
        words.append(text)
        confidences.append(confidence)
    if not words:
        return OcrResult(text="", confidence=0.0, word_count=0)
    return OcrResult(
        text=" ".join(words),
        confidence=round(sum(confidences) / len(confidences) / 100.0, 4),
        word_count=len(words),
    )


def difference_hash(gray: np.ndarray) -> int:
    if not isinstance(gray, np.ndarray) or gray.size == 0:
        raise ValueError("difference hash requires a non-empty image")
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    if gray.ndim != 2:
        raise ValueError("difference hash requires a grayscale or BGR image")
    reduced = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = reduced[:, 1:] > reduced[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(left: int, right: int) -> int:
    if left < 0 or right < 0:
        raise ValueError("perceptual hashes must be non-negative")
    return (left ^ right).bit_count()


def chapter_at(chapters: Iterable[Chapter], timestamp_ms: int) -> int | None:
    for chapter in chapters:
        if chapter.start_ms <= timestamp_ms < chapter.end_ms:
            return chapter.index
    return None


def _write_candidate(
    frame: np.ndarray,
    *,
    frames_dir: Path,
    previews_dir: Path,
    frame_index: int,
    timestamp_ms: int,
    reasons: tuple[str, ...],
    difference: float,
    chapter_index: int | None,
    perceptual_hash: int,
) -> FrameCandidate:
    identifier = f"frame-{timestamp_ms:012d}"
    full_path = frames_dir / f"{identifier}.jpg"
    preview_path = previews_dir / f"{identifier}.jpg"
    if not cv2.imwrite(str(full_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 94]):
        raise FrameExtractionError("could not write full frame JPEG")
    preview_width = min(960, int(frame.shape[1]))
    preview_height = max(1, round(frame.shape[0] * preview_width / frame.shape[1]))
    preview = cv2.resize(
        frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA
    )
    if not cv2.imwrite(str(preview_path), preview, [cv2.IMWRITE_JPEG_QUALITY, 84]):
        full_path.unlink(missing_ok=True)
        raise FrameExtractionError("could not write preview frame JPEG")
    full_bytes = full_path.read_bytes()
    preview_bytes = preview_path.read_bytes()
    return FrameCandidate(
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        selection_reasons=reasons,
        full_path=full_path,
        preview_path=preview_path,
        full_content_hash=sha256(full_bytes).hexdigest(),
        preview_content_hash=sha256(preview_bytes).hexdigest(),
        perceptual_hash=f"{perceptual_hash:016x}",
        width=int(frame.shape[1]),
        height=int(frame.shape[0]),
        difference_score=round(difference, 6),
        chapter_index=chapter_index,
    )


def _apply_hourly_limit(
    candidates: list[FrameCandidate], *, duration_ms: int, maximum_per_hour: int
) -> tuple[FrameCandidate, ...]:
    del duration_ms  # Candidates are capped independently in each timeline hour.
    selected: set[int] = set()
    by_hour: dict[int, list[int]] = {}
    for index, candidate in enumerate(candidates):
        by_hour.setdefault(candidate.timestamp_ms // 3_600_000, []).append(index)
    for indexes in by_hour.values():
        capacity = min(maximum_per_hour, len(indexes))
        if capacity == 0:
            continue

        # The cap must preserve the two user-visible coverage promises before
        # it optimizes for visually interesting frames: every chapter has an
        # anchor, and no quiet timeline stretch disappears. Starting with
        # chapter boundaries lets those frames do double duty as timeline
        # anchors instead of reserving a separate evenly-spaced set that can
        # crowd short chapters out of a small budget.
        anchors: set[int] = {
            index
            for index in indexes
            if "chapter_boundary" in candidates[index].selection_reasons
        }
        anchors.update({indexes[0], indexes[-1]})
        if len(anchors) > capacity:
            # A caller can configure a cap smaller than the number of chapters
            # in one hour. Keep the cap hard and deterministic; the quality
            # gate will make the uncovered chapters explicit.
            chapter_anchors = sorted(
                anchors,
                key=lambda index: (
                    index not in {indexes[0], indexes[-1]},
                    candidates[index].timestamp_ms,
                ),
            )
            anchors = set(chapter_anchors[:capacity])

        while len(anchors) < capacity:
            ordered_anchors = sorted(
                anchors, key=lambda index: candidates[index].timestamp_ms
            )
            gaps = [
                (
                    candidates[right].timestamp_ms
                    - candidates[left].timestamp_ms,
                    left,
                    right,
                )
                for left, right in zip(
                    ordered_anchors, ordered_anchors[1:], strict=False
                )
            ]
            largest_gap, left, right = max(gaps, default=(0, 0, 0))
            if largest_gap <= MAXIMUM_CAPPED_TIMELINE_GAP_MS:
                break
            available = [
                index
                for index in indexes
                if index not in anchors
                and candidates[left].timestamp_ms
                < candidates[index].timestamp_ms
                < candidates[right].timestamp_ms
            ]
            if not available:
                break
            midpoint_ms = (
                candidates[left].timestamp_ms + candidates[right].timestamp_ms
            ) // 2
            anchors.add(
                min(
                    available,
                    key=lambda index: (
                        abs(candidates[index].timestamp_ms - midpoint_ms),
                        candidates[index].timestamp_ms,
                    ),
                )
            )
        selected.update(anchors)

        ranked = sorted(
            (index for index in indexes if index not in anchors),
            key=lambda index: (
                "chapter_boundary" in candidates[index].selection_reasons,
                "first_frame" in candidates[index].selection_reasons,
                candidates[index].difference_score,
                -candidates[index].timestamp_ms,
            ),
            reverse=True,
        )
        selected.update(ranked[: capacity - len(anchors)])
    retained: list[FrameCandidate] = []
    for index, candidate in enumerate(candidates):
        if index in selected:
            retained.append(
                FrameCandidate(
                    frame_index=len(retained),
                    timestamp_ms=candidate.timestamp_ms,
                    selection_reasons=candidate.selection_reasons,
                    full_path=candidate.full_path,
                    preview_path=candidate.preview_path,
                    full_content_hash=candidate.full_content_hash,
                    preview_content_hash=candidate.preview_content_hash,
                    perceptual_hash=candidate.perceptual_hash,
                    width=candidate.width,
                    height=candidate.height,
                    difference_score=candidate.difference_score,
                    chapter_index=candidate.chapter_index,
                )
            )
        else:
            candidate.full_path.unlink(missing_ok=True)
            candidate.preview_path.unlink(missing_ok=True)
    return tuple(retained)


def _prepare_staging_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise FrameExtractionError("frame staging root must be a real directory")
    return root.resolve()


def _prepare_child(root: Path, name: str) -> Path:
    child = root / name
    child.mkdir(mode=0o700, exist_ok=True)
    if child.is_symlink() or not child.is_dir():
        raise FrameExtractionError("frame staging path must be a real directory")
    return child.resolve()


def _validate_selection_options(
    *,
    sample_seconds: float,
    change_threshold: float,
    safeguard_seconds: float,
    maximum_per_hour: int,
) -> None:
    if not math.isfinite(sample_seconds) or sample_seconds <= 0:
        raise ValueError("sample_seconds must be positive")
    if not math.isfinite(change_threshold) or not 0 <= change_threshold <= 1:
        raise ValueError("change_threshold must be between zero and one")
    if not math.isfinite(safeguard_seconds) or safeguard_seconds <= 0:
        raise ValueError("safeguard_seconds must be positive")
    if isinstance(maximum_per_hour, bool) or maximum_per_hour <= 0:
        raise ValueError("maximum_per_hour must be positive")


def _validate_chapters(chapters: tuple[Chapter, ...]) -> tuple[Chapter, ...]:
    previous_end = 0
    for expected_index, chapter in enumerate(chapters):
        if (
            chapter.index != expected_index
            or chapter.start_ms < previous_end
            or chapter.end_ms <= chapter.start_ms
        ):
            raise ValueError("chapters must be ordered valid intervals")
        previous_end = chapter.end_ms
    return chapters


def _configured_timeout() -> int:
    raw = os.getenv(
        "VIDEO_TESSERACT_TIMEOUT_SECONDS", str(DEFAULT_OCR_TIMEOUT_SECONDS)
    )
    try:
        timeout = int(raw)
    except ValueError as error:
        raise OcrError("invalid Tesseract timeout configuration") from error
    if timeout <= 0:
        raise OcrError("invalid Tesseract timeout configuration")
    return timeout
