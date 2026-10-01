"""Every package the deployed entrypoints import must be in the image.

The Dockerfile copies packages one line at a time, which means adding a new
top-level package is silently fine locally and fails at import time on the
deployed service. That is exactly what happened when `decks` shipped: the API
container crash-looped on `ModuleNotFoundError: No module named 'decks'` while
every test on the laptop passed.

This test closes that gap by asking the entrypoints themselves what they import
rather than maintaining a second list that can drift from the first.
"""

import ast
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPOSITORY_ROOT / "Dockerfile"

# What the image is actually started with: the API, the worker, and the
# combined supervisor that runs both.
ENTRYPOINTS = (
    Path("api/main.py"),
    Path("worker/main.py"),
    Path("scripts/serve.py"),
)


def copied_paths() -> set[str]:
    """The first path segment of every COPY target in the Dockerfile."""

    copied: set[str] = set()
    for line in DOCKERFILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        parts = stripped.split()[1:]
        # Drop flags such as `--from=uv`, and the destination argument.
        sources = [part for part in parts if not part.startswith("--")][:-1]
        for source in sources:
            copied.add(source.strip("./").split("/")[0].removesuffix(".py"))
    return copied


def local_packages() -> set[str]:
    """Top-level packages and Python modules importable by runtime code."""

    return {
        entry.name
        for entry in REPOSITORY_ROOT.iterdir()
        if entry.is_dir() and (entry / "__init__.py").exists()
    } | {entry.stem for entry in REPOSITORY_ROOT.glob("*.py")}


def imported_packages(source: Path) -> set[str]:
    """Top-level project packages one module imports, transitively.

    Walking the import graph rather than only the entrypoint's own imports:
    `worker.main` reaching `decks.worker` is a direct import, but the next
    missing package is as likely to be two hops down.
    """

    packages = local_packages()
    seen: set[Path] = set()
    found: set[str] = set()
    queue = [source]

    while queue:
        current = queue.pop()
        resolved = (REPOSITORY_ROOT / current).resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)

        tree = ast.parse(resolved.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports stay inside a package already accounted for.
                names = [node.module] if node.level == 0 and node.module else []
            else:
                continue

            for name in names:
                root = name.split(".")[0]
                if root not in packages:
                    continue
                found.add(root)
                module = REPOSITORY_ROOT / Path(*name.split(".")).with_suffix(".py")
                package = REPOSITORY_ROOT / Path(*name.split(".")) / "__init__.py"
                queue.append(module if module.exists() else package)

    return found


class DockerImageContentsTests(unittest.TestCase):
    def test_every_imported_package_is_copied_into_the_image(self) -> None:
        copied = copied_paths()
        for entrypoint in ENTRYPOINTS:
            for package in sorted(imported_packages(entrypoint)):
                with self.subTest(entrypoint=str(entrypoint), package=package):
                    self.assertIn(
                        package,
                        copied,
                        f"{entrypoint} imports `{package}`, which the Dockerfile "
                        "never copies. The container will crash on import.",
                    )

    def test_the_entrypoints_this_guards_still_exist(self) -> None:
        """A renamed entrypoint would make this test pass by checking nothing."""

        for entrypoint in ENTRYPOINTS:
            self.assertTrue((REPOSITORY_ROOT / entrypoint).exists(), entrypoint)

    def test_the_decks_package_is_reachable_from_the_worker(self) -> None:
        """The specific regression: the worker polls the deck queue."""

        self.assertIn("decks", imported_packages(Path("worker/main.py")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
