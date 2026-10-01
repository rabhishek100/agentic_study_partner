"""Dynamic question generator for book and video chat starter prompts."""


import json
import logging
import os
import re
from typing import Any
from uuid import UUID

from psycopg import Connection

from observability import traced
from storage.database import parse_owner_id

logger = logging.getLogger(__name__)

DEFAULT_GENERATION_MODEL = "anthropic/claude-3.5-sonnet"
MAX_QUESTION_WORDS = 12
MAX_QUESTION_CHARACTERS = 90

_NUMBERING = re.compile(
    r"^\s*(?:[-*•]\s*|(?:q(?:uestion)?\s*)?\d+[.):]\s*)",
    re.I,
)
_WORDS = re.compile(r"[\w]+(?:[-'][\w]+)*")
_SIMPLE_OPENERS = (
    "what ",
    "why ",
    "how ",
    "when ",
    "where ",
    "which ",
    "can ",
    "does ",
    "do ",
    "is ",
    "are ",
)
_GENERIC_STARTERS = [
    "What is the main idea?",
    "Why does this topic matter?",
    "How could I use this in practice?",
    "What is a common mistake here?",
    "Which idea should I review next?",
]


def _topic_label(value: str, *, maximum_words: int = 5) -> str:
    """Turn a chapter heading into a short phrase that reads inside a question."""
    label = " ".join(str(value).split()).strip(" -–—:.;")
    if ":" in label:
        label = label.split(":", 1)[1].strip()
    label = re.sub(
        r"^(?:chapter|section|part|lecture)\s+[\w.-]+\s*[-–—:]?\s*",
        "",
        label,
        flags=re.I,
    )
    words = label.split()
    return " ".join(words[:maximum_words]).strip(" -–—:.;") or "this topic"


def normalize_suggested_question(value: object) -> str | None:
    """Return one safe, concise starter question or reject the candidate.

    The model is deliberately not trusted to follow the presentation contract.
    Rejecting a compound prompt is safer than truncating it: a clipped question
    can change meaning, while a deterministic fallback stays useful.
    """
    if not isinstance(value, (str, int, float)):
        return None
    question = _NUMBERING.sub("", " ".join(str(value).split())).strip(' "\'')
    question = question.rstrip(".!?") + "?"
    lowered = question.casefold()
    words = _WORDS.findall(question)

    if not question or len(question) > MAX_QUESTION_CHARACTERS:
        return None
    if not 3 <= len(words) <= MAX_QUESTION_WORDS:
        return None
    if not lowered.startswith(_SIMPLE_OPENERS):
        return None
    if any(mark in question[:-1] for mark in ("?", "!", ".")):
        return None
    if ";" in question or ":" in question or question.count(",") > 1:
        return None
    if len(re.findall(r"\b(?:and|or)\b", lowered)) > 1:
        return None
    if any(
        phrase in lowered
        for phrase in (
            "how would you design",
            "suppose you",
            "end-to-end",
            "generate questions",
            "generate interview",
            "list all",
        )
    ):
        return None
    return question


def select_concise_questions(
    generated: list[object] | None,
    fallbacks: list[str],
) -> list[str]:
    """Keep valid model suggestions and deterministically fill the five slots."""
    selected: list[str] = []
    seen: set[str] = set()
    for candidate in [*(generated or []), *fallbacks, *_GENERIC_STARTERS]:
        question = normalize_suggested_question(candidate)
        if not question or question.casefold() in seen:
            continue
        seen.add(question.casefold())
        selected.append(question)
        if len(selected) == 5:
            return selected
    return selected


def _deterministic_book_questions(
    books_data: list[dict[str, Any]],
) -> list[str]:
    """Generate clean fallback starter questions when LLM generation is unavailable."""
    if not books_data:
        return list(_GENERIC_STARTERS)

    if len(books_data) == 1:
        book = books_data[0]
        chapters = book.get("chapters", [])
        topic = _topic_label(chapters[0]) if chapters else "the main idea"
        second = _topic_label(chapters[1]) if len(chapters) > 1 else "this topic"
        return select_concise_questions(
            [],
            [
                f"What is {topic} about?",
                f"Why does {topic} matter?",
                f"What should I know about {second}?",
                "What is the book's most useful idea?",
                "Where could I apply these ideas?",
            ],
        )

    # Multiple books scope
    return [
        "What idea appears across these books?",
        "Where do these books disagree?",
        "Which concept is most useful in practice?",
        "How do the main ideas connect?",
        "What should I learn first?",
    ]


def _deterministic_video_questions(
    video_title: str,
    chapters: list[str],
) -> list[str]:
    """Generate clean fallback starter questions for a video lecture."""
    topic = _topic_label(chapters[len(chapters) // 2]) if chapters else "this topic"
    title = _topic_label(video_title)
    return select_concise_questions(
        [],
        [
            "What is this lecture mainly about?",
            f"Why does {topic} matter?",
            "What was the most important diagram?",
            "How could I use this in practice?",
            f"What should I remember about {title}?",
        ],
    )


def _call_llm_for_questions(system_prompt: str, user_prompt: str) -> list[str] | None:
    """Invoke OpenRouter LLM to return exactly 5 dynamic questions."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    try:
        from langchain_openai import ChatOpenAI

        model_name = (
            os.getenv("OPENROUTER_GENERATION_MODEL") or DEFAULT_GENERATION_MODEL
        )
        llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "1")),
            timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "15")),
            max_tokens=300,
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
        if isinstance(data, list):
            return [
                str(question).strip()
                for question in data
                if isinstance(question, (str, int, float))
                and str(question).strip()
            ][:5]
    except Exception as exc:
        logger.warning("LLM dynamic question generation failed: %s", exc)
    return None


@traced("study.question_generator.generate_book_questions", flow="suggested_questions")
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
        "You write clickable starter questions for a study app. "
        "Generate exactly 5 simple, interesting questions grounded in the "
        "supplied book and chapter titles. Each question must ask about one "
        "idea, use plain language, contain one sentence, and use at most 12 words. "
        "Use varied openings such as What, Why, How, When, or Which. "
        "Do not create full interview exercises, multi-step design problems, "
        "bundled checklists, or requests to generate more questions. Good "
        "examples: 'What problem does caching solve?', 'Why does data drift "
        "matter?', 'When should you use replication?'. "
        "Format your output strictly as a JSON array of strings, e.g.: "
        '["Question 1", "Question 2", "Question 3", "Question 4", "Question 5"]. '
        "Do NOT include any additional conversational text or markdown explanation."
    )

    scope_desc = "\n".join(
        f"- Book: '{book['title']}' by {book['author']}. Chapters: "
        f"{', '.join(book['chapters']) if book['chapters'] else 'N/A'}"
        for book in books_data
    )
    user_prompt = f"Write five concise starter questions for:\n{scope_desc}"

    llm_questions = _call_llm_for_questions(system_prompt, user_prompt)
    return select_concise_questions(
        llm_questions,
        _deterministic_book_questions(books_data),
    )


@traced("study.question_generator.generate_video_questions", flow="suggested_questions")
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
        "You write clickable starter questions for a technical lecture app. "
        "Generate exactly 5 simple, interesting questions grounded in the "
        "supplied title and chapters. Each question must ask about one idea, "
        "use plain language, contain one sentence, and use at most 12 words. "
        "Include an overview, a chapter idea, and one visual question. "
        "Do not create full interview exercises, multi-step problems, bundled "
        "checklists, or requests to generate more questions. Good examples: "
        "'What is this lecture mainly about?', 'Why does attention matter?', "
        "'What does the diagram explain?'. "
        "Format your output strictly as a JSON array of 5 strings: "
        '["Q1", "Q2", "Q3", "Q4", "Q5"]. '
        "Do NOT include any extra text."
    )

    chapter_list = "\n".join(f"- {chapter}" for chapter in chapters)
    user_prompt = (
        f"Video title: '{video_title}'\n"
        f"Chapters:\n{chapter_list if chapter_list else 'No chapters listed.'}"
    )

    llm_questions = _call_llm_for_questions(system_prompt, user_prompt)
    return select_concise_questions(
        llm_questions,
        _deterministic_video_questions(video_title, chapters),
    )
