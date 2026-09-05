"""Describe a cited figure for someone who is listening, not looking.

The ingest caption already says what a figure shows, but it was written to be
*matched*: dense, unprefaced, and full of the noun phrases retrieval needs
("Scatter of horsepower against weight, fitted line, R-squared 0.72"). Read
out mid-sentence it lands as a fragment, and a spoken 'R-squared 0.72' is the
kind of thing a voice model renders as "R hyphen squared".

So a figure gets a second description, written for the ear, generated once per
distinct image and stored. The cost shape is the same argument captioning
made: one vision call per image, ever, against a per-play cost if the
description were produced on demand and thrown away.
"""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import logging
import os
import re
from typing import Protocol
from uuid import UUID

import httpx
from psycopg import Connection

from storage.book_images import load_figure


logger = logging.getLogger("study_partner.narration.figures")

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_NARRATION_MODEL = "google/gemini-2.5-flash-lite"

# A description is a sentence or two inside a spoken answer, not a monologue.
# The cap is enforced on the way in as well as asked for in the instruction,
# because a model that ignores the word limit would otherwise stall playback
# on a paragraph nobody asked for.
MAXIMUM_DESCRIPTION_CHARACTERS = 2_000

# One request narrates the figures of one answer. An answer citing more than
# this many distinct figures is not a thing that happens; the cap is here so a
# crafted request cannot fan out into an unbounded number of vision calls.
MAXIMUM_FIGURES_PER_REQUEST = 12

NARRATION_INSTRUCTION = (
    "You are describing a figure from a technical source to someone who is "
    "listening to an answer being read aloud and cannot see the page.\n\n"
    "Write one or two sentences of plain spoken English, at most 45 words. "
    "Say what the figure shows and the point it is evidence for: the axes and "
    "the trend, the parts and how they connect, or the values that matter.\n\n"
    "Write it to be heard. Full sentences, no lists, no headings, no markdown, "
    "no parentheses, and no symbol a voice cannot say: write 'approximately' "
    "rather than an approximation sign, 'R squared of about 0.7' rather than "
    "'R^2=0.72', 'x-axis' rather than an axis label in isolation. Spell short "
    "formulas out in words.\n\n"
    "Do not begin with 'This figure', 'The image shows', or similar — the "
    "listener has already been told a figure is coming. Do not speculate "
    "beyond what is visible. If the image is a logo, an icon, a decorative "
    "rule, or a rendered heading rather than a substantive figure, reply with "
    "exactly: NOT_A_FIGURE"
)

NOT_A_FIGURE = "NOT_A_FIGURE"

# Asking for no preamble is not the same as getting none. Measured on the real
# corpus, `google/gemini-2.5-flash-lite` opens with "This diagram shows…"
# regardless of the instruction — the same thing captioning found, which is why
# it matches NOT_A_FIGURE anywhere rather than at the start.
#
# The preamble matters more here than it did there. The script has already said
# "Figure, page 257." by the time this is spoken, so "This diagram shows a
# summary of…" makes the listener wait through a second announcement for the
# content. Stripping it deterministically is cheaper and more reliable than
# another round of prompt wording.
OPENER = re.compile(
    r"^\s*(?:this|the|that)\s+"
    r"(?:figure|diagram|image|picture|chart|plot|graph|table|screenshot"
    r"|illustration|schematic|flowchart|visualization|visualisation)\s+"
    r"(?:shows|illustrates|depicts|presents|displays|outlines|describes"
    r"|represents|summari[sz]es|plots|compares|visuali[sz]es)\s+",
    re.IGNORECASE,
)

# Below this, removing the opener would leave a fragment rather than a
# sentence, and a fragment is worse than a redundant lead-in.
MINIMUM_REMAINDER_CHARACTERS = 20


def without_opener(description: str) -> str:
    """Drop a "This diagram shows" preamble, keeping the sentence readable."""

    remainder = OPENER.sub("", description.strip(), count=1)
    if remainder == description.strip():
        return description.strip()
    if len(remainder) < MINIMUM_REMAINDER_CHARACTERS:
        return description.strip()
    return remainder[0].upper() + remainder[1:]


class FigureNarrator(Protocol):
    """Turns image bytes into one description written to be heard."""

    model_name: str

    def narrate(self, payload: bytes, mime_type: str) -> str | None: ...


@dataclass(frozen=True)
class FigureRequest:
    book_id: int
    block_id: int


class OpenRouterFigureNarrator:
    """The hosted vision model, through OpenRouter's chat-completions endpoint."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model_name = (
            model_name
            or os.getenv("OPENROUTER_NARRATION_FIGURE_MODEL", "").strip()
            or os.getenv("OPENROUTER_CAPTION_MODEL", "").strip()
            or DEFAULT_NARRATION_MODEL
        )
        if client is not None:
            self._client = client
            return
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required to narrate figures")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def narrate(self, payload: bytes, mime_type: str) -> str | None:
        encoded = b64encode(payload).decode("ascii")
        response = self._client.post(
            OPENROUTER_CHAT_URL,
            json={
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": NARRATION_INSTRUCTION},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 160,
            },
        )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            raise ValueError("narration provider returned no choices")
        text = (choices[0]["message"]["content"] or "").strip()
        # Matched anywhere, not just at the start: captioning learned that a
        # model reaches the right judgement and then explains it first.
        if not text or NOT_A_FIGURE in text.upper():
            return None
        return text


def _rows_for(
    connection: Connection,
    *,
    owner_id: UUID,
    figures: Sequence[FigureRequest],
) -> dict[int, dict]:
    """The blocks the caller may hear, with any description already stored.

    Owner-scoped in the query itself, so a block belonging to someone else is
    indistinguishable from one that does not exist. The caption comes along as
    the fallback for a figure whose description cannot be produced right now:
    dense prose read aloud beats silence where the answer says "as shown".
    """

    if not figures:
        return {}
    block_ids = sorted({figure.block_id for figure in figures})
    rows = connection.execute(
        """
        select image_blocks.block_id,
               image_blocks.book_id,
               image_blocks.mime_type,
               image_blocks.storage_key,
               image_blocks.base64_hash,
               image_blocks.owner_id,
               image_captions.caption,
               image_captions.skipped_reason,
               narration_figures.description
        from image_blocks
        left join image_captions
          on image_captions.block_id = image_blocks.block_id
         and image_captions.owner_id = image_blocks.owner_id
        left join narration_figures
          on narration_figures.content_hash = image_blocks.base64_hash
         and narration_figures.owner_id = image_blocks.owner_id
        where image_blocks.owner_id = %s
          and image_blocks.block_id = any(%s)
        """,
        (owner_id, block_ids),
    ).fetchall()
    return {row["block_id"]: row for row in rows}


def _store(
    connection: Connection,
    *,
    owner_id: UUID,
    content_hash: str,
    description: str,
    model_name: str,
) -> None:
    connection.execute(
        """
        insert into narration_figures
            (owner_id, content_hash, description, model_name)
        values (%s, %s, %s, %s)
        on conflict (owner_id, content_hash) do update
            set description = excluded.description,
                model_name = excluded.model_name,
                generated_at = now()
        """,
        (owner_id, content_hash, description[:MAXIMUM_DESCRIPTION_CHARACTERS], model_name),
    )


def spoken_descriptions(
    connection: Connection,
    *,
    owner_id: UUID,
    figures: Iterable[FigureRequest],
    narrator: FigureNarrator | None = None,
) -> dict[int, str]:
    """Spoken descriptions for the figures an answer cites, by block id.

    Generates what is missing and stores it. A figure that cannot be described
    — the captioner already judged it decorative, its bytes are unreadable, or
    the provider is down — falls back to its caption, and to nothing at all if
    it has none. The caller says "figure on page 84" either way, so a missing
    description costs the listener a description, not the fact of the figure.
    """

    requested = list(figures)[:MAXIMUM_FIGURES_PER_REQUEST]
    rows = _rows_for(connection, owner_id=owner_id, figures=requested)

    described: dict[int, str] = {}
    # Two figures in one answer can be the same image. Describing it twice
    # would be two vision calls for one result.
    generated_by_hash: dict[str, str] = {}
    lazy_narrator = narrator

    for figure in requested:
        row = rows.get(figure.block_id)
        # The book has to match too: a block id names one block, and a request
        # pairing it with the wrong book is a request for something that does
        # not exist.
        if row is None or row["book_id"] != figure.book_id:
            continue

        stored = (row["description"] or "").strip()
        if stored:
            described[figure.block_id] = stored
            continue

        content_hash = row["base64_hash"]
        if content_hash and content_hash in generated_by_hash:
            described[figure.block_id] = generated_by_hash[content_hash]
            continue

        caption = (row["caption"] or "").strip()
        # The captioner already looked at this image and judged it furniture.
        # Paying a vision call to reach the same conclusion is waste.
        if row["skipped_reason"] in {"boilerplate", "too_small", "unsupported"}:
            continue

        if lazy_narrator is None:
            try:
                lazy_narrator = OpenRouterFigureNarrator()
            except ValueError as error:
                logger.warning("figure narration is unavailable: %s", error)
                if caption:
                    described[figure.block_id] = caption
                continue

        try:
            payload = load_figure(row)
            # Sanitised here rather than inside one narrator: what gets stored
            # and spoken should not depend on which client produced it.
            description = without_opener(
                lazy_narrator.narrate(payload, row["mime_type"]) or ""
            )[:MAXIMUM_DESCRIPTION_CHARACTERS]
        except Exception as error:  # noqa: BLE001 - reported, then degraded
            logger.warning(
                "figure %s could not be narrated: %s", figure.block_id, error
            )
            if caption:
                described[figure.block_id] = caption
            continue

        if not description:
            # The model says this is not a substantive figure. Announcing it
            # and saying nothing about it is the honest outcome.
            continue

        described[figure.block_id] = description
        if content_hash:
            generated_by_hash[content_hash] = description
            _store(
                connection,
                owner_id=owner_id,
                content_hash=content_hash,
                description=description,
                model_name=lazy_narrator.model_name,
            )

    return described
