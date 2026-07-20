"""One-call grounded summarization for a complete resolved scope."""

from dataclasses import dataclass
import re
from typing import Protocol

import tiktoken

from .context import DEFAULT_ENCODING, ScopeContext
from .scope import ResolvedScope


CITATION = re.compile(r"\[N(\d+):P(\d+)]")
OPTIONAL_RECAP_TITLES = frozenset({"summary", "conclusion"})


class SummaryModel(Protocol):
    """Minimal LangChain-compatible model contract used by tests and runtime."""

    def invoke(self, messages: list[tuple[str, str]]): ...


@dataclass(frozen=True)
class SummaryConfig:
    """Explicit prompt budget; complete inputs are never silently truncated."""

    context_window_tokens: int = 64_000
    max_output_tokens: int = 4_000
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


def build_summary_messages(
    scope: ResolvedScope,
    context: ScopeContext,
) -> list[tuple[str, str]]:
    """Build the grounded prompt over the complete formatted scope."""

    required_sections = "\n".join(
        f"- Node {node.id}: {node.path_text}"
        for node in scope.nodes
        if node.id in context.expected_node_ids
    )
    system = (
        "You summarize technical-book evidence. Use only the supplied "
        "evidence. Do not use outside knowledge or infer the contents of "
        "omitted images. Cite every substantive claim using [N<node>:P<page>] "
        "from the supplied block markers. Preserve uncertainty when evidence "
        "is incomplete. Do not add a references or sources section; the "
        "application appends exact source metadata after validation."
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
2. An overview.
3. A section-by-section summary covering every required node.
4. Key concepts and definitions.
5. Important examples, comparisons, and tables.
6. Main takeaways.

Use citations such as [N14:P21]. Do not cite the block suffix.

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


def summarize_scope(
    model: SummaryModel,
    *,
    scope: ResolvedScope,
    context: ScopeContext,
    config: SummaryConfig | None = None,
) -> SummaryResult:
    """Make one complete-scope call and validate the returned citations."""

    config = config or SummaryConfig()
    messages = build_summary_messages(scope, context)
    budget = prompt_budget(messages, config=config)
    if not budget.fits:
        raise ContextWindowExceededError(budget)
    text = _response_text(model.invoke(messages))
    return SummaryResult(
        text=text,
        validation=validate_summary(
            text,
            scope=scope,
            context=context,
        ),
        budget=budget,
    )
