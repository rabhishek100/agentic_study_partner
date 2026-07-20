"""Minimal Gradio chat for grounded book questions."""

import gradio as gr

from study.query import (
    QueryExecutionError,
    answer_query,
)
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError


def respond(
    message: str,
    history: list,
    retrieval_mode: str,
    book_id: float | None,
) -> str:
    del history
    selected_book_id = int(book_id) if book_id is not None else None
    try:
        return answer_query(
            message,
            retrieval_mode=retrieval_mode,
            book_id=selected_book_id,
        )
    except (
        ContextWindowExceededError,
        QueryExecutionError,
        ScopeResolutionError,
    ) as error:
        return f"**Unable to complete request:** {error}"


demo = gr.ChatInterface(
    fn=respond,
    title="Agentic Study Partner",
    description="Ask a question about Designing Machine Learning Systems.",
    additional_inputs=[
        gr.Dropdown(
            choices=["hybrid", "hybrid_rerank", "bm25", "vector"],
            value="hybrid",
            label="Retrieval mode",
        ),
        gr.Number(
            value=None,
            precision=0,
            label="Book ID (optional)",
            info="Use this to disambiguate chapter or section requests.",
        ),
    ],
    examples=[
        ["Summarize Chapter 1", "hybrid", 1],
        ["What sections are present in Chapter 1?", "hybrid", 1],
        ["How does reservoir sampling work?", "hybrid", None],
        ["What is data leakage and how can it be prevented?", "hybrid", None],
    ],
)


if __name__ == "__main__":
    demo.launch()
