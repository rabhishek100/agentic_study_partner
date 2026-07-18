"""Minimal Gradio chat for grounded book questions."""

import gradio as gr

from scripts.ask_book import answer_question


def respond(message: str, history: list) -> str:
    del history
    return answer_question(message)


demo = gr.ChatInterface(
    fn=respond,
    title="Agentic Study Partner",
    description="Ask a question about Designing Machine Learning Systems.",
    examples=[
        "How does reservoir sampling work?",
        "What is data leakage and how can it be prevented?",
        "Compare batch prediction with online prediction.",
    ],
)


if __name__ == "__main__":
    demo.launch()
