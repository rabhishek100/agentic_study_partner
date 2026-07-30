"""Run deterministic hierarchy requests and complete-scope summaries."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from storage.database import (
    connection as database_connection,
)
from storage.database import (
    environment_owner_id,
    parse_owner_id,
)
from study.content import load_scope_content
from study.context import ScopeContext, build_scope_context
from study.query import DEFAULT_GENERATION_MODEL
from study.render import format_chapter_list, format_outline
from study.request import (
    StudyRequest,
    UnsupportedStudyRequestError,
    parse_study_request,
    resolve_study_request,
)
from study.scope import ResolvedScope, ScopeResolutionError
from study.summarize import (
    ContextWindowExceededError,
    SummaryConfig,
    append_references,
    build_summary_messages,
    prompt_budget,
    summarize_scope_with_repair,
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Map an explicit study query to canonical Postgres hierarchy and "
            "optionally summarize the complete scope."
        )
    )
    parser.add_argument("query", help="Explicit chapter or section request")
    parser.add_argument(
        "--database-url",
        help="Postgres URL; defaults to DATABASE_URL",
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument(
        "--owner-id",
        help="Owner UUID; defaults to DEFAULT_OWNER_ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and measure context without calling the model",
    )
    parser.add_argument(
        "--dump-context",
        type=Path,
        help="Write the complete formatted LLM context for inspection",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write a validated Markdown summary instead of printing it",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_GENERATION_MODEL") or DEFAULT_GENERATION_MODEL,
    )
    parser.add_argument(
        "--context-window",
        type=int,
        default=int(os.getenv("SUMMARY_CONTEXT_WINDOW_TOKENS", "64000")),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(os.getenv("SUMMARY_MAX_OUTPUT_TOKENS", "8000")),
    )
    parser.add_argument(
        "--safety-margin-tokens",
        type=int,
        default=int(os.getenv("SUMMARY_SAFETY_MARGIN_TOKENS", "1000")),
    )
    return parser


def format_dry_run(
    request: StudyRequest,
    scope: ResolvedScope,
    context: ScopeContext,
    *,
    config: SummaryConfig,
) -> str:
    """Render the exact mapping, inclusion counts, and prompt budget."""

    messages = build_summary_messages(scope, context)
    budget = prompt_budget(messages, config=config)
    node_ids = ", ".join(str(node.id) for node in scope.nodes)
    return "\n".join(
        [
            f"Intent: {request.intent}",
            f"Book: {scope.book_title} (ID {scope.book_id})",
            f"Scope: {scope.kind} — {scope.display_path}",
            f"Node IDs: {node_ids}",
            f"PDF pages: {scope.start_page}–{scope.end_page}",
            f"Included blocks: {context.included_block_count}",
            f"Skipped blocks: {context.skipped_block_count}",
            f"Tables: {context.table_count}",
            f"Images represented as placeholders: {context.image_count}",
            f"Formatted evidence tokens: {context.token_count}",
            f"Complete prompt tokens: {budget.input_tokens}",
            f"Output reserve: {budget.max_output_tokens}",
            f"Safety reserve: {budget.safety_margin_tokens}",
            f"Configured context window: {budget.context_window_tokens}",
            f"Required total: {budget.required_tokens}",
            f"Fits without truncation: {'yes' if budget.fits else 'no'}",
        ]
    )


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def main() -> None:
    load_dotenv()
    parser = build_argument_parser()
    args = parser.parse_args()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    try:
        request = parse_study_request(args.query)
        with database_connection(args.database_url, readonly=True) as connection:
            scope = resolve_study_request(
                connection,
                request,
                owner_id=owner_id,
                book_id=args.book_id,
            )
            if request.intent in {"list_chapters", "list_sections"}:
                print(
                    format_chapter_list(scope)
                    if request.intent == "list_chapters"
                    else format_outline(scope)
                )
                return
            evidence = load_scope_content(connection, scope, owner_id=owner_id)
        context = build_scope_context(evidence)
        config = SummaryConfig(
            context_window_tokens=args.context_window,
            max_output_tokens=args.max_output_tokens,
            safety_margin_tokens=args.safety_margin_tokens,
        )
        messages = build_summary_messages(scope, context)
        budget = prompt_budget(messages, config=config)
        if not budget.fits:
            raise ContextWindowExceededError(budget)
        if args.dump_context:
            _write(args.dump_context, context.text)
            print(f"Wrote complete context to {args.dump_context}")
        if args.dry_run:
            print(format_dry_run(request, scope, context, config=config))
            return

        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            parser.error("OPENROUTER_API_KEY is missing from .env")
        print(
            f"Resolved {scope.display_path} to {len(scope.nodes)} nodes; "
            f"loading the LangChain model client for {args.model}...",
            file=sys.stderr,
            flush=True,
        )
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=args.model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            max_tokens=config.max_output_tokens,
            timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
            extra_body={
                "reasoning": {
                    "effort": os.getenv(
                        "OPENROUTER_GENERATION_REASONING",
                        "none",
                    ),
                    "exclude": True,
                }
            },
        )
        print(
            f"Sending {budget.input_tokens} prompt tokens to OpenRouter; "
            "waiting for the summary...",
            file=sys.stderr,
            flush=True,
        )
        result = summarize_scope_with_repair(
            model,
            scope=scope,
            context=context,
            config=config,
        )
    except (
        ContextWindowExceededError,
        ScopeResolutionError,
        UnsupportedStudyRequestError,
    ) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print(
            "\nSummary cancelled before writing a new validated output.",
            file=sys.stderr,
        )
        raise SystemExit(130) from None

    if not result.validation.valid:
        print(result.text)
        finish_reason = (
            f" (model finish reason: {result.finish_reason})"
            if result.finish_reason
            else ""
        )
        print(
            f"\nSummary validation failed{finish_reason}:",
            file=sys.stderr,
        )
        for error in result.validation.errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    if result.validation.warnings:
        print("Summary validation warnings:", file=sys.stderr)
        for warning in result.validation.warnings:
            print(f"- {warning}", file=sys.stderr)
    rendered_summary = append_references(result.text, scope=scope)
    if args.output:
        _write(args.output, rendered_summary + "\n")
        print(f"Wrote validated summary to {args.output}")
    else:
        print(rendered_summary)


if __name__ == "__main__":
    main()
