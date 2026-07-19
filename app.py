"""Minimal Gradio chat for grounded book questions."""

import gradio as gr

from scripts.ask_book import answer_question


def respond(message: str, history: list, retrieval_mode: str) -> str:
    del history
    return answer_question(message, retrieval_mode=retrieval_mode)


demo = gr.ChatInterface(
    fn=respond,
    title="Agentic Study Partner",
    description="Ask a question about Designing Machine Learning Systems.",
    additional_inputs=[
        gr.Dropdown(
            choices=["hybrid", "hybrid_rerank", "bm25", "vector"],
            value="hybrid",
            label="Retrieval mode",
        )
    ],
    examples=[
        ["How does reservoir sampling work?", "hybrid"],
        ["What is data leakage and how can it be prevented?", "hybrid"],
        ["Compare batch prediction with online prediction.", "hybrid"],
    ],
)


if __name__ == "__main__":
    demo.launch()
