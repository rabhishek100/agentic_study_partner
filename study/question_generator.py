"""Dynamic question generator for book and video chat starter prompts."""

import json
import logging
import os
from typing import Any
from uuid import UUID

from psycopg import Connection

from storage.database import parse_owner_id

logger = logging.getLogger(__name__)

DEFAULT_GENERATION_MODEL = "anthropic/claude-3.5-sonnet"


def _deterministic_book_questions(
    books_data: list[dict[str, Any]],
) -> list[str]:
    """Generate clean fallback starter questions when LLM generation is unavailable."""
    if not books_data:
        return [
            "What topics are covered in the library?",
            "Summarize the main concepts in these books.",
            "What are the core technical trade-offs discussed?",
            "Explain the primary architecture patterns.",
            "Generate interview questions based on the available material.",
        ]

    if len(books_data) == 1:
        book = books_data[0]
        title = book["title"]
        chapters = book.get("chapters", [])
        questions = []
        if chapters:
            c1 = chapters[0]
            questions.append(f"What sections are present in {c1}?")
            questions.append(f"Summarize {c1} of {title}.")
            if len(chapters) > 1:
                questions.append(f"What are the key takeaways from {chapters[1]}?")
            if len(chapters) > 2:
                questions.append(f"How does {chapters[2]} relate to {c1}?")
        
        while len(questions) < 5:
            fallbacks = [
                f"What are the primary concepts introduced in {title}?",
                f"Summarize the main architecture discussed in {title}.",
                f"What key system design trade-offs does {title} highlight?",
                f"What causes training-serving skew or architectural drift according to {title}?",
                f"Generate 3 technical interview questions based on {title}.",
            ]
            for f in fallbacks:
                if f not in questions and len(questions) < 5:
                    questions.append(f)
        return questions[:5]

    # Multiple books scope
    titles = [b["title"] for b in books_data[:3]]
    joined_titles = ", ".join(titles)
    return [
        f"What overarching themes unite {joined_titles}?",
        "Compare the core architectural approaches between these selected books.",
        "What are the most critical technical trade-offs covered across these sources?",
        "Summarize the key concepts across all selected books.",
        "Generate cross-book technical interview questions based on this library scope.",
    ]


def _deterministic_video_questions(
    video_title: str,
    chapters: list[str],
) -> list[str]:
    """Generate clean fallback starter questions for a video lecture."""
    questions = [
        f"Summarize the lecture '{video_title}'.",
        f"List the core topics and sections covered in '{video_title}'.",
    ]
    if chapters:
        mid_chapter = chapters[len(chapters) // 2]
        questions.append(f"Explain {mid_chapter}.")
        if len(chapters) > 1 and chapters[0] != mid_chapter:
            questions.append(f"What key concepts are introduced in {chapters[0]}?")

    fallbacks = [
        "What was drawn or written on the board during this lecture?",
        f"What are the main takeaways from '{video_title}'?",
        "Generate 3 review questions to test my understanding of this lecture.",
    ]
    for f in fallbacks:
        if f not in questions and len(questions) < 5:
            questions.append(f)
    return questions[:5]


def _call_llm_for_questions(system_prompt: str, user_prompt: str) -> list[str] | None:
    """Invoke OpenRouter LLM to return exactly 5 dynamic questions."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    try:
        from langchain_openai import ChatOpenAI

        model_name = os.getenv("OPENROUTER_GENERATION_MODEL") or DEFAULT_GENERATION_MODEL
        llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "1")),
            timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "15")),
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        res = llm.invoke(messages)
        content = res.content.strip()

        # Parse JSON output from model response
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        data = json.loads(content)
        if isinstance(data, list) and len(data) >= 3:
            result = [str(q).strip() for q in data if isinstance(q, (str, int, float)) and str(q).strip()]
            if len(result) >= 3:
                return result[:5]
    except Exception as exc:
        logger.warning("LLM dynamic question generation failed: %s", exc)
    return None


def generate_book_questions(
    connection: Connection,
    *,
    owner_id: str | UUID,
    book_ids: list[int] | None = None,
) -> list[str]:
    """Generate 5 dynamic starter questions for the given book selection."""
    owner = parse_owner_id(owner_id)

    # 1. Fetch book titles and chapter nodes
    if book_ids:
        rows = connection.execute(
            """
            select id, title, author
            from public.books
            where owner_id = %s and id = any(%s)
            order by title
            """,
            (owner, book_ids),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            select id, title, author
            from public.books
            where owner_id = %s
            order by title
            """,
            (owner,),
        ).fetchall()

    if not rows:
        return _deterministic_book_questions([])

    books_data = []
    for r in rows:
        b_id = r["id"]
        nodes = connection.execute(
            """
            select title
            from public.nodes
            where owner_id = %s and book_id = %s and node_type = 'chapter'
            order by id
            limit 10
            """,
            (owner, b_id),
        ).fetchall()
        chapter_titles = [n["title"] for n in nodes]
        books_data.append(
            {
                "id": b_id,
                "title": r["title"],
                "author": r["author"] or "Unknown",
                "chapters": chapter_titles,
            }
        )

    # 2. Try LLM generation
    system_prompt = (
        "You are an AI study partner and technical interviewer. "
        "Generate exactly 5 engaging, distinct, highly relevant study/interview questions based on the user's technical book scope. "
        "Format your output strictly as a JSON array of strings, e.g.: "
        '["Question 1", "Question 2", "Question 3", "Question 4", "Question 5"]. '
        "Do NOT include any additional conversational text or markdown explanation."
    )

    scope_desc = "\n".join(
        f"- Book: '{b['title']}' by {b['author']}. Chapters: {', '.join(b['chapters']) if b['chapters'] else 'N/A'}"
        for b in books_data
    )
    user_prompt = f"Please generate 5 dynamic starter questions for the following study materials:\n{scope_desc}"

    llm_questions = _call_llm_for_questions(system_prompt, user_prompt)
    if llm_questions and len(llm_questions) == 5:
        return llm_questions

    # 3. Deterministic fallback
    return _deterministic_book_questions(books_data)


def generate_video_questions(
    connection: Connection,
    *,
    owner_id: str | UUID,
    video_id: str | UUID,
) -> list[str]:
    """Generate 5 dynamic starter questions for a video lecture."""
    owner = parse_owner_id(owner_id)

    # 1. Fetch video and chapter metadata
    v_row = connection.execute(
        """
        select title
        from video.videos
        where owner_id = %s and id = %s
        """,
        (owner, UUID(str(video_id))),
    ).fetchone()

    video_title = v_row["title"] if v_row else "Lecture"

    c_rows = connection.execute(
        """
        select title
        from video.chapters
        where owner_id = %s and video_id = %s
        order by start_time_seconds
        """,
        (owner, UUID(str(video_id))),
    ).fetchall()

    chapters = [c["title"] for c in c_rows if c.get("title")]

    # 2. Try LLM generation
    system_prompt = (
        "You are an AI study partner specializing in technical lecture videos. "
        "Generate exactly 5 engaging, distinct, highly relevant questions for a student watching this video. "
        "Include a mix of high-level overview, specific chapter concepts, and board/visual inquiry questions. "
        "Format your output strictly as a JSON array of 5 strings: [\"Q1\", \"Q2\", \"Q3\", \"Q4\", \"Q5\"]. "
        "Do NOT include any extra text."
    )

    user_prompt = (
        f"Video Title: '{video_title}'\n"
        f"Chapters:\n" + "\n".join(f"- {c}" for c in chapters) if chapters else "No chapters listed."
    )

    llm_questions = _call_llm_for_questions(system_prompt, user_prompt)
    if llm_questions and len(llm_questions) == 5:
        return llm_questions

    # 3. Fallback
    return _deterministic_video_questions(video_title, chapters)
