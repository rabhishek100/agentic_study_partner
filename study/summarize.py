"""One-call grounded summarization for a complete resolved scope."""


import logging
import re
from dataclasses import dataclass, replace
from typing import Protocol

import tiktoken

from observability import traced
from .context import DEFAULT_ENCODING, ScopeContext
from .contracts import PromptProfile, ResponseDepth
from .prompts import (
    DEFAULT_PROMPT_PROFILE,
    LOCKED_GROUNDING_PROMPT,
    build_answer_messages,
)
from .scope import ResolvedScope
from .streaming import TokenCallback, invoke_with_streaming

CITATION = re.compile(r"\[N(\d+):P(\d+)]")
GROUPED_CITATION = re.compile(r"\[((?:N\d+:P\d+)(?:\s*;\s*N\d+:P\d+)+)]")
OPENAI_CITATION = re.compile(r"\ue200cite((?:\ue202[^\ue200-\ue203]+)+)\ue201")
OPENAI_CITATION_TARGET = re.compile(r"\ue202((?:S\d+)|(?:N\d+:P\d+))")

# Bracket glyphs a model may substitute for plain ASCII. Translated before any
# marker is parsed; see `normalize_citation_syntax`.
BRACKET_GLYPHS = str.maketrans(
    {
        "\u3010": "[",  # 【 CJK left black lenticular
        "\u3011": "]",  # 】
        "\uff3b": "[",  # ［ fullwidth left square
        "\uff3d": "]",  # ］
        "\u301a": "[",  # 〚 left white square
        "\u301b": "]",  # 〛
    }
)
OPTIONAL_RECAP_TITLES = frozenset({"summary", "conclusion"})
OPTIONAL_INTERVIEW_SECTION = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:lab\b|exercises?\b)",
    re.IGNORECASE,
)
logger = logging.getLogger("study_partner.summarize")


class SummaryModel(Protocol):
    """Minimal LangChain-compatible model contract used by tests and runtime."""

    def invoke(self, messages: list[tuple[str, str]]): ...


@dataclass(frozen=True)
class SummaryConfig:
    """Explicit prompt budget; complete inputs are never silently truncated."""

    context_window_tokens: int = 64_000
    max_output_tokens: int = 8_000
    safety_margin_tokens: int = 1_000
    encoding_name: str = DEFAULT_ENCODING

    def __post_init__(self) -> None:
        if self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.safety_margin_tokens < 0:
            raise ValueError("safety_margin_tokens cannot be negative")
        if (
            self.max_output_tokens + self.safety_margin_tokens
            >= self.context_window_tokens
        ):
            raise ValueError("output and safety budgets must leave room for input")


@dataclass(frozen=True)
class PromptBudget:
    input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    context_window_tokens: int

    @property
    def required_tokens(self) -> int:
        return self.input_tokens + self.max_output_tokens + self.safety_margin_tokens

    @property
    def fits(self) -> bool:
        return self.required_tokens <= self.context_window_tokens


class ContextWindowExceededError(ValueError):
    """The complete prompt cannot be sent without truncating source evidence."""

    def __init__(self, budget: PromptBudget) -> None:
        self.budget = budget
        super().__init__(
            f"complete prompt requires {budget.required_tokens} tokens "
            f"but the configured context window is "
            f"{budget.context_window_tokens}; choose a larger-context model "
            "or add an evaluated hierarchical fallback"
        )


@dataclass(frozen=True)
class SummaryValidation:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    cited_node_ids: frozenset[int]
    missing_node_ids: frozenset[int]
    required_missing_node_ids: frozenset[int]
    optional_missing_node_ids: frozenset[int]
    citation_errors: tuple[str, ...]

    @property
    def citation_safe(self) -> bool:
        """Whether every citation is present, valid, and inside the scope."""

        return not self.citation_errors

    @property
    def coverage_complete(self) -> bool:
        return not self.required_missing_node_ids


@dataclass(frozen=True)
class SummaryResult:
    text: str
    validation: SummaryValidation
    budget: PromptBudget
    finish_reason: str | None
    attempt_count: int = 1
    initial_errors: tuple[str, ...] = ()


def _optional_coverage_node_ids(
    scope: ResolvedScope,
    context: ScopeContext,
    response_depth: ResponseDepth,
) -> frozenset[int]:
    optional: set[int] = set()
    for node in scope.nodes:
        if node.id not in context.expected_node_ids:
            continue
        if node.title.casefold().strip() in OPTIONAL_RECAP_TITLES:
            optional.add(node.id)
            continue
        if response_depth != "interview":
            continue
        path_parts = (part.strip() for part in node.path_text.split(" :: "))
        if any(OPTIONAL_INTERVIEW_SECTION.match(part) for part in path_parts):
            optional.add(node.id)
    return frozenset(optional)


def _coverage_lines(
    scope: ResolvedScope,
    context: ScopeContext,
    node_ids: frozenset[int],
) -> str:
    allowed_pages_by_node: dict[int, list[int]] = {}
    for node_id, page in sorted(context.allowed_citations):
        allowed_pages_by_node.setdefault(node_id, []).append(page)
    return "\n".join(
        f"- Node {node.id}: {node.path_text}; allowed citations: "
        + ", ".join(f"[N{node.id}:P{page}]" for page in allowed_pages_by_node[node.id])
        for node in scope.nodes
        if node.id in node_ids
    )


def build_summary_messages(
    scope: ResolvedScope,
    context: ScopeContext,
    *,
    validation_feedback: tuple[str, ...] = (),
    profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
) -> list[tuple[str, str]]:
    """Build the grounded prompt over the complete formatted scope."""

    optional_node_ids = _optional_coverage_node_ids(
        scope,
        context,
        response_depth,
    )
    required_node_ids = context.expected_node_ids.difference(optional_node_ids)
    required_sections = _coverage_lines(scope, context, required_node_ids)
    optional_sections = _coverage_lines(scope, context, optional_node_ids)
    optional_instruction = (
        "\n\nOptional supporting coverage:\n"
        f"{optional_sections}\n"
        "Use these sections when they add interview value, but shorten or omit "
        "them before sacrificing required coverage."
        if optional_sections
        else ""
    )
    correction = ""
    if validation_feedback:
        correction = (
            "\n\nA previous draft was rejected by deterministic validation:\n"
            + "\n".join(f"- {error}" for error in validation_feedback)
            + "\nRegenerate the complete summary. Fix every listed problem. "
            "Use only citation markers present in Required coverage or "
            "Complete evidence; do not guess or combine node IDs and pages.\n"
        )
    grounding = f"""
This is a complete-scope review, not top-k retrieval. Cite every
substantive claim with [N<node>:P<page>] copied exactly from the supplied
evidence markers; never combine a node ID with a page outside that node's
allowed list.
Complete coverage is mandatory: cite every node in Required coverage at least
once. Preserve the listed source order internally even when organizing the
answer by interview usefulness. Do not infer omitted images.

Required coverage:
{required_sections}
{optional_instruction}

If space becomes limited, shorten overview, examples, follow-ups, and revision
cues before omitting a required node. Use citations such as [N14:P21] and do
copy them directly from the evidence. Before returning the answer, verify that
every required node appears in at least one citation and every substantive
paragraph or bullet carries a supporting citation.
{correction}
""".strip()
    source_label = "Paper" if scope.document_type == "paper" else "Book"
    task = (
        f"Explain the complete paper {scope.book_title}."
        if scope.document_type == "paper" and scope.kind == "book"
        else f"Prepare {scope.display_path} for a technical interview."
    )
    request_context = (
        f"{source_label}: {scope.book_title}\n"
        f"Scope: {scope.display_path}\n"
        f"PDF pages: {scope.start_page}–{scope.end_page}\n\n"
        f"Required coverage:\n{required_sections}\n\n"
        f"{optional_instruction}\n\n"
        "If space becomes limited, shorten items 2, 4, 5, and 6 before "
        "omitting any required node."
        f"{correction}"
    )
    return build_answer_messages(
        profile=profile or DEFAULT_PROMPT_PROFILE,
        question=task,
        evidence=context.text,
        archetype="chapter_review",
        depth=response_depth,
        request_context=request_context,
        additional_grounding=grounding,
    )


def prompt_budget(
    messages: list[tuple[str, str]],
    *,
    config: SummaryConfig,
) -> PromptBudget:
    """Estimate the complete request conservatively with explicit reserves."""

    encoding = tiktoken.get_encoding(config.encoding_name)
    input_tokens = (
        sum(
            len(encoding.encode(role)) + len(encoding.encode(content)) + 4
            for role, content in messages
        )
        + 4
    )
    return PromptBudget(
        input_tokens=input_tokens,
        max_output_tokens=config.max_output_tokens,
        safety_margin_tokens=config.safety_margin_tokens,
        context_window_tokens=config.context_window_tokens,
    )


@traced("study.summarize.validate_summary", flow="summary")
def validate_summary(
    text: str,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    response_depth: ResponseDepth = "interview",
) -> SummaryValidation:
    """Reject invented citations and report uncovered content-bearing nodes."""

    citation_errors: list[str] = []
    coverage_errors: list[str] = []
    warnings: list[str] = []
    citations = [(int(node_id), int(page)) for node_id, page in CITATION.findall(text)]
    valid_citations = {
        citation for citation in citations if citation in context.allowed_citations
    }
    invalid_citations = sorted(set(citations).difference(context.allowed_citations))
    if not citations:
        citation_errors.append("summary contains no citations")
    if invalid_citations:
        citation_errors.append(
            "summary contains out-of-scope citations: "
            + ", ".join(f"[N{node_id}:P{page}]" for node_id, page in invalid_citations)
        )

    cited_nodes = frozenset(node_id for node_id, _ in valid_citations)
    missing_nodes = context.expected_node_ids.difference(cited_nodes)
    nodes = {node.id: node for node in scope.nodes}
    optional_nodes = missing_nodes.intersection(
        _optional_coverage_node_ids(scope, context, response_depth)
    )
    required_missing_nodes = missing_nodes.difference(optional_nodes)
    if required_missing_nodes:
        coverage_errors.append(
            "summary does not cite content from required nodes: "
            + "; ".join(
                f"{node_id} ({nodes[node_id].path_text})"
                for node_id in sorted(required_missing_nodes)
            )
        )
    if optional_nodes:
        warnings.append(
            "Summary omits optional supporting sections: "
            + "; ".join(nodes[node_id].path_text for node_id in sorted(optional_nodes))
        )
    errors = (*citation_errors, *coverage_errors)
    return SummaryValidation(
        valid=not errors,
        errors=errors,
        warnings=tuple(warnings),
        cited_node_ids=cited_nodes,
        missing_node_ids=frozenset(missing_nodes),
        required_missing_node_ids=frozenset(required_missing_nodes),
        optional_missing_node_ids=frozenset(optional_nodes),
        citation_errors=tuple(citation_errors),
    )


def normalize_citation_syntax(text: str) -> str:
    """Normalize provider-rendered and grouped markers to the app contract.

    Bracket glyphs come first, because a model that reaches for a fullwidth
    bracket breaks grounding silently rather than loudly. On 2026-09-04 an
    interview answer came back with twenty-five `\u3010S1\u3011` markers and not
    one `[S1]`: the extractor below matched none of them, the turn was stored
    with an empty citation list, and the interface rendered the markers as
    literal text because there was nothing to link them to. The answer looked
    complete and was ungrounded.

    Which bracket a model picks is not part of the contract, so the glyph is
    normalised rather than the parsers being taught every variant. CJK corner
    brackets and fullwidth square brackets are the two that turn up.
    """

    text = text.translate(BRACKET_GLYPHS)

    provider_normalized = OPENAI_CITATION.sub(
        lambda match: " ".join(
            f"[{target}]"
            for target in OPENAI_CITATION_TARGET.findall(match.group(1))
        ),
        text,
    )
    return GROUPED_CITATION.sub(
        lambda match: " ".join(
            f"[{marker.strip()}]" for marker in match.group(1).split(";")
        ),
        provider_normalized,
    )


def append_references(text: str, *, scope: ResolvedScope) -> str:
    """Append exact hierarchy/page references used by the summary."""

    nodes = {node.id: node for node in scope.nodes}
    citations: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in CITATION.finditer(text):
        citation = (int(match.group(1)), int(match.group(2)))
        if citation in seen or citation[0] not in nodes:
            continue
        seen.add(citation)
        citations.append(citation)
    if not citations:
        return text.rstrip()

    references = ["## References", ""]
    for node_id, page in citations:
        node = nodes[node_id]
        hierarchy = node.path_text.replace(" :: ", " → ")
        # A reader looks for the number printed on the page, not the index of
        # the file. For a scan those differ by however much front matter was
        # included and by whatever pages the scanner missed - up to eight in
        # this library - so both are given where the mapping was measured.
        printed = scope.printed_page(page)
        location = (
            f"PDF p. {page}" if printed is None else f"p. {printed} (PDF p. {page})"
        )
        references.append(
            f"- [N{node_id}:P{page}] {scope.book_title} → {hierarchy} — {location}"
        )
    return text.rstrip() + "\n\n" + "\n".join(references)


def _response_text(response) -> str:
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        raise TypeError("summary model returned non-text content")
    if not content.strip():
        raise ValueError("summary model returned an empty response")
    return content.strip()


def _finish_reason(response) -> str | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    reason = metadata.get("finish_reason") or metadata.get("stop_reason")
    return str(reason) if reason is not None else None


@traced("study.summarize.summarize_scope", flow="summary")
def summarize_scope(
    model: SummaryModel,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    config: SummaryConfig | None = None,
    validation_feedback: tuple[str, ...] = (),
    token_callback: TokenCallback | None = None,
    profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
) -> SummaryResult:
    """Make one complete-scope call and validate the returned citations."""

    config = config or SummaryConfig()
    messages = build_summary_messages(
        scope,
        context,
        validation_feedback=validation_feedback,
        profile=profile,
        response_depth=response_depth,
    )
    budget = prompt_budget(messages, config=config)
    if not budget.fits:
        raise ContextWindowExceededError(budget)
    response = invoke_with_streaming(model, messages, token_callback=token_callback)
    text = normalize_citation_syntax(_response_text(response))
    return SummaryResult(
        text=text,
        validation=validate_summary(
            text,
            scope=scope,
            context=context,
            response_depth=response_depth,
        ),
        budget=budget,
        finish_reason=_finish_reason(response),
    )


@traced("study.summarize.summarize_scope_with_repair", flow="summary")
def summarize_scope_with_repair(
    model: SummaryModel,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    config: SummaryConfig | None = None,
    profile: PromptProfile | None = None,
    response_depth: ResponseDepth = "interview",
) -> SummaryResult:
    """Use complete provider responses and repair only genuine invalid drafts.

    Summary streaming is intentionally disabled here. Some OpenRouter provider
    streams have ended without a terminal finish reason, yielding a plausible
    but truncated early-chapter draft. A normal invoke returns the complete
    response before deterministic coverage and citation validation runs.
    """

    config = config or SummaryConfig()
    first = summarize_scope(
        model,
        scope=scope,
        context=context,
        config=config,
        token_callback=None,
        profile=profile,
        response_depth=response_depth,
    )
    if first.validation.valid:
        return first
    logger.warning(
        "summary draft failed validation attempt=1 scope=%r chars=%d "
        "finish_reason=%r errors=%s",
        scope.display_path,
        len(first.text),
        first.finish_reason,
        "; ".join(first.validation.errors),
    )
    repaired = _repair_summary(
        model,
        first,
        scope=scope,
        context=context,
        config=config,
        profile=profile,
        response_depth=response_depth,
    )
    if repaired.validation.valid:
        logger.info(
            "summary repair succeeded attempt=2 scope=%r chars=%d",
            scope.display_path,
            len(repaired.text),
        )
        return replace(
            repaired,
            attempt_count=2,
            initial_errors=first.validation.errors,
        )
    logger.warning(
        "summary draft failed validation attempt=2 scope=%r chars=%d "
        "finish_reason=%r errors=%s",
        scope.display_path,
        len(repaired.text),
        repaired.finish_reason,
        "; ".join(repaired.validation.errors),
    )
    final = _repair_summary(
        model,
        repaired,
        scope=scope,
        context=context,
        config=config,
        profile=profile,
        response_depth=response_depth,
    )
    if not final.validation.valid:
        logger.warning(
            "summary draft failed validation attempt=3 scope=%r chars=%d "
            "finish_reason=%r errors=%s",
            scope.display_path,
            len(final.text),
            final.finish_reason,
            "; ".join(final.validation.errors),
        )
    else:
        logger.info(
            "summary repair succeeded attempt=3 scope=%r chars=%d",
            scope.display_path,
            len(final.text),
        )
    safe_candidates = [
        candidate
        for candidate in (first, repaired, final)
        if candidate.validation.citation_safe
    ]
    best = min(
        safe_candidates,
        key=lambda candidate: len(candidate.validation.required_missing_node_ids),
        default=final,
    )
    return replace(
        best,
        attempt_count=3,
        initial_errors=first.validation.errors,
    )


@traced("study.summarize._repair_summary", flow="summary")
def _repair_summary(
    model: SummaryModel,
    result: SummaryResult,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    config: SummaryConfig,
    profile: PromptProfile | None,
    response_depth: ResponseDepth,
) -> SummaryResult:
    """Repair only missing coverage when citations are already safe."""

    missing = result.validation.required_missing_node_ids
    if not result.validation.citation_safe or not missing:
        return summarize_scope(
            model,
            scope=scope,
            context=context,
            config=config,
            validation_feedback=result.validation.errors,
            token_callback=None,
            profile=profile,
            response_depth=response_depth,
        )

    coverage = _coverage_lines(scope, context, missing)
    messages = [
        (
            "system",
            LOCKED_GROUNDING_PROMPT
            + "\n\nWrite only a concise Markdown coverage addendum for the "
            "missing source sections. Do not rewrite the existing answer, "
            "include a references section, or discuss validation. Introductory "
            "and heading-only nodes may have substantive descendant sections. "
            "Check the complete source and existing answer before claiming "
            "that details or results are absent; cite the descendant evidence "
            "as well as the missing overview node for technical facts.",
        ),
        (
            "human",
            f"""Chapter: {scope.display_path}

The existing answer is citation-safe but needs coverage from:
{coverage}

Existing answer (for continuity, not evidence):
{result.text}

Return only additional interview-relevant points supported by the evidence
below. Cite every paragraph or bullet, and use every listed node at least once.

Complete canonical source, including descendant sections:
{context.text}""".strip(),
        ),
    ]
    budget = prompt_budget(messages, config=config)
    if not budget.fits:
        raise ContextWindowExceededError(budget)
    response = invoke_with_streaming(model, messages, token_callback=None)
    addendum = normalize_citation_syntax(_response_text(response))
    combined = (
        result.text.rstrip() + "\n\n## Additional interview points\n\n" + addendum
    )
    return SummaryResult(
        text=combined,
        validation=validate_summary(
            combined,
            scope=scope,
            context=context,
            response_depth=response_depth,
        ),
        budget=budget,
        finish_reason=_finish_reason(response),
    )
