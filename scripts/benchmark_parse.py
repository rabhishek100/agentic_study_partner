"""Measure whether parsing a book in parallel page batches is worth it.

Runs the same layout parser two ways over the same pages: once as a single
document, and once as page batches across a process pool. Every page goes
through ``hi_res`` in both, so this measures scheduling, not a quality
trade-off.

Meant to run on the machine that will do the work:

    railway ssh --service worker -- python -m scripts.benchmark_parse --pages 24

Without a local path it downloads the newest source object from the private
bucket, so the deployed worker can benchmark a real book.
"""

import argparse
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import fitz


def _partition(path: str):
    """Parse one PDF exactly as the pipeline does. Top level so it pickles.

    Calls the pipeline's own partition rather than restating its options: a
    benchmark that drifts from the settings it claims to measure is worse
    than no benchmark. The import stays deferred so the module keeps costing
    nothing until a parse actually runs.
    """

    from parsing.parser import _partition as partition

    return len(partition(Path(path), "hi_res"))


def slice_pdf(source: Path, first: int, last: int, destination: Path) -> Path:
    with fitz.open(source) as document:
        subset = fitz.open()
        subset.insert_pdf(document, from_page=first, to_page=last)
        subset.save(str(destination))
        subset.close()
    return destination


def newest_source(workspace: Path) -> Path:
    """Fetch the most recent uploaded book from the private bucket."""

    from ingestion.config import load_limits
    from ingestion.storage_objects import download_object, list_prefix

    bucket = load_limits().source_bucket
    for owner in list_prefix(bucket, limit=50):
        for job in list_prefix(bucket, owner, limit=50):
            path = f"{owner}/{job}/original.pdf"
            target = workspace / "source.pdf"
            download_object(bucket, path, target, maximum_bytes=200_000_000)
            print(f"using {path} ({target.stat().st_size / 1e6:.1f} MB)")
            return target
    raise SystemExit("no source objects in the bucket to benchmark")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="local PDF; defaults to Storage")
    parser.add_argument("--pages", type=int, default=24, help="pages to benchmark")
    parser.add_argument("--first", type=int, default=40, help="first page, 0-based")
    parser.add_argument(
        "--workers", type=int, nargs="*", default=[2, 4], help="pool sizes to try"
    )
    arguments = parser.parse_args()

    print(f"cpus visible: {os.cpu_count()}")

    with tempfile.TemporaryDirectory(prefix="parse-benchmark-") as directory:
        workspace = Path(directory)
        source = arguments.source or newest_source(workspace)

        with fitz.open(source) as document:
            available = document.page_count
        first = min(arguments.first, max(0, available - arguments.pages))
        last = min(first + arguments.pages - 1, available - 1)
        count = last - first + 1
        print(f"benchmarking pages {first + 1}-{last + 1} of {available}\n")

        whole = slice_pdf(source, first, last, workspace / "whole.pdf")
        start = time.time()
        elements = _partition(str(whole))
        serial = time.time() - start
        print(
            f"serial          {serial:7.1f}s  {serial / count:5.2f}s/page  "
            f"{elements} elements"
        )

        for workers in arguments.workers:
            if workers < 2 or workers > count:
                continue
            size = max(1, count // workers)
            batches = []
            for index in range(workers):
                batch_first = first + index * size
                batch_last = last if index == workers - 1 else batch_first + size - 1
                if batch_first > last:
                    break
                batches.append(
                    str(
                        slice_pdf(
                            source,
                            batch_first,
                            batch_last,
                            workspace / f"batch{index}.pdf",
                        )
                    )
                )

            start = time.time()
            with ProcessPoolExecutor(max_workers=workers) as pool:
                counts = list(pool.map(_partition, batches))
            parallel = time.time() - start
            print(
                f"parallel({workers})    {parallel:7.1f}s  {parallel / count:5.2f}s/page  "
                f"{sum(counts)} elements  ->  {serial / parallel:.2f}x"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
