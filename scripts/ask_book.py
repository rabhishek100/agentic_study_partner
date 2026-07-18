import argparse
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from retrieval.sqlite import connect, connect_source, search


def answer_question(
    question: str,
    database_path: str = "data/retrieval.sqlite3",
    source_path: str = "data/books.sqlite3",
    book_id: int = 1,
) -> str:
    load_dotenv()
    with connect_source(source_path) as source:
        row = source.execute("SELECT title FROM books WHERE id = ?", (book_id,))
        book = row.fetchone()["title"]
    with connect(database_path) as database:
        hits = search(database, question, book_id=book_id, limit=5, unique_nodes=True)
    evidence = "\n\n".join(
        f"[S{i}] {hit.path_text} (PDF pp. {hit.start_page}–{hit.end_page})\n{hit.text}"
        for i, hit in enumerate(hits, 1)
    )
    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_MODEL", "openai/gpt-5.6-luna"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    rules = "Answer only from the evidence. Cite claims with [S1], [S2], etc. If evidence is insufficient, say so."
    reply = model.invoke(
        [("system", rules), ("human", f"Question: {question}\n\n{evidence}")]
    )
    sources = ["### Sources"]
    for i, hit in enumerate(hits, 1):
        hierarchy = hit.path_text.replace(" :: ", " → ")
        pages = str(hit.start_page)
        pages += f"–{hit.end_page}" if hit.end_page != hit.start_page else ""
        sources.append(f"- **[S{i}]** {book} → {hierarchy} — PDF p. {pages}")
    return f"{reply.content}\n\n" + "\n".join(sources)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    args = parser.parse_args()
    print(answer_question(args.question))


if __name__ == "__main__":
    main()
