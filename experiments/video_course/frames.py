"""Deterministic candidate-frame extraction and cheap OCR baseline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import math
from pathlib import Path
import re
import subprocess
from typing import Iterable

import cv2
import numpy as np

from .models import Chapter, FrameCandidate


WORD = re.compile(r"[a-z0-9_+.-]+", re.IGNORECASE)


def extract_candidates(
    video_path: Path,
    output_dir: Path,
    *,
    chapters: list[Chapter],
    sample_seconds: float = 2.0,
    change_threshold: float = 0.115,
    safeguard_seconds: float = 30.0,
    maximum_per_hour: int = 260,
) -> list[FrameCandidate]:
    frames_dir = output_dir / "frames"
    previews_dir = output_dir / "previews"
    frames_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"could not open {video_path}")
    duration_ms = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, capture.get(cv2.CAP_PROP_FPS)) * 1000)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if duration_ms <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("video metadata is incomplete")

    candidates: list[FrameCandidate] = []
    previous_small: np.ndarray | None = None
    previous_hash: int | None = None
    last_kept_ms = -int(safeguard_seconds * 1000)
    previous_chapter: int | None = None
    step_ms = max(500, int(sample_seconds * 1000))
    try:
        for timestamp_ms in range(0, duration_ms, step_ms):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_ms)
            ok, frame = capture.read()
            if not ok:
                continue
            small = cv2.resize(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                (320, 180),
                interpolation=cv2.INTER_AREA,
            )
            difference = (
                1.0
                if previous_small is None
                else float(np.mean(cv2.absdiff(previous_small, small))) / 255.0
            )
            perceptual = difference_hash(small)
            chapter_index = chapter_at(chapters, timestamp_ms)
            reasons: list[str] = []
            if previous_small is None:
                reasons.append("first_frame")
            if chapter_index != previous_chapter:
                reasons.append("chapter_boundary")
            if difference >= change_threshold:
                reasons.append("visual_change")
            if timestamp_ms - last_kept_ms >= int(safeguard_seconds * 1000):
                reasons.append("periodic_safeguard")
            if reasons:
                # A moving cursor or compression noise can trigger a candidate
                # even when the rendered content is unchanged. Exact/near-exact
                # dHash equality suppresses that before any disk or OCR cost.
                if (
                    previous_hash is not None
                    and hamming_distance(previous_hash, perceptual) <= 2
                    and reasons == ["visual_change"]
                ):
                    previous_small = small
                    previous_chapter = chapter_index
                    continue
                candidate = _persist_frame(
                    frame,
                    frames_dir=frames_dir,
                    previews_dir=previews_dir,
                    timestamp_ms=timestamp_ms,
                    difference=difference,
                    reasons=tuple(reasons),
                    chapter_index=chapter_index,
                )
                candidates.append(candidate)
                last_kept_ms = timestamp_ms
                previous_hash = perceptual
            previous_small = small
            previous_chapter = chapter_index
    finally:
        capture.release()

    candidates = add_ocr(candidates)
    candidates = deduplicate(candidates, maximum_per_hour=maximum_per_hour, duration_ms=duration_ms)
    return candidates


def _persist_frame(
    frame: np.ndarray,
    *,
    frames_dir: Path,
    previews_dir: Path,
    timestamp_ms: int,
    difference: float,
    reasons: tuple[str, ...],
    chapter_index: int | None,
) -> FrameCandidate:
    identifier = f"frame-{timestamp_ms:09d}"
    frame_path = frames_dir / f"{identifier}.jpg"
    preview_path = previews_dir / f"{identifier}.jpg"
    if not frame_path.exists():
        cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
    preview_width = min(960, frame.shape[1])
    preview_height = round(frame.shape[0] * preview_width / frame.shape[1])
    if not preview_path.exists():
        preview = cv2.resize(
            frame, (preview_width, preview_height), interpolation=cv2.INTER_AREA
        )
        cv2.imwrite(str(preview_path), preview, [cv2.IMWRITE_JPEG_QUALITY, 84])
    payload = frame_path.read_bytes()
    return FrameCandidate(
        id=identifier,
        timestamp_ms=timestamp_ms,
        path=str(frame_path),
        preview_path=str(preview_path),
        width=int(frame.shape[1]),
        height=int(frame.shape[0]),
        content_hash=sha256(payload).hexdigest(),
        difference_score=round(difference, 6),
        selection_reasons=reasons,
        chapter_index=chapter_index,
    )


def add_ocr(
    candidates: list[FrameCandidate],
    *,
    workers: int = 4,
) -> list[FrameCandidate]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        readings = list(pool.map(_ocr, candidates))
    return [
        replace(candidate, ocr_text=text, ocr_confidence=confidence)
        for candidate, (text, confidence) in zip(candidates, readings, strict=True)
    ]


def _ocr(candidate: FrameCandidate) -> tuple[str, float]:
    completed = subprocess.run(
        ["tesseract", candidate.preview_path, "stdout", "-l", "eng", "--psm", "11", "tsv"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return "", 0.0
    words: list[str] = []
    confidences: list[float] = []
    for line in completed.stdout.splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) < 12:
            continue
        text = columns[11].strip()
        try:
            confidence = float(columns[10])
        except ValueError:
            confidence = -1
        if text and confidence >= 0:
            words.append(text)
            confidences.append(confidence)
    mean = sum(confidences) / len(confidences) if confidences else 0.0
    return " ".join(words), round(mean / 100.0, 4)


def deduplicate(
    candidates: list[FrameCandidate],
    *,
    maximum_per_hour: int,
    duration_ms: int,
) -> list[FrameCandidate]:
    if not candidates:
        return []
    retained: list[FrameCandidate] = []
    hashes: list[int] = []
    for candidate in candidates:
        image = cv2.imread(candidate.preview_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        current_hash = difference_hash(image)
        chapter_boundary = "chapter_boundary" in candidate.selection_reasons
        if retained and not chapter_boundary:
            previous = retained[-1]
            text_similarity = jaccard(previous.ocr_text, candidate.ocr_text)
            hash_distance = hamming_distance(hashes[-1], current_hash)
            # Keep a periodic state at least once per minute even if the slide
            # has not changed. It is the evidence that a long interval really
            # remained stable, not an ingestion gap.
            if (
                candidate.timestamp_ms - previous.timestamp_ms < 60_000
                and hash_distance <= 8
                and text_similarity >= 0.90
            ):
                _unlink_candidate(candidate)
                continue
        retained.append(candidate)
        hashes.append(current_hash)

    maximum = max(1, math.ceil(duration_ms / 3_600_000 * maximum_per_hour))
    if len(retained) <= maximum:
        return retained
    mandatory = {
        index
        for index, candidate in enumerate(retained)
        if "chapter_boundary" in candidate.selection_reasons
    }
    ranked = sorted(
        range(len(retained)),
        key=lambda index: (
            index not in mandatory,
            retained[index].difference_score,
            retained[index].ocr_confidence,
        ),
        reverse=True,
    )
    selected = set(ranked[:maximum]) | mandatory
    result: list[FrameCandidate] = []
    for index, candidate in enumerate(retained):
        if index in selected:
            result.append(candidate)
        else:
            _unlink_candidate(candidate)
    return result


def _unlink_candidate(candidate: FrameCandidate) -> None:
    Path(candidate.path).unlink(missing_ok=True)
    Path(candidate.preview_path).unlink(missing_ok=True)


def difference_hash(gray: np.ndarray) -> int:
    reduced = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = reduced[:, 1:] > reduced[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def jaccard(left: str, right: str) -> float:
    left_words = set(WORD.findall(left.lower()))
    right_words = set(WORD.findall(right.lower()))
    if not left_words and not right_words:
        return 1.0
    union = left_words | right_words
    return len(left_words & right_words) / len(union) if union else 0.0


def chapter_at(chapters: Iterable[Chapter], timestamp_ms: int) -> int | None:
    for chapter in chapters:
        if chapter.start_ms <= timestamp_ms < chapter.end_ms:
            return chapter.index
    return None

