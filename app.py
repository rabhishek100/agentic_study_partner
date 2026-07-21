"""Minimal Gradio chat for grounded book questions."""

import gradio as gr

from study.analyze import ConversationDecisionError
from study.contracts import ConversationState
from study.conversation import execute_conversation_turn
from study.query import QueryExecutionError
from study.scope import ScopeResolutionError
from study.summarize import ContextWindowExceededError


def _conversation_state(
    value: dict | None,
) -> ConversationState | None:
    return ConversationState.model_validate(value) if value else None


def _diagnostics(
    result,
    state: ConversationState,
) -> str:
    scope = (
        state.active_scope.display_path
        if state.active_scope
        else "No persistent scope"
    )
    query = result.standalone_query or "Not applicable"
    return (
        f"**Route:** `{result.route}`  \n"
        f"**Standalone query:** {query}  \n"
        f"**Active scope:** {scope}  \n"
        f"**Retrieval:** `{result.retrieval_mode or 'not used'}`"
    )


def respond_conversation(
    message: str,
    history: list,
    retrieval_mode: str,
    book_id: float | None,
    state_data: dict | None,
) -> tuple[str, dict | None, str]:
    del history
    selected_book_id = int(book_id) if book_id is not None else None
    state = _conversation_state(state_data)
    try:
        result, updated = execute_conversation_turn(
            message,
            state,
            retrieval_mode=retrieval_mode,
            book_id=selected_book_id,
        )
        return (
            result.answer,
            updated.model_dump(mode="json"),
            _diagnostics(result, updated),
        )
    except (
        ContextWindowExceededError,
        QueryExecutionError,
        ScopeResolutionError,
        ConversationDecisionError,
    ) as error:
        return (
            f"**Unable to complete request:** {error}",
            state_data,
            "**Turn failed before conversation state was updated.**",
        )


def _submit(
    message: str,
    history: list[dict] | None,
    retrieval_mode: str,
    book_id: float | None,
    state_data: dict | None,
):
    if not message.strip():
        return "", history or [], state_data, ""
    answer, updated_state, diagnostics = respond_conversation(
        message,
        history or [],
        retrieval_mode,
        book_id,
        state_data,
    )
    updated_history = list(history or [])
    updated_history.extend(
        [
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ]
    )
    return "", updated_history, updated_state, diagnostics


def _clear():
    return "", [], None, "Conversation state cleared."


with gr.Blocks(title="Agentic Study Partner") as demo:
    conversation_state = gr.State(None)
    gr.Markdown(
        "# Agentic Study Partner\n"
        "Ask grounded questions, summarize a chapter or section, and continue "
        "with follow-up questions."
    )
    with gr.Row():
        retrieval = gr.Dropdown(
            choices=["hybrid", "hybrid_rerank", "bm25", "vector"],
            value="hybrid",
            label="Retrieval mode",
        )
        selected_book = gr.Number(
            value=1,
            precision=0,
            label="Book ID",
            info="Changing books starts a new internal conversation.",
        )
    chatbot = gr.Chatbot(
        height=560,
        placeholder=(
            "Try “Summarize Chapter 3,” then "
            "“Compare the three dataflow modes.”"
        ),
    )
    message = gr.Textbox(
        placeholder="Ask about the book…",
        label="Message",
        autofocus=True,
    )
    with gr.Row():
        send = gr.Button("Send", variant="primary")
        clear = gr.Button("Clear conversation")
    with gr.Accordion("Turn diagnostics", open=False):
        diagnostics = gr.Markdown("No turns yet.")

    inputs = [
        message,
        chatbot,
        retrieval,
        selected_book,
        conversation_state,
    ]
    outputs = [
        message,
        chatbot,
        conversation_state,
        diagnostics,
    ]
    send.click(_submit, inputs=inputs, outputs=outputs)
    message.submit(_submit, inputs=inputs, outputs=outputs)
    clear.click(_clear, outputs=outputs)


if __name__ == "__main__":
    demo.launch()
