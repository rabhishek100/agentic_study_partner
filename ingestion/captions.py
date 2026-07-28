"""Describe canonical figures with a vision model, once, at ingest.

Captions are what let a figure be *used* rather than merely displayed: they
go into the chunk text, so a diagram competes in retrieval on its own merit,
and into the answer prompt, so the model can refer to what a figure shows
instead of the interface guessing where to put it.

Cost shape matters here. Captioning once at ingest costs roughly a thousand
input tokens per image, one time. Sending images with every question instead
would re-send the same bytes on every turn and force a vision-capable model
for all generation — measurably more expensive, for strictly less: a caption
is also alt text and also searchable, which an inline image is not.

Not everything is worth describing. Measured across the four production books
(810 figures), publisher boilerplate — the "Check for updates" badge and
similar — accounts for 78 of them, identifiable because the identical bytes
recur across pages and even across books. Another 21 are too small to be a
diagram. Those are recorded as skipped, with a reason, rather than captioned
or silently dropped.
"""

from base64 import b64decode
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
import logging
import os
from typing import Any, Protocol
from uuid import UUID

import httpx
from psycopg import Connection

from storage.database import parse_owner_id


logger = logging.getLogger("study_partner.ingestion.captions")

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
# Chosen from OpenRouter's vision-capable list on price/capability:
# $0.10/M input, which puts one full corpus pass in the tens of cents.
DEFAULT_CAPTION_MODEL = "google/gemini-2.5-flash-lite"

# Both thresholds come from `scripts/evaluate_figures.py` over the real corpus
# rather than from intuition; see evaluation/figure_selection_measurement.md.
# An image whose exact bytes recur this many times is furniture, not a figure.
BOILERPLATE_REPEAT_THRESHOLD = 3
# Below this, an image is a rule, a logo, or a rendered heading. The median
# real figure is ~84 KB.
MINIMUM_FIGURE_BYTES = 4_000

CAPTION_INSTRUCTION = (
    "You are describing a figure from a technical textbook so that it can be "
    "found by search and referred to in a written answer.\n\n"
    "Write 1-3 sentences of plain prose. State what the figure shows and the "
    "specific technical content a reader would cite it for: the variables or "
    "axes, the relationship or trend, the components and how they connect, or "
    "the concrete values that matter. Name the concepts using the words a "
    "textbook would use, because this text is what search will match against.\n\n"
    "Do not begin with 'This figure', 'The image shows', or similar. Do not "
    "speculate beyond what is visible. If the image is a logo, an icon, a "
    "decorative rule, or a rendered heading rather than a substantive figure, "
    "reply with exactly: NOT_A_FIGURE"
)

NOT_A_FIGURE = "NOT_A_FIGURE"


class Captioner(Protocol):
    """Turns image bytes into one searchable description."""

    model_name: str

    def describe(self, payload: bytes, mime_type: str) -> str | None: ...


@dataclass(frozen=True)
class CaptionSummary:
    """What one captioning pass did to a book."""

    captioned: int = 0
    reused: int = 0
    skipped_boilerplate: int = 0
    skipped_small: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return (
            self.captioned
            + self.reused
            + self.skipped_boilerplate
            + self.skipped_small
            + self.failed
        )


class OpenRouterCaptioner:
    """Hosted vision model through OpenRouter's chat-completions endpoint."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        timeout: float = 90.0,
        max_attempts: int = 3,
    ) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for captioning")
        self.model_name = (
            model_name
            or os.getenv("OPENROUTER_CAPTION_MODEL")
            or DEFAULT_CAPTION_MODEL
        )
        self._max_attempts = max_attempts
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def describe(self, payload: bytes, mime_type: str) -> str | None:
        from base64 import b64encode

        encoded = b64encode(payload).decode("ascii")
        request = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CAPTION_INSTRUCTION},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{encoded}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 200,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(OPENROUTER_CHAT_URL, json=request)
                response.raise_for_status()
                choices = response.json().get("choices") or []
                if not choices:
                    raise ValueError("caption provider returned no choices")
                text = (choices[0]["message"]["content"] or "").strip()
                # Anywhere, not just at the start. Models reach the right
                # judgement and then explain it first — "This text constitutes
                # a heading, not a substantive figure. NOT_A_FIGURE" — and a
                # `startswith` check stored that explanation as a caption and
                # showed the heading as a figure.
                if not text or NOT_A_FIGURE in text.upper():
                    return None
                return text
            except Exception as error:  # noqa: BLE001 - retried, then reported
                last_error = error
                logger.warning(
                    "caption attempt %s/%s failed: %s",
                    attempt,
                    self._max_attempts,
                    error,
                )
        raise RuntimeError(f"captioning failed after retries: {last_error}")


def content_hash(base64_content: str) -> str:
    return hashlib.sha256(base64_content.encode()).hexdigest()


def _boilerplate_hashes(
    connection: Connection,
    *,
    owner_id: UUID,
    threshold: int = BOILERPLATE_REPEAT_THRESHOLD,
) -> set[str]:
    """Hashes whose identical bytes recur across the owner's whole library.

    Scoped to the owner rather than the book on purpose: the same publisher
    badge appears in several books, and three occurrences spread over two
    books is exactly as much evidence of furniture as three in one.
    """

    rows = connection.execute(
        """
        select content_hash, count(*) as occurrences
        from (
            select encode(sha256(base64_content::bytea), 'hex') as content_hash
            from image_blocks
            where owner_id = %s
        ) hashed
        group by content_hash
        having count(*) >= %s
        """,
        (owner_id, threshold),
    ).fetchall()
    return {row["content_hash"] for row in rows}


def _existing_captions(
    connection: Connection,
    *,
    owner_id: UUID,
) -> dict[str, dict[str, Any]]:
    """Captions already produced, keyed by image content.

    The same figure captioned once is reused everywhere it appears, which is
    what keeps a re-run cheap and a repeated diagram consistent.
    """

    rows = connection.execute(
        """
        select distinct on (content_hash)
            content_hash, caption, model_name, skipped_reason
        from image_captions
        where owner_id = %s
        order by content_hash, generated_at desc
        """,
        (owner_id,),
    ).fetchall()
    return {row["content_hash"]: dict(row) for row in rows}


def caption_book_figures(
    connection: Connection,
    book_id: int,
    *,
    owner_id: str | UUID,
    captioner: Captioner,
    on_progress=None,
    only_missing: bool = True,
) -> CaptionSummary:
    """Caption one book's figures, reusing work already done.

    Idempotent: an unchanged figure that already has a caption is left alone,
    so a retry after a partial failure resumes rather than paying twice.
    """

    owner = parse_owner_id(owner_id)
    boilerplate = _boilerplate_hashes(connection, owner_id=owner)
    known = _existing_captions(connection, owner_id=owner)

    rows = connection.execute(
        """
        select
            image_blocks.block_id,
            image_blocks.mime_type,
            image_blocks.base64_content,
            image_captions.block_id as captioned
        from image_blocks
        left join image_captions
          on image_captions.block_id = image_blocks.block_id
         and image_captions.owner_id = image_blocks.owner_id
        where image_blocks.book_id = %s and image_blocks.owner_id = %s
        order by image_blocks.block_id
        """,
        (book_id, owner),
    ).fetchall()

    captioned = reused = skipped_boilerplate = skipped_small = failed = 0

    for index, row in enumerate(rows, start=1):
        if on_progress is not None:
            on_progress(index, len(rows))
        if only_missing and row["captioned"] is not None:
            reused += 1
            continue

        digest = content_hash(row["base64_content"])

        if digest in boilerplate:
            _record(
                connection,
                row["block_id"],
                owner_id=owner,
                book_id=book_id,
                content_hash=digest,
                skipped_reason="boilerplate",
            )
            skipped_boilerplate += 1
            continue

        try:
            payload = b64decode(row["base64_content"], validate=True)
        except Exception:
            _record(
                connection,
                row["block_id"],
                owner_id=owner,
                book_id=book_id,
                content_hash=digest,
                skipped_reason="unsupported",
            )
            failed += 1
            continue

        if len(payload) < MINIMUM_FIGURE_BYTES:
            _record(
                connection,
                row["block_id"],
                owner_id=owner,
                book_id=book_id,
                content_hash=digest,
                skipped_reason="too_small",
            )
            skipped_small += 1
            continue

        previous = known.get(digest)
        if previous is not None:
            _record(
                connection,
                row["block_id"],
                owner_id=owner,
                book_id=book_id,
                content_hash=digest,
                caption=previous["caption"],
                model_name=previous["model_name"],
                skipped_reason=previous["skipped_reason"],
            )
            reused += 1
            continue

        try:
            caption = captioner.describe(payload, row["mime_type"])
        except Exception as error:  # noqa: BLE001 - one figure must not fail a book
            logger.warning("captioning block %s failed: %s", row["block_id"], error)
            _record(
                connection,
                row["block_id"],
                owner_id=owner,
                book_id=book_id,
                content_hash=digest,
                skipped_reason="failed",
            )
            failed += 1
            continue

        if caption is None:
            # The model itself judged this decorative.
            _record(
                connection,
                row["block_id"],
                owner_id=owner,
                book_id=book_id,
                content_hash=digest,
                skipped_reason="boilerplate",
            )
            skipped_boilerplate += 1
            known[digest] = {
                "caption": None,
                "model_name": captioner.model_name,
                "skipped_reason": "boilerplate",
            }
            continue

        _record(
            connection,
            row["block_id"],
            owner_id=owner,
            book_id=book_id,
            content_hash=digest,
            caption=caption,
            model_name=captioner.model_name,
        )
        known[digest] = {
            "caption": caption,
            "model_name": captioner.model_name,
            "skipped_reason": None,
        }
        captioned += 1

    return CaptionSummary(
        captioned=captioned,
        reused=reused,
        skipped_boilerplate=skipped_boilerplate,
        skipped_small=skipped_small,
        failed=failed,
    )


def _record(
    connection: Connection,
    block_id: int,
    *,
    owner_id: UUID,
    book_id: int,
    content_hash: str,
    caption: str | None = None,
    model_name: str | None = None,
    skipped_reason: str | None = None,
) -> None:
    connection.execute(
        """
        insert into image_captions (
            block_id, owner_id, book_id, caption, content_hash,
            model_name, skipped_reason, generated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, now())
        on conflict (block_id) do update set
            caption = excluded.caption,
            content_hash = excluded.content_hash,
            model_name = excluded.model_name,
            skipped_reason = excluded.skipped_reason,
            generated_at = excluded.generated_at
        """,
        (
            block_id,
            owner_id,
            book_id,
            caption,
            content_hash,
            model_name,
            skipped_reason,
        ),
    )


def load_captions(
    connection: Connection,
    block_ids: Sequence[int] | Iterable[int],
    *,
    owner_id: str | UUID,
) -> dict[int, str]:
    """Captions for the given blocks, omitting anything skipped."""

    identifiers = list(block_ids)
    if not identifiers:
        return {}
    rows = connection.execute(
        """
        select block_id, caption from image_captions
        where owner_id = %s and block_id = any(%s) and caption is not null
        """,
        (parse_owner_id(owner_id), identifiers),
    ).fetchall()
    return {row["block_id"]: row["caption"] for row in rows}
