"""One-call grounded summarization for a complete resolved scope."""

from dataclasses import dataclass, replace
import logging
import re
from typing import Protocol

import tiktoken

from .context import DEFAULT_ENCODING, ScopeContext
from .scope import ResolvedScope
from .streaming import TokenCallback, invoke_with_streaming


CITATION = re.compile(r"\[N(\d+):P(\d+)]")
GROUPED_CITATION = re.compile(
    r"\[((?:N\d+:P\d+)(?:\s*;\s*N\d+:P\d+)+)]"
)
OPTIONAL_RECAP_TITLES = frozenset({"summary", "conclusion"})
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
            raise ValueError(
                "output and safety budgets must leave room for input"
            )


@dataclass(frozen=True)
class PromptBudget:
    input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    context_window_tokens: int

    @property
    def required_tokens(self) -> int:
        return (
            self.input_tokens
            + self.max_output_tokens
            + self.safety_margin_tokens
        )

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


@dataclass(frozen=True)
class SummaryResult:
    text: str
    validation: SummaryValidation
    budget: PromptBudget
    finish_reason: str | None
    attempt_count: int = 1
    initial_errors: tuple[str, ...] = ()


def build_summary_messages(
    scope: ResolvedScope,
    context: ScopeContext,
    *,
    validation_feedback: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Build the grounded prompt over the complete formatted scope."""

    allowed_pages_by_node: dict[int, list[int]] = {}
    for node_id, page in sorted(context.allowed_citations):
        allowed_pages_by_node.setdefault(node_id, []).append(page)
    required_sections = "\n".join(
        f"- Node {node.id}: {node.path_text}; allowed citations: "
        + ", ".join(
            f"[N{node.id}:P{page}]"
            for page in allowed_pages_by_node[node.id]
        )
        for node in scope.nodes
        if node.id in context.expected_node_ids
    )
    system = (
        "You summarize technical-book evidence. Use only the supplied "
        "evidence. Do not use outside knowledge or infer the contents of "
        "omitted images. Cite every substantive claim using [N<node>:P<page>] "
        "from the supplied block markers. Copy citations exactly from the "
        "allowed citations listed for that node; never combine a node ID with "
        "a page that is not in its allowed list. Preserve uncertainty when "
        "evidence is incomplete. Complete coverage is more important than detail: "
        "cite every node in Required coverage at least once, proceed in the "
        "given order, and keep early sections concise so later sections are "
        "not omitted. Do not add a references or sources section; the "
        "application appends exact source metadata after validation."
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
    human = f"""
Summarize this complete {scope.kind} scope:

Book: {scope.book_title}
Scope: {scope.display_path}
PDF pages: {scope.start_page}–{scope.end_page}

Required coverage:
{required_sections}

Return Markdown with:
1. A title matching the scope.
2. A concise overview.
3. A concise section-by-section summary covering every required node in the
   listed order, with at least one valid citation from each node.
4. Key concepts and definitions.
5. Important examples, comparisons, and tables.
6. Main takeaways.

If space becomes limited, shorten items 2, 4, 5, and 6 before omitting any
required node from item 3.

Use citations such as [N14:P21]. Do not cite the block suffix.
{correction}

Complete evidence:

{context.text}
""".strip()
    return [("system", system), ("human", human)]


def prompt_budget(
    messages: list[tuple[str, str]],
    *,
    config: SummaryConfig,
) -> PromptBudget:
    """Estimate the complete request conservatively with explicit reserves."""

    encoding = tiktoken.get_encoding(config.encoding_name)
    input_tokens = sum(
        len(encoding.encode(role)) + len(encoding.encode(content)) + 4
        for role, content in messages
    ) + 4
    return PromptBudget(
        input_tokens=input_tokens,
        max_output_tokens=config.max_output_tokens,
        safety_margin_tokens=config.safety_margin_tokens,
        context_window_tokens=config.context_window_tokens,
    )


def validate_summary(
    text: str,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
) -> SummaryValidation:
    """Reject invented citations and report uncovered content-bearing nodes."""

    errors: list[str] = []
    warnings: list[str] = []
    citations = [
        (int(node_id), int(page))
        for node_id, page in CITATION.findall(text)
    ]
    valid_citations = {
        citation
        for citation in citations
        if citation in context.allowed_citations
    }
    invalid_citations = sorted(
        set(citations).difference(context.allowed_citations)
    )
    if not citations:
        errors.append("summary contains no citations")
    if invalid_citations:
        errors.append(
            "summary contains out-of-scope citations: "
            + ", ".join(
                f"[N{node_id}:P{page}]"
                for node_id, page in invalid_citations
            )
        )

    cited_nodes = frozenset(node_id for node_id, _ in valid_citations)
    missing_nodes = context.expected_node_ids.difference(cited_nodes)
    nodes = {node.id: node for node in scope.nodes}
    recap_nodes = {
        node_id
        for node_id in missing_nodes
        if nodes[node_id].title.casefold().strip() in OPTIONAL_RECAP_TITLES
    }
    required_missing_nodes = missing_nodes.difference(recap_nodes)
    if required_missing_nodes:
        errors.append(
            "summary does not cite content from required nodes: "
            + "; ".join(
                f"{node_id} ({nodes[node_id].path_text})"
                for node_id in sorted(required_missing_nodes)
            )
        )
    if recap_nodes:
        warnings.append(
            "summary does not cite optional recap nodes: "
            + "; ".join(
                f"{node_id} ({nodes[node_id].path_text})"
                for node_id in sorted(recap_nodes)
            )
        )
    return SummaryValidation(
        valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        cited_node_ids=cited_nodes,
        missing_node_ids=frozenset(missing_nodes),
    )


def normalize_citation_syntax(text: str) -> str:
    """Split grouped valid marker syntax without changing citation values."""

    return GROUPED_CITATION.sub(
        lambda match: " ".join(
            f"[{marker.strip()}]"
            for marker in match.group(1).split(";")
        ),
        text,
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
        references.append(
            f"- [N{node_id}:P{page}] {scope.book_title} → "
            f"{hierarchy} — PDF p. {page}"
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


def summarize_scope(
    model: SummaryModel,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    config: SummaryConfig | None = None,
    validation_feedback: tuple[str, ...] = (),
    token_callback: TokenCallback | None = None,
) -> SummaryResult:
    """Make one complete-scope call and validate the returned citations."""

    config = config or SummaryConfig()
    messages = build_summary_messages(
        scope,
        context,
        validation_feedback=validation_feedback,
    )
    budget = prompt_budget(messages, config=config)
    if not budget.fits:
        raise ContextWindowExceededError(budget)
    response = invoke_with_streaming(
        model, messages, token_callback=token_callback
    )
    text = normalize_citation_syntax(_response_text(response))
    return SummaryResult(
        text=text,
        validation=validate_summary(
            text,
            scope=scope,
            context=context,
        ),
        budget=budget,
        finish_reason=_finish_reason(response),
    )


def summarize_scope_with_repair(
    model: SummaryModel,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    config: SummaryConfig | None = None,
) -> SummaryResult:
    """Use complete provider responses and repair only genuine invalid drafts.

    Summary streaming is intentionally disabled here. Some OpenRouter provider
    streams have ended without a terminal finish reason, yielding a plausible
    but truncated early-chapter draft. A normal invoke returns the complete
    response before deterministic coverage and citation validation runs.
    """

    first = summarize_scope(
        model,
        scope=scope,
        context=context,
        config=config,
        token_callback=None,
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
    repaired = summarize_scope(
        model,
        scope=scope,
        context=context,
        config=config,
        validation_feedback=first.validation.errors,
        token_callback=None,
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
    final = summarize_scope(
        model,
        scope=scope,
        context=context,
        config=config,
        validation_feedback=repaired.validation.errors,
        token_callback=None,
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
    return replace(
        final,
        attempt_count=3,
        initial_errors=first.validation.errors,
    )
