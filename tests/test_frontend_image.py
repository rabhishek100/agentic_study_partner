"""What the deployed frontend image must contain, and cannot get for free.

The Python image has `test_docker_image_contents.py` for the same class of
bug: something that works on a laptop and is silently missing on the platform.
This is the frontend's version of it.
"""

import subprocess
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DOCKERFILE = REPOSITORY_ROOT / "frontend" / "Dockerfile"
PDF_WORKER = Path("frontend/public/pdf.worker.min.mjs")


def is_gitignored(path: Path) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
        ).returncode
        == 0
    )


class PdfWorkerReachesTheImageTests(unittest.TestCase):
    """A viewer that 404s its own worker renders no PDF at all.

    That shipped: `/pdf.worker.min.mjs` returned 404 in production while every
    other page worked, because three reasonable decisions combined into one
    unreasonable outcome — the worker is gitignored so it cannot drift from
    pdfjs-dist, `railway up` filters the upload through .gitignore, and the
    build stage copies only `node_modules` out of the stage whose postinstall
    had written the file.
    """

    def test_the_worker_is_gitignored_so_it_cannot_come_from_the_context(self) -> None:
        """The premise the rest of this rests on. If it stops being true, the
        Dockerfile requirement below can be relaxed — but check, don't guess."""

        self.assertTrue(
            is_gitignored(PDF_WORKER),
            f"{PDF_WORKER} is no longer gitignored; revisit the build step "
            "that regenerates it",
        )

    def test_the_build_stage_regenerates_the_worker_after_copying_source(self) -> None:
        lines = [
            line.strip()
            for line in FRONTEND_DOCKERFILE.read_text().splitlines()
            if line.strip()
        ]
        copy_source = next(
            index for index, line in enumerate(lines) if line == "COPY . ."
        )
        generates = [
            index
            for index, line in enumerate(lines)
            if line.startswith("RUN ") and "copy-pdf-worker" in line
        ]

        self.assertTrue(
            generates,
            "frontend/Dockerfile never runs copy-pdf-worker.mjs, so the "
            "deployed image has no pdf.js worker and the viewer 404s it",
        )
        # After `COPY . .`, or the context's worker-less public/ overwrites it.
        self.assertTrue(
            any(index > copy_source for index in generates),
            "copy-pdf-worker.mjs runs before `COPY . .`, which then replaces "
            "public/ with the context's copy and undoes it",
        )

    def test_a_missing_worker_fails_the_build_rather_than_shipping(self) -> None:
        """The postinstall hook only warns, which is right for an install on a
        laptop and wrong for the image that gets deployed."""

        text = FRONTEND_DOCKERFILE.read_text()
        self.assertIn(
            "test -f public/pdf.worker.min.mjs",
            text,
            "nothing asserts the worker exists, so a failed copy would ship a "
            "viewer that cannot render any PDF",
        )


if __name__ == "__main__":
    unittest.main()
