"""Deterministic hierarchy and content access for study workflows."""

from .content import ContentBlock, EvidenceBundle, NodeContent, load_scope_content
from .scope import (
    AmbiguousScopeError,
    ResolvedScope,
    ScopeCandidate,
    ScopeNode,
    ScopeNotFoundError,
    ScopeResolutionError,
    list_chapters,
    resolve_book,
    resolve_chapter,
    resolve_named_scope,
    resolve_section,
)
from .request import (
    StudyRequest,
    UnsupportedStudyRequestError,
    parse_study_request,
    resolve_study_request,
)
from .context import ScopeContext, build_scope_context
from .summarize import (
    ContextWindowExceededError,
    PromptBudget,
    SummaryConfig,
    SummaryResult,
    SummaryValidation,
    build_summary_messages,
    prompt_budget,
    summarize_scope,
    validate_summary,
)

__all__ = [
    "AmbiguousScopeError",
    "ContentBlock",
    "ContextWindowExceededError",
    "EvidenceBundle",
    "NodeContent",
    "PromptBudget",
    "ResolvedScope",
    "ScopeCandidate",
    "ScopeNode",
    "ScopeNotFoundError",
    "ScopeResolutionError",
    "ScopeContext",
    "SummaryConfig",
    "SummaryResult",
    "SummaryValidation",
    "StudyRequest",
    "UnsupportedStudyRequestError",
    "list_chapters",
    "load_scope_content",
    "parse_study_request",
    "build_scope_context",
    "build_summary_messages",
    "prompt_budget",
    "resolve_book",
    "resolve_chapter",
    "resolve_named_scope",
    "resolve_section",
    "resolve_study_request",
    "summarize_scope",
    "validate_summary",
]
