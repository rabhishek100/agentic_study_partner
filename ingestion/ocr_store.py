"""Durable page checkpoints for the OCR stage.

The OCR stage is the first one that spends real money per unit of work, so a
completed page is committed the moment it returns instead of being held until
the stage ends. A worker that dies at page 700 resumes at page 701.

Reuse is conditioned on the *reading*, not on the page number. A retry running
under a different model or a different instruction is producing different text,
and quietly mixing the two would leave a book transcribed by two engines
carrying one engine's provenance.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from storage.database import parse_owner_id

from .ocr import FabricationAssessment, PageTranscription


__all__ = [
    "CheckpointedPage",
    "OcrPageSummary",
    "clear_pages",
    "completed_pages",
    "page_summary",
    "record_page",
    "transcribed_text",
]


@dataclass(frozen=True)
class CheckpointedPage:
    """One page already transcribed under the current reading."""

    page: int
    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    fabrication_verdict: str


@dataclass(frozen=True)
class OcrPageSummary:
    """What a job's transcription cost and how much of it needs a second look."""

    pages: int = 0
    flagged: int = 0
    unassessable: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def provenance(self) -> dict[str, object]:
        return {
            "pages": self.pages,
            "flagged": self.flagged,
            "unassessable": self.unassessable,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


def record_page(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    transcription: PageTranscription,
    fabrication: FabricationAssessment,
) -> None:
    """Commit one transcribed page.

    Upsert rather than insert: a retry that re-reads a page under a changed
    model must replace the older reading, not collide with it.
    """

    connection.execute(
        """
        insert into ingestion_ocr_pages (
            job_id, owner_id, page, text, provider, model_id, render_dpi,
            prompt_hash, input_tokens, output_tokens, cost_usd,
            fabrication_verdict, fabrication_json
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (job_id, page) do update set
            text = excluded.text,
            provider = excluded.provider,
            model_id = excluded.model_id,
            render_dpi = excluded.render_dpi,
            prompt_hash = excluded.prompt_hash,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cost_usd = excluded.cost_usd,
            fabrication_verdict = excluded.fabrication_verdict,
            fabrication_json = excluded.fabrication_json,
            created_at = now()
        """,
        (
            UUID(str(job_id)),
            parse_owner_id(owner_id),
            transcription.page,
            transcription.text,
            transcription.provider,
            transcription.model_id,
            transcription.render_dpi,
            transcription.prompt_hash,
            transcription.input_tokens,
            transcription.output_tokens,
            transcription.cost_usd,
            fabrication.verdict,
            Jsonb(fabrication.provenance()),
        ),
    )


def completed_pages(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    model_id: str,
    prompt_hash: str,
) -> dict[int, CheckpointedPage]:
    """Return pages already read under this exact model and instruction.

    Pages read under anything else are ignored rather than deleted: they stay
    as evidence of what the previous attempt did, and the upsert in
    :func:`record_page` replaces them as the new reading reaches them.
    """

    rows = connection.execute(
        """
        select page, text, cost_usd, input_tokens, output_tokens,
               fabrication_verdict
        from ingestion_ocr_pages
        where job_id = %s and owner_id = %s
          and model_id = %s and prompt_hash = %s
        order by page
        """,
        (
            UUID(str(job_id)),
            parse_owner_id(owner_id),
            model_id,
            prompt_hash,
        ),
    ).fetchall()

    return {
        int(row["page"]): CheckpointedPage(
            page=int(row["page"]),
            text=row["text"],
            cost_usd=float(row["cost_usd"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            fabrication_verdict=row["fabrication_verdict"],
        )
        for row in rows
    }


def transcribed_text(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
) -> list[tuple[int, str]]:
    """Return every transcribed page of a job in reading order."""

    rows = connection.execute(
        """
        select page, text
        from ingestion_ocr_pages
        where job_id = %s and owner_id = %s
        order by page
        """,
        (UUID(str(job_id)), parse_owner_id(owner_id)),
    ).fetchall()
    return [(int(row["page"]), row["text"]) for row in rows]


def page_summary(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
) -> OcrPageSummary:
    """Aggregate one job's transcription for provenance and the review queue."""

    row = connection.execute(
        """
        select count(*) as pages,
               count(*) filter (where fabrication_verdict = 'flagged') as flagged,
               count(*) filter (where fabrication_verdict = 'unassessable')
                   as unassessable,
               coalesce(sum(input_tokens), 0) as input_tokens,
               coalesce(sum(output_tokens), 0) as output_tokens,
               coalesce(sum(cost_usd), 0) as cost_usd
        from ingestion_ocr_pages
        where job_id = %s and owner_id = %s
        """,
        (UUID(str(job_id)), parse_owner_id(owner_id)),
    ).fetchone()

    if row is None:
        return OcrPageSummary()
    return OcrPageSummary(
        pages=int(row["pages"]),
        flagged=int(row["flagged"]),
        unassessable=int(row["unassessable"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cost_usd=float(row["cost_usd"]),
    )


def clear_pages(
    connection: Connection,
    *,
    owner_id: str | UUID,
    job_id: str | UUID,
    pages: Iterable[int] | None = None,
) -> int:
    """Discard checkpoints, for a re-read of some or all of a job's pages."""

    if pages is None:
        result = connection.execute(
            "delete from ingestion_ocr_pages where job_id = %s and owner_id = %s",
            (UUID(str(job_id)), parse_owner_id(owner_id)),
        )
    else:
        wanted = [int(page) for page in pages]
        if not wanted:
            return 0
        result = connection.execute(
            """
            delete from ingestion_ocr_pages
            where job_id = %s and owner_id = %s and page = any(%s)
            """,
            (UUID(str(job_id)), parse_owner_id(owner_id), wanted),
        )
    return result.rowcount or 0
