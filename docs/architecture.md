# Architecture

The system keeps extracted book content separate from anything that can be rebuilt. The study workflow chooses between loading a complete chapter or searching small chunks, then returns an answer only with evidence from the book.

```mermaid
flowchart LR
    PDF[Book PDF] --> Parse[PDF parser]
    Parse --> Canonical[(Canonical SQLite<br/>books, sections, content)]

    Canonical --> Chunk[Chunk builder]
    Chunk --> BM25[(SQLite FTS5<br/>keyword index)]
    Chunk --> Vector[(Chroma<br/>vector index)]

    Next[Next.js React client] --> API[FastAPI]
    Gradio[Optional Gradio UI] --> Turn[Conversation entry point]
    API --> Turn
    Turn --> Graph[LangGraph study turn]
    Graph --> Plan[Understand request]
    Plan --> Scope[Complete chapter or section]
    Plan --> Search[Book search]
    Scope --> Canonical
    Search --> BM25
    Search --> Vector
    Scope --> Answer[Grounded answer or summary]
    Search --> Answer
    Answer --> Check[Evidence and citation checks]
    Check --> Turn
    Turn --> API

    Graph -. trace .-> LangSmith[LangSmith]
    Answer -. trace .-> LangSmith
```

## Important boundaries

- `data/books.sqlite3` is the lossless book record after ingestion. It contains the book structure and ordered content, not summaries or search indexes.
- `data/retrieval.sqlite3` and `data/chroma/` are generated search data. They can be deleted and rebuilt from the canonical database.
- Complete chapter and section summaries load the whole selected section tree. They do not rely on a small set of search results.
- Ordinary questions search chunks, attach their exact source pages, and return “insufficient evidence” when the book does not support an answer.
- The Next.js and Gradio interfaces call the same conversation workflow. They do not contain separate answering logic.
