"""Transcribe scanned pages, and measure how far to trust the result.

This is the one generative step that sits inside the canonical layer, so it is
built to be checkable rather than merely accurate. Three things follow from
that:

*Pages are transcribed in isolation.* One page per call, no cross-page context.
A page can then be re-run alone, a bad page cannot poison its neighbours, and
the model cannot carry an early misreading forward as established fact.

*Every reading is stamped.* Provider, model, render dpi, and prompt hash travel
with the text. For a pure scan the transcription is the most faithful record
that exists — there is no better source to fall back to — so the honest
treatment is provenance, not pretending some other artifact is ground truth.

*Every reading is cross-checked.* A deterministic engine reads the same pixels,
and long spans that appear only in the model's version are flagged. Invention
is the failure mode a generative transcription introduces and a dumb one
cannot, and it is invisible downstream: a fabricated sentence reads perfectly
and cites perfectly.

The gate flags and never blocks. Its thresholds are guesses until the gold set
in `docs/ocr-ingestion.md` exists, and a guess that can halt a 400-page book
over one noisy diagram page trades a small risk for a certain one.
"""

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

import fitz
import httpx

from parsing.markup import (
    BBOX_GRID,
    FigureRegion,
    PageMarkup,
    parse_page_markup,
)


# Re-exported so callers of the transcription stage have one import for the
# whole contract: what a provider returns, and how to read it back.
__all__ = [
    "BBOX_GRID",
    "FabricationAssessment",
    "FigureRegion",
    "OcrBudget",
    "OcrError",
    "OcrProvider",
    "OpenRouterOcrProvider",
    "PageMarkup",
    "PageTranscription",
    "TesseractOcrProvider",
    "assess_fabrication",
    "parse_page_markup",
    "prompt_hash",
    "render_page",
]

logger = logging.getLogger("study_partner.ingestion.ocr")

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Top of OCR Arena for dense-text transcription, and about $3 for the 778
# scanned pages in the corpus. The cheap tier is roughly a sixth of that and
# exists so prompt iteration during development does not cost a full pass
# every time. Both are read from the environment: a `-preview` model id will
# be withdrawn eventually, and a hardcoded one turns that into an outage.
DEFAULT_OCR_MODEL = "google/gemini-3-flash-preview"
DEFAULT_OCR_FALLBACK_MODEL = "qwen/qwen3-vl-32b-instruct"

# Sources in this corpus carry only 110-160 dpi of real detail, so rendering
# beyond 300 buys pixels without information while multiplying image tokens.
DEFAULT_RENDER_DPI = 300

# A page of dense prose runs about 900 tokens of Markdown. The ceiling is
# generous enough for a table-heavy page and low enough that a model looping on
# a figure cannot bill for a whole book.
DEFAULT_MAX_OUTPUT_TOKENS = 4_096

PAGE_INSTRUCTION = """\
Transcribe this page of a technical book exactly as it appears.

Rules:
- Transcribe only what is printed. Never complete a sentence, correct an \
error, or supply a word you cannot read. If text is illegible, write \
[illegible] in its place.
- Preserve the reading order a human would follow, including across columns.
- Use Markdown heading levels that match the visual hierarchy of the page.
- Render tables as HTML <table> elements, not as Markdown tables.
- Render mathematics as LaTeX: $inline$ and $$display$$.
- Represent each figure, diagram, or photograph as a <figure> element. Include \
its printed caption as the element's text if it has one. If you can locate the \
figure on the page, add a data-bbox attribute with its bounding box as four \
numbers from 0 to 1 in the order left, top, right, bottom.
- Wrap a running header in <!-- header: ... --> and a running footer, including \
any printed page number, in <!-- footer: ... -->. Reproduce them verbatim.
- Ignore text that has bled through from the reverse side of the page. It \
appears faint and mirrored.

Output the transcription alone. Do not add commentary, and do not wrap the \
whole page in a code fence.\
"""


class OcrError(RuntimeError):
    """A page could not be transcribed."""


@dataclass(frozen=True)
class PageTranscription:
    """One page as read by one engine, with everything needed to reproduce it."""

    page: int
    text: str
    provider: str
    model_id: str
    render_dpi: int
    prompt_hash: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def provenance(self) -> dict[str, object]:
        return {
            "page": self.page,
            "provider": self.provider,
            "model_id": self.model_id,
            "render_dpi": self.render_dpi,
            "prompt_hash": self.prompt_hash,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


class OcrProvider(Protocol):
    """Turns one rendered page into text.

    ``model_id`` and ``prompt_hash`` are part of the contract, not incidental
    attributes: together they identify the *reading*, and the checkpoint store
    reuses a page only when both still match. A provider that hides either one
    silently defeats resumption and pays for every page twice.
    """

    name: str
    model_id: str
    prompt_hash: str

    def transcribe(self, image: bytes, mime_type: str, page: int) -> PageTranscription:
        ...


def render_page(document: fitz.Document, index: int, dpi: int) -> bytes:
    """Render a zero-based page to PNG bytes.

    Renders are derived and cheap to reproduce, so they are never persisted;
    only the thumbnails the review interface needs outlive the job.
    """

    return document[index].get_pixmap(dpi=dpi).tobytes("png")


class OpenRouterOcrProvider:
    """A hosted vision model, one page per request."""

    def __init__(
        self,
        model_id: str | None = None,
        *,
        timeout: float = 180.0,
        max_attempts: int = 3,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        render_dpi: int = DEFAULT_RENDER_DPI,
        instruction: str = PAGE_INSTRUCTION,
    ) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required for OCR")
        self.name = "openrouter"
        self.model_id = (
            model_id or os.getenv("OPENROUTER_OCR_MODEL") or DEFAULT_OCR_MODEL
        )
        self._instruction = instruction
        self.prompt_hash = prompt_hash(instruction)
        self._max_attempts = max_attempts
        self._max_output_tokens = max_output_tokens
        self._render_dpi = render_dpi
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def transcribe(self, image: bytes, mime_type: str, page: int) -> PageTranscription:
        encoded = b64encode(image).decode("ascii")
        request = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            "max_tokens": self._max_output_tokens,
            # Transcription is not a creative task, and a page re-run after a
            # crash should produce the same text as the run it replaces.
            "temperature": 0,
            # Ask OpenRouter to bill the response back to us. Estimating cost
            # from token counts and a price table goes stale the moment a model
            # is repriced, and this evaluation reports real dollars.
            "usage": {"include": True},
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(OPENROUTER_CHAT_URL, json=request)
                response.raise_for_status()
                body = response.json()
                choices = body.get("choices") or []
                if not choices:
                    raise OcrError("OCR provider returned no choices")
                text = (choices[0]["message"].get("content") or "").strip()
                if not text:
                    raise OcrError("OCR provider returned empty text")
                text, removed = collapse_degenerate_runs(text)
                if removed:
                    logger.warning(
                        "page %s: collapsed %s characters of repetition; the "
                        "page may be truncated where the loop consumed the "
                        "output budget",
                        page,
                        removed,
                    )
                usage = body.get("usage") or {}
                return PageTranscription(
                    page=page,
                    text=_strip_outer_fence(text),
                    provider=self.name,
                    model_id=self.model_id,
                    render_dpi=self._render_dpi,
                    prompt_hash=self.prompt_hash,
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    cost_usd=float(usage.get("cost") or 0.0),
                )
            except Exception as error:  # noqa: BLE001 - retried, then reported
                last_error = error
                logger.warning(
                    "ocr attempt %s/%s failed for page %s: %s",
                    attempt,
                    self._max_attempts,
                    page,
                    error,
                )
        raise OcrError(f"page {page} failed after retries: {last_error}")


class TesseractOcrProvider:
    """The local deterministic engine.

    It is the cross-check rather than the product. It garbles equations and
    reads bleed-through as words, but it has no capacity to invent a sentence,
    which is exactly the property the gate needs from a reference.
    """

    def __init__(
        self,
        *,
        binary: str = "tesseract",
        page_segmentation: str = "1",
        language: str = "eng",
        render_dpi: int = DEFAULT_RENDER_DPI,
    ) -> None:
        resolved = shutil.which(binary)
        if resolved is None:
            raise ValueError(f"{binary} is not installed")
        self.name = "tesseract"
        self.model_id = _tesseract_version(resolved)
        # No instruction to identify: the engine is the whole reading.
        self.prompt_hash = ""
        self._binary = resolved
        self._psm = page_segmentation
        self._language = language
        self._render_dpi = render_dpi

    def transcribe(self, image: bytes, mime_type: str, page: int) -> PageTranscription:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "page.png"
            source.write_bytes(image)
            completed = subprocess.run(
                [
                    self._binary,
                    str(source),
                    "stdout",
                    "-l",
                    self._language,
                    "--psm",
                    self._psm,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise OcrError(
                f"tesseract failed on page {page}: {completed.stderr.strip()[:200]}"
            )
        return PageTranscription(
            page=page,
            text=completed.stdout.strip(),
            provider=self.name,
            model_id=self.model_id,
            render_dpi=self._render_dpi,
            prompt_hash=self.prompt_hash,
        )


def prompt_hash(instruction: str) -> str:
    """Identify the exact instruction a transcription was produced under."""

    return sha256(instruction.encode("utf-8")).hexdigest()[:16]


def _tesseract_version(binary: str) -> str:
    try:
        completed = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, check=False
        )
        first = (completed.stdout or completed.stderr).strip().splitlines()[0]
        return first.strip()
    except Exception:  # noqa: BLE001 - identification only, never fatal
        return "tesseract"


_OUTER_FENCE = re.compile(r"\A```[a-zA-Z]*\n(?P<body>.*)\n```\s*\Z", re.DOTALL)

# A run of one character repeated this many times is not something a page
# prints; it is the model looping. Measured: one page produced 59,648 hyphens
# in a row, consumed its entire 4,096-token output budget doing it, and left
# the rest of the page untranscribed.
DEGENERATE_RUN = 24
_DEGENERATE = re.compile(r"(\S)\1{" + str(DEGENERATE_RUN) + r",}")


def collapse_degenerate_runs(text: str) -> tuple[str, int]:
    """Shorten absurd character repetitions, and say how much was removed.

    A rule printed on a page is a handful of characters; tens of thousands is a
    generation failure. Collapsing is not a loss of content - the run carries
    none - but it is a *change* to the canonical text, so the caller records
    that it happened rather than letting it pass silently.
    """

    collapsed = _DEGENERATE.sub(lambda match: match.group(1) * 3, text)
    return collapsed, len(text) - len(collapsed)


def _strip_outer_fence(text: str) -> str:
    """Unwrap a whole page the model fenced despite being asked not to.

    Only the outermost fence, and only when it wraps everything: a page that
    legitimately contains a code listing must keep its fences.
    """

    match = _OUTER_FENCE.match(text.strip())
    return match.group("body") if match else text


# --- fabrication gate -------------------------------------------------------

# Below this, a mismatch is OCR noise rather than invention. A fabricated
# passage is a whole clause or sentence; a garbled word is one token.
DEFAULT_RUN_THRESHOLD = 12
# A reference this short means the deterministic engine failed on the page, not
# that the model invented it. Figure-only pages land here constantly.
MINIMUM_REFERENCE_TOKENS = 30
# Short tokens carry no evidence either way, and OCR mangles them most.
MINIMUM_COMPARABLE_TOKEN = 4

UNASSESSABLE = "unassessable"
SUPPORTED = "supported"
FLAGGED = "flagged"

_MARKUP = re.compile(r"<[^>]+>|<!--.*?-->|\$\$?.*?\$\$?|`[^`]*`", re.DOTALL)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class FabricationAssessment:
    """How much of a model's reading the deterministic engine can corroborate."""

    verdict: str
    longest_unsupported_run: int = 0
    unsupported_ratio: float = 0.0
    comparable_tokens: int = 0
    sample: str = ""

    @property
    def flagged(self) -> bool:
        return self.verdict == FLAGGED

    def provenance(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "longest_unsupported_run": self.longest_unsupported_run,
            "unsupported_ratio": round(self.unsupported_ratio, 4),
            "comparable_tokens": self.comparable_tokens,
            "sample": self.sample,
        }


def _comparable_tokens(text: str) -> list[str]:
    """Words worth comparing between two readings of the same pixels.

    Markup is stripped first: the model is asked for HTML tables, LaTeX, and
    comment-wrapped headers, and none of that exists in a plain-text reference.
    Counting it as unsupported would flag every well-formed page.
    """

    stripped = _MARKUP.sub(" ", text)
    return [
        token.lower()
        for token in _TOKEN.findall(stripped)
        if len(token) >= MINIMUM_COMPARABLE_TOKEN
    ]


def assess_fabrication(
    candidate: str,
    reference: str,
    *,
    run_threshold: int = DEFAULT_RUN_THRESHOLD,
) -> FabricationAssessment:
    """Flag spans present in ``candidate`` that ``reference`` cannot corroborate.

    The signal is a long *consecutive* run of uncorroborated words, not the
    overall mismatch rate. Two engines reading the same page disagree
    constantly at the word level and still agree about what the page says; a
    dozen words in a row that only one of them saw is a different claim
    entirely.
    """

    # Repetition is invention of a different shape, and the word-level
    # comparison below is blind to it: a page of hyphens contributes no
    # comparable tokens at all, so it scored "supported" while carrying 59,648
    # characters the page does not have.
    _, repeated = collapse_degenerate_runs(candidate)
    if repeated:
        return FabricationAssessment(
            verdict=FLAGGED,
            longest_unsupported_run=repeated,
            unsupported_ratio=repeated / max(len(candidate), 1),
            comparable_tokens=0,
            sample=f"{repeated} characters of repeated output",
        )

    reference_tokens = set(_comparable_tokens(reference))
    candidate_tokens = _comparable_tokens(candidate)

    if len(reference_tokens) < MINIMUM_REFERENCE_TOKENS or not candidate_tokens:
        return FabricationAssessment(
            verdict=UNASSESSABLE, comparable_tokens=len(candidate_tokens)
        )

    longest = 0
    current = 0
    unsupported = 0
    longest_end = 0
    for index, token in enumerate(candidate_tokens):
        if token in reference_tokens:
            current = 0
            continue
        current += 1
        unsupported += 1
        if current > longest:
            longest = current
            longest_end = index + 1

    verdict = FLAGGED if longest >= run_threshold else SUPPORTED
    sample = ""
    if verdict == FLAGGED:
        sample = " ".join(candidate_tokens[longest_end - longest : longest_end])[:200]

    return FabricationAssessment(
        verdict=verdict,
        longest_unsupported_run=longest,
        unsupported_ratio=unsupported / len(candidate_tokens),
        comparable_tokens=len(candidate_tokens),
        sample=sample,
    )


@dataclass
class OcrBudget:
    """A hard ceiling on what one job may spend.

    Exceeding it aborts with a named error. A retry loop over 778 pages is the
    failure mode worth engineering against, and the version that finds out from
    the bill is the one without this class.
    """

    max_pages: int
    max_cost_usd: float
    pages_done: int = 0
    cost_usd: float = 0.0

    def charge(self, transcription: PageTranscription) -> None:
        self.pages_done += 1
        self.cost_usd += transcription.cost_usd
        if self.pages_done > self.max_pages:
            raise OcrError(
                f"OCR page budget exhausted: {self.pages_done} > {self.max_pages}"
            )
        if self.cost_usd > self.max_cost_usd:
            raise OcrError(
                f"OCR cost budget exhausted: "
                f"${self.cost_usd:.2f} > ${self.max_cost_usd:.2f}"
            )

    def provenance(self) -> dict[str, object]:
        return {
            "pages_done": self.pages_done,
            "cost_usd": round(self.cost_usd, 6),
            "max_pages": self.max_pages,
            "max_cost_usd": self.max_cost_usd,
        }
