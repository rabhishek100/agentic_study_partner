"""Compare transcription engines on a stratified sample of real pages.

    uv run python -m scripts.evaluate_transcription select --out gold.json
    uv run python -m scripts.evaluate_transcription transcribe --gold gold.json
    uv run python -m scripts.evaluate_transcription score --gold gold.json

`select` chooses pages by what they contain, using signals already stored from
ingestion, and writes a manifest. `transcribe` runs each engine over those
pages. `score` compares them against the adjudicated reference and prints the
table that belongs in the measurement write-up.

The reference is filled in between `transcribe` and `score`, by adjudication.
It is not authored by this script and is never called ground truth.
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

import fitz

from evals.transcription import (
    FORMULA,
    PATHOLOGICAL,
    PROSE,
    TABLE,
    categorize_page,
    score_page,
)
from ingestion.ocr import (
    DEFAULT_OCR_FALLBACK_MODEL,
    OpenRouterOcrProvider,
    TesseractOcrProvider,
    render_page,
)
from storage.database import (
    connection as database_connection,
    environment_owner_id,
    parse_owner_id,
)


logger = logging.getLogger("study_partner.scripts.evaluate_transcription")

# Even coverage of the four page kinds, per book. Weighted towards the ones
# that distinguish engines: prose is where they agree.
QUOTA = {PROSE: 3, TABLE: 3, FORMULA: 3, PATHOLOGICAL: 4}

# How a reference was produced decides what it can measure. Only one made from
# the page itself is independent of every engine being scored; one taken from a
# candidate makes that candidate unrankable and quietly flatters it.
INDEPENDENT_SOURCES = frozenset({"image"})


def _engine_label(model_id: str) -> str:
    """A short name for a model, taken from the model itself."""

    return model_id.split("/")[-1].split(":")[0]


def _books(connection, owner_id) -> list[dict]:
    """Books whose pages were transcribed, newest ingestion per book."""

    rows = connection.execute(
        """
        select b.id as book_id, b.title, j.id as job_id, b.page_count
        from books b
        join ingestion_jobs j on j.book_id = b.id
        where b.owner_id = %s and b.status = 'ready'
          and exists (select 1 from ingestion_ocr_pages p where p.job_id = j.id)
        order by b.id
        """,
        (owner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _select(connection, owner_id) -> list[dict]:
    chosen: list[dict] = []
    for book in _books(connection, owner_id):
        pages = connection.execute(
            """
            select page, text, fabrication_verdict
            from ingestion_ocr_pages where job_id = %s order by page
            """,
            (book["job_id"],),
        ).fetchall()
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in pages:
            category = categorize_page(
                row["text"],
                flagged=row["fabrication_verdict"] == "flagged",
                unassessable=row["fabrication_verdict"] == "unassessable",
            )
            buckets[category].append(dict(row))

        for category, quota in QUOTA.items():
            available = buckets.get(category, [])
            if not available:
                logger.warning(
                    "book %s has no %s pages", book["book_id"], category
                )
                continue
            # Spread the sample through the book rather than taking the first
            # few: a scan's quality drifts, and the front is not the back.
            step = max(1, len(available) // quota)
            for row in available[::step][:quota]:
                chosen.append(
                    {
                        "book_id": book["book_id"],
                        "title": book["title"],
                        "job_id": str(book["job_id"]),
                        "page": row["page"],
                        "category": category,
                        "ingested_text": row["text"],
                        "candidates": {},
                        "reference": None,
                        "reference_source": None,
                    }
                )
    return chosen


def _source_for(connection, owner_id, book_id: int) -> Path | None:
    """The local PDF a book was ingested from, when this machine still has it."""

    row = connection.execute(
        """
        select original_filename, provenance_json from ingestion_jobs
        where book_id = %s and owner_id = %s
        """,
        (book_id, owner_id),
    ).fetchone()
    if row is None:
        return None
    # A locally-ingested book records the file it was read from; one uploaded
    # through the browser records only the name it was uploaded under. Both
    # name the same file on the machine holding the library.
    local = (row["provenance_json"] or {}).get("local_source") or {}
    for name in (local.get("filename"), row["original_filename"]):
        if not name:
            continue
        candidate = Path.home() / "Downloads" / "unstructured_books_to_ingest" / name
        if candidate.is_file():
            return candidate
    return None


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["select", "transcribe", "score"])
    parser.add_argument("--gold", type=Path, default=Path("evaluation/ocr_gold.json"))
    parser.add_argument("--owner-id")
    parser.add_argument("--database-url")
    parser.add_argument(
        "--sources",
        type=Path,
        help="Directory holding the original PDFs, for re-rendering pages",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = build_argument_parser().parse_args(argv)
    owner_id = (
        parse_owner_id(arguments.owner_id)
        if arguments.owner_id
        else environment_owner_id()
    )

    if arguments.stage == "select":
        with database_connection(arguments.database_url, readonly=True) as connection:
            pages = _select(connection, owner_id)
        arguments.gold.parent.mkdir(parents=True, exist_ok=True)
        arguments.gold.write_text(json.dumps(pages, indent=1))
        counts: dict[str, int] = defaultdict(int)
        for entry in pages:
            counts[f"{entry['book_id']}/{entry['category']}"] += 1
        print(f"selected {len(pages)} pages -> {arguments.gold}")
        for key in sorted(counts):
            print(f"  {key}: {counts[key]}")
        return 0

    pages = json.loads(arguments.gold.read_text())

    if arguments.stage == "transcribe":
        primary = OpenRouterOcrProvider()
        fallback = OpenRouterOcrProvider(model_id=DEFAULT_OCR_FALLBACK_MODEL)
        reference_engine = TesseractOcrProvider()
        sources = arguments.sources
        by_book: dict[int, Path] = {}
        with database_connection(arguments.database_url, readonly=True) as connection:
            for entry in pages:
                book_id = entry["book_id"]
                if book_id in by_book:
                    continue
                found = _source_for(connection, owner_id, book_id)
                if found is None and sources is not None:
                    matches = list(sources.glob("*.pdf"))
                    found = matches[0] if len(matches) == 1 else None
                if found is not None:
                    by_book[book_id] = found

        cost = 0.0
        for index, entry in enumerate(pages, start=1):
            source = by_book.get(entry["book_id"])
            if source is None:
                logger.warning("no source PDF for book %s; skipped", entry["book_id"])
                continue
            with fitz.open(source) as document:
                image = render_page(document, entry["page"] - 1, 300)
            # Labelled by the model that actually ran, not by its position.
            # These were "gemini" and "qwen" by assumption, which would have
            # silently relabelled every result the day the primary changed.
            for name, engine in (
                (_engine_label(primary.model_id), primary),
                (_engine_label(fallback.model_id), fallback),
                ("tesseract", reference_engine),
            ):
                if name in entry["candidates"]:
                    continue
                try:
                    result = engine.transcribe(image, "image/png", entry["page"])
                except Exception as error:  # noqa: BLE001 - recorded, not fatal
                    logger.warning("%s failed on page %s: %s", name, entry["page"], error)
                    continue
                entry["candidates"][name] = result.text
                cost += result.cost_usd
            arguments.gold.write_text(json.dumps(pages, indent=1))
            print(f"  {index}/{len(pages)}  book {entry['book_id']} page {entry['page']}")
        print(f"transcribed; ${cost:.4f} spent")
        return 0

    missing = [entry for entry in pages if not entry.get("reference")]
    if missing:
        print(
            f"{len(missing)} of {len(pages)} pages have no reference yet; "
            "adjudicate them before scoring",
            file=sys.stderr,
        )
        return 2

    # A reference copied from a candidate scores that candidate against itself.
    # The first run of this evaluation did exactly that on 31 of 39 pages and
    # reported a character error rate of 0.0000 for the engine it had copied —
    # a number produced by construction, and one that reads like a measurement.
    # Only a reference made independently of every candidate can rank them.
    independent = [
        entry for entry in pages if entry.get("reference_source") in INDEPENDENT_SOURCES
    ]
    excluded = len(pages) - len(independent)
    if excluded:
        print(
            f"scoring {len(independent)} of {len(pages)} pages; {excluded} excluded "
            "because their reference came from a candidate",
        )
        print()
    if not independent:
        print(
            "no page has a reference independent of the candidates; nothing "
            "can be ranked",
            file=sys.stderr,
        )
        return 3
    pages = independent

    scores = [
        score_page(
            book_id=entry["book_id"],
            page=entry["page"],
            category=entry["category"],
            engine=engine,
            candidate=candidate,
            reference=entry["reference"],
        )
        for entry in pages
        for engine, candidate in entry["candidates"].items()
    ]

    engines = sorted({s.engine for s in scores})
    print(f"{'engine':<26}{'pages':>6}{'CER':>9}{'WER':>9}{'table F1':>10}{'fabricated':>12}")
    for engine in engines:
        rows = [s for s in scores if s.engine == engine and s.scored]
        if not rows:
            continue
        tables = [s.table_cell_f1 for s in rows if s.table_cell_f1 is not None]
        print(
            f"{engine:<26}{len(rows):>6}"
            f"{sum(s.character_error_rate for s in rows) / len(rows):>9.4f}"
            f"{sum(s.word_error_rate for s in rows) / len(rows):>9.4f}"
            f"{(sum(tables) / len(tables) if tables else float('nan')):>10.4f}"
            f"{sum(len(s.fabricated) for s in rows):>12}"
        )

    print()
    print(f"{'engine':<26}{'category':<16}{'pages':>6}{'CER':>9}")
    for engine in engines:
        for category in (PROSE, TABLE, FORMULA, PATHOLOGICAL):
            rows = [
                s
                for s in scores
                if s.engine == engine and s.category == category and s.scored
            ]
            if not rows:
                continue
            print(
                f"{engine:<26}{category:<16}{len(rows):>6}"
                f"{sum(s.character_error_rate for s in rows) / len(rows):>9.4f}"
            )

    fabrications = [(s, span) for s in scores for span in s.fabricated]
    if fabrications:
        print()
        print("spans present in a reading and absent from the reference:")
        for score, span in fabrications[:10]:
            print(f"  {score.engine:<10} book {score.book_id} p{score.page}: {span[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
