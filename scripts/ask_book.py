import argparse
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from retrieval.langchain import BookRetriever
from retrieval.search import RetrievalMode
from retrieval.sqlite import connect_source


def answer_question(
    question: str,
    database_path: str = "data/retrieval.sqlite3",
    source_path: str = "data/books.sqlite3",
    chroma_path: str = "data/chroma",
    book_id: int | None = None,
    retrieval_mode: RetrievalMode = "hybrid",
) -> str:
    load_dotenv()
    with connect_source(source_path) as source:
        if book_id is None:
            rows = source.execute("SELECT id, title FROM books").fetchall()
        else:
            rows = source.execute(
                "SELECT id, title FROM books WHERE id = ?",
                (book_id,),
            ).fetchall()
        books = {row["id"]: row["title"] for row in rows}
    documents = BookRetriever(
        database_path=database_path,
        chroma_path=chroma_path,
        mode=retrieval_mode,
        book_id=book_id,
        k=5,
    ).invoke(question)
    if not documents:
        return "I could not find relevant evidence in the indexed books."

    evidence = "\n\n".join(
        f"[S{i}] {document.metadata['path']} "
        f"(PDF pp. {document.metadata['start_page']}–"
        f"{document.metadata['end_page']})\n{document.page_content}"
        for i, document in enumerate(documents, 1)
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
    for i, document in enumerate(documents, 1):
        metadata = document.metadata
        hierarchy = metadata["path"].replace(" :: ", " → ")
        pages = str(metadata["start_page"])
        if metadata["end_page"] != metadata["start_page"]:
            pages += f"–{metadata['end_page']}"
        book = books.get(metadata["book_id"], f"Book {metadata['book_id']}")
        sources.append(f"- **[S{i}]** {book} → {hierarchy} — PDF p. {pages}")
    return (
        f"{reply.content}\n\n"
        f"_Retrieval: {retrieval_mode}_\n\n"
        + "\n".join(sources)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument(
        "--retrieval-mode",
        choices=("bm25", "vector", "hybrid"),
        default="hybrid",
    )
    parser.add_argument("--book-id", type=int)
    parser.add_argument("--chroma-path", default="data/chroma")
    args = parser.parse_args()
    print(
        answer_question(
            args.question,
            chroma_path=args.chroma_path,
            book_id=args.book_id,
            retrieval_mode=args.retrieval_mode,
        )
    )


if __name__ == "__main__":
    main()
