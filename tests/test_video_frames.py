"""Frame extraction and OCR are deterministic and have no network dependency."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from video.acquisition import Chapter
from video.frames import (
    FrameExtractionError,
    OcrError,
    difference_hash,
    hamming_distance,
    parse_tesseract_tsv,
    run_command,
    run_tesseract,
    select_frames,
)


@dataclass
class Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeCapture:
    def __init__(self, frames: list[np.ndarray], *, fps: float = 0.5) -> None:
        self.frames = frames
        self.fps = fps
        self.position_ms = 0
        self.released = False

    def isOpened(self) -> bool:
        return True

    def get(self, property_id: int) -> float:
        if property_id == cv2.CAP_PROP_FRAME_COUNT:
            return float(len(self.frames))
        if property_id == cv2.CAP_PROP_FPS:
            return self.fps
        if property_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.frames[0].shape[1])
        if property_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.frames[0].shape[0])
        return 0.0

    def set(self, property_id: int, value: float) -> bool:
        if property_id == cv2.CAP_PROP_POS_MSEC:
            self.position_ms = round(value)
            return True
        return False

    def read(self) -> tuple[bool, np.ndarray]:
        index = round(self.position_ms / (1000 / self.fps))
        if index < 0 or index >= len(self.frames):
            return False, np.empty((0, 0, 3), dtype=np.uint8)
        return True, self.frames[index].copy()

    def release(self) -> None:
        self.released = True


class ClosedCapture(FakeCapture):
    def isOpened(self) -> bool:
        return False


def _tsv(*rows: tuple[str, str]) -> str:
    header = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext"
    )
    body = [
        f"5\t1\t1\t1\t1\t{index}\t0\t0\t10\t10\t{confidence}\t{text}"
        for index, (confidence, text) in enumerate(rows, 1)
    ]
    return "\n".join([header, *body])


class VideoFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.video = self.root / "source.mp4"
        self.video.write_bytes(b"decoder input is injected")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selection_is_chronological_and_writes_full_and_preview_jpegs(self) -> None:
        black = np.zeros((1080, 1200, 3), dtype=np.uint8)
        diagram = black.copy()
        diagram[:, 600:] = 255
        frames = [black, black, diagram, diagram, diagram]
        capture = FakeCapture(frames)

        candidates = select_frames(
            self.video,
            self.root / "staging",
            chapters=(
                Chapter(0, "Intro", 0, 6_000),
                Chapter(1, "Diagram", 6_000, 10_000),
            ),
            capture_factory=lambda _: capture,
        )

        self.assertEqual([item.timestamp_ms for item in candidates], [0, 4_000, 6_000])
        self.assertEqual([item.frame_index for item in candidates], [0, 1, 2])
        self.assertIn("visual_change", candidates[1].selection_reasons)
        self.assertIn("chapter_boundary", candidates[2].selection_reasons)
        self.assertTrue(capture.released)
        for candidate in candidates:
            self.assertIsInstance(candidate.full_path, Path)
            self.assertIsInstance(candidate.preview_path, Path)
            self.assertEqual(
                candidate.full_path.parent, (self.root / "staging/frames").resolve()
            )
            self.assertEqual(
                candidate.preview_path.parent,
                (self.root / "staging/previews").resolve(),
            )
            self.assertTrue(candidate.full_path.is_file())
            self.assertTrue(candidate.preview_path.is_file())
            self.assertEqual(len(candidate.full_content_hash), 64)
            self.assertEqual(len(candidate.preview_content_hash), 64)
            self.assertRegex(candidate.perceptual_hash, r"^[0-9a-f]{16}$")
            preview = cv2.imread(str(candidate.preview_path))
            self.assertEqual(preview.shape[1], 960)

    def test_static_video_keeps_thirty_second_safeguards(self) -> None:
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        capture = FakeCapture([frame] * 36)

        candidates = select_frames(
            self.video,
            self.root / "static",
            capture_factory=lambda _: capture,
        )

        self.assertEqual(
            [item.timestamp_ms for item in candidates], [0, 30_000, 60_000]
        )
        self.assertIn("periodic_safeguard", candidates[-1].selection_reasons)

    def test_hourly_limit_keeps_ranked_candidates_in_time_order(self) -> None:
        frames: list[np.ndarray] = []
        for index in range(5):
            frame = np.zeros((90, 160, 3), dtype=np.uint8)
            frame[:, 20 + index * 25 :] = 255
            frames.append(frame)

        candidates = select_frames(
            self.video,
            self.root / "limited",
            change_threshold=0.01,
            maximum_per_hour=2,
            capture_factory=lambda _: FakeCapture(frames),
        )

        self.assertEqual(len(candidates), 2)
        timestamps = [item.timestamp_ms for item in candidates]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual([item.frame_index for item in candidates], [0, 1])
        self.assertEqual(len(list((self.root / "limited/frames").glob("*.jpg"))), 2)
        self.assertEqual(len(list((self.root / "limited/previews").glob("*.jpg"))), 2)

    def test_invalid_source_and_closed_capture_are_rejected(self) -> None:
        with self.assertRaisesRegex(FrameExtractionError, "regular video"):
            select_frames(self.root / "missing.mp4", self.root / "missing-output")

        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        capture = ClosedCapture([frame])
        with self.assertRaisesRegex(FrameExtractionError, "could not open"):
            select_frames(
                self.video,
                self.root / "closed",
                capture_factory=lambda _: capture,
            )
        self.assertTrue(capture.released)

    def test_difference_hash_is_a_deterministic_64_bit_signal(self) -> None:
        plain = np.zeros((32, 32), dtype=np.uint8)
        split = plain.copy()
        split[:, 16:] = 255

        plain_hash = difference_hash(plain)
        self.assertEqual(plain_hash, difference_hash(plain.copy()))
        self.assertGreater(hamming_distance(plain_hash, difference_hash(split)), 0)
        self.assertLessEqual(plain_hash.bit_length(), 64)

    def test_tesseract_uses_psm_11_and_parses_confidence(self) -> None:
        image = self.root / "preview.jpg"
        image.write_bytes(b"injected runner does not decode this")
        calls: list[tuple[tuple[str, ...], int]] = []

        def runner(argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
            calls.append((argv, timeout_seconds))
            return Result(
                stdout=_tsv(
                    ("80", "Self"), ("100", "attention"), ("-1", "ignored")
                )
            )

        with patch.dict("os.environ", {"VIDEO_TESSERACT_BINARY": "/opt/tesseract"}):
            result = run_tesseract(image, runner=runner, timeout_seconds=12)

        argv, timeout = calls[0]
        self.assertEqual(argv[0], "/opt/tesseract")
        self.assertEqual(argv[argv.index("--psm") + 1], "11")
        self.assertEqual(argv[-1], "tsv")
        self.assertEqual(timeout, 12)
        self.assertEqual(result.text, "Self attention")
        self.assertEqual(result.confidence, 0.9)
        self.assertEqual(result.word_count, 2)

    def test_tsv_parser_rejects_malformed_output_and_accepts_no_words(self) -> None:
        with self.assertRaisesRegex(OcrError, "invalid TSV"):
            parse_tesseract_tsv("not tsv")
        self.assertEqual(parse_tesseract_tsv(_tsv()).text, "")

    def test_tesseract_failures_do_not_expose_stderr(self) -> None:
        image = self.root / "preview.jpg"
        image.write_bytes(b"image")

        with self.assertRaisesRegex(OcrError, "tesseract failed") as raised:
            run_tesseract(
                image,
                runner=lambda argv, *, timeout_seconds: Result(
                    returncode=1, stderr="/private/path and signed data"
                ),
            )
        self.assertNotIn("private", str(raised.exception))

        def timeout(argv: tuple[str, ...], *, timeout_seconds: int) -> Result:
            raise subprocess.TimeoutExpired(argv, timeout_seconds)

        with self.assertRaisesRegex(OcrError, "timed out"):
            run_tesseract(image, runner=timeout)

    @patch("video.frames.subprocess.run")
    def test_command_runner_never_invokes_a_shell(self, subprocess_run) -> None:
        subprocess_run.return_value = Result()

        run_command(("tesseract", "unsafe; value"), timeout_seconds=7)

        subprocess_run.assert_called_once_with(
            ["tesseract", "unsafe; value"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=7,
        )


if __name__ == "__main__":
    unittest.main()
