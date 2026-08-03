"""Automatic source-grounded question generation and diagnostic evaluation."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from collections import Counter
import json
import re
import time
from typing import Any

from .models import (
    Chapter,
    EvaluationQuestion,
    EvaluationResult,
    EvidenceUnit,
    RetrievedEvidence,
)
from .openrouter import OpenRouterClient
from .retrieval import MultimodalIndex, embed_query


GENERATION_MODEL = "openai/gpt-5.6-luna"
JUDGE_MODEL = "google/gemini-3.5-flash-lite"
CITATION = re.compile(r"\[E(\d+)\]")
REQUIRED_CATEGORY_COUNTS = {
    "visual_only": 6,
    "multimodal": 8,
    "transcript_led": 4,
    "slide_led": 4,
    "temporal_change": 4,
    "synthesis": 2,
    "unanswerable": 2,
}


QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 30,
            "maxItems": 30,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "category",
                    "question",
                    "expected_modalities",
                    "expected_chapter_indexes",
                    "generated_from_evidence_ids",
                ],
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "visual_only",
                            "multimodal",
                            "transcript_led",
                            "slide_led",
                            "temporal_change",
                            "synthesis",
                            "unanswerable",
                        ],
                    },
                    "question": {"type": "string"},
                    "expected_modalities": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "expected_chapter_indexes": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "generated_from_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


UNANSWERABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "expected_modalities", "expected_chapter_indexes"],
    "properties": {
        "question": {"type": "string"},
        "expected_modalities": {"type": "array", "items": {"type": "string"}},
        "expected_chapter_indexes": {
            "type": "array",
            "items": {"type": "integer"},
        },
    },
}


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "used_evidence_ids", "unsupported_gap"],
    "properties": {
        "answer": {"type": "string"},
        "used_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "unsupported_gap": {"type": ["string", "null"]},
    },
}


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "semantic_agreement",
        "missing_points",
        "unsupported_claims",
        "summary",
    ],
    "properties": {
        "semantic_agreement": {"type": "number", "minimum": 0, "maximum": 1},
        "missing_points": {"type": "array", "items": {"type": "string"}},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}


def generate_questions(
    client: OpenRouterClient,
    evidence: list[EvidenceUnit],
    chapters: list[Chapter],
) -> list[EvaluationQuestion]:
    # The complete lecture inventory is only ~300K characters and fits well
    # inside Luna's context. The earlier 120K prefix silently omitted the
    # self-attention and transformer chapters from question generation.
    inventory = evidence_inventory(evidence, maximum_chars=500_000)
    chapter_text = "\n".join(
        f"{chapter.index}: {chapter.title} ({format_ms(chapter.start_ms)}–{format_ms(chapter.end_ms)})"
        for chapter in chapters
    )
    prompt = f"""\
Generate exactly 30 evaluation questions for a visual-first RAG system over
one technical lecture. Derive every answerable question strictly from the
evidence inventory. The deliberately unanswerable questions must sound
plausible for this lecture but must not be answerable from the inventory.

Required category counts:
- visual_only: 6
- multimodal: 8
- transcript_led: 4
- slide_led: 4
- temporal_change: 4
- synthesis: 2
- unanswerable: 2

Every official chapter below must appear in expected_chapter_indexes for at
least two answerable questions. Do not let longer early chapters crowd out
self-attention, transformer architecture, or the detailed example.

Visual-only questions must require something actually visible rather than a
fact repeated in speech. Multimodal questions must benefit from combining at
least two source types. Temporal-change questions must ask what changed across
frames. Avoid trivia and vague prompts. For answerable questions, list the
specific evidence IDs that establish the answer. Do not use outside knowledge.

Chapters:
{chapter_text}

Evidence inventory:
{inventory}
"""
    result, _ = client.call_json(
        model=GENERATION_MODEL,
        operation="evaluation-question-generation",
        prompt=prompt,
        schema_name="video_evaluation_questions",
        schema=QUESTION_SCHEMA,
        max_tokens=6_000,
        estimated_cost_usd=0.035,
    )
    questions = parse_questions(result, evidence)
    if question_category_counts(questions) != REQUIRED_CATEGORY_COUNTS:
        repair_prompt = f"""\
Repair this evaluation set so it contains exactly the required category
counts below. Preserve good questions when possible. Replace surplus-category
questions with genuinely source-unanswerable questions where needed. An
unanswerable question must be plausible for this lecture but unsupported by
the supplied evidence inventory, and its generated_from_evidence_ids must be
empty. Keep exactly 30 unique, useful questions. Do not use outside knowledge
to answer any question.

Required counts: {json.dumps(REQUIRED_CATEGORY_COUNTS, sort_keys=True)}

Current set:
{json.dumps(result, ensure_ascii=False)}

Evidence inventory:
{inventory}
"""
        result, _ = client.call_json(
            model=GENERATION_MODEL,
            operation="evaluation-question-repair",
            prompt=repair_prompt,
            schema_name="repaired_video_evaluation_questions",
            schema=QUESTION_SCHEMA,
            max_tokens=6_000,
            estimated_cost_usd=0.035,
        )
        questions = parse_questions(result, evidence)
    counts = question_category_counts(questions)
    if counts != REQUIRED_CATEGORY_COUNTS:
        questions = repair_category_counts(
            client, questions, evidence, inventory=inventory
        )
        counts = question_category_counts(questions)
    required_chapter_indexes = {chapter.index for chapter in chapters}
    if not question_chapter_coverage(questions, required_chapter_indexes):
        chapter_repair_prompt = f"""\
Repair this 30-question video-lecture evaluation set. Keep the exact category
counts {json.dumps(REQUIRED_CATEGORY_COUNTS, sort_keys=True)}. Every official
chapter index must occur in expected_chapter_indexes for at least two
answerable questions. Replace overrepresented early-chapter questions with
useful questions from missing chapters while preserving the meaning of every
question category. Unanswerable questions must remain unsupported and have
no evidence IDs. Use only the complete evidence inventory.

Official chapters:
{chapter_text}

Current questions:
{json.dumps([asdict(question) for question in questions], ensure_ascii=False)}

Complete evidence inventory:
{inventory}
"""
        repaired_result, _ = client.call_json(
            model=GENERATION_MODEL,
            operation="evaluation-question-chapter-repair",
            prompt=chapter_repair_prompt,
            schema_name="chapter_balanced_video_questions",
            schema=QUESTION_SCHEMA,
            max_tokens=6_000,
            estimated_cost_usd=0.04,
        )
        questions = parse_questions(repaired_result, evidence)
        questions = repair_category_counts(
            client, questions, evidence, inventory=inventory
        )
        counts = question_category_counts(questions)
    if counts != REQUIRED_CATEGORY_COUNTS:
        raise RuntimeError(f"question category counts {counts}, expected {REQUIRED_CATEGORY_COUNTS}")
    if not question_chapter_coverage(questions, required_chapter_indexes):
        raise RuntimeError(
            "question set does not cover every official chapter at least twice: "
            f"{dict(question_chapter_counts(questions))}"
        )
    return questions


def repair_category_counts(
    client: OpenRouterClient,
    questions: list[EvaluationQuestion],
    evidence: list[EvidenceUnit],
    *,
    inventory: str,
) -> list[EvaluationQuestion]:
    """Replace only surplus items; the model cannot alter quota bookkeeping."""

    repaired = list(questions)
    counts = question_category_counts(repaired)
    missing_unanswerable = REQUIRED_CATEGORY_COUNTS["unanswerable"] - counts["unanswerable"]
    if missing_unanswerable <= 0:
        return repaired
    existing = "\n".join(f"- {question.question}" for question in repaired)
    for replacement_index in range(missing_unanswerable):
        surplus_categories = {
            category
            for category, required in REQUIRED_CATEGORY_COUNTS.items()
            if counts[category] > required
        }
        surplus_position = next(
            (
                index
                for index in range(len(repaired) - 1, -1, -1)
                if repaired[index].category in surplus_categories
            ),
            None,
        )
        if surplus_position is None:
            break
        prompt = f"""\
Create one deliberately unanswerable evaluation question for this technical
lecture. It must sound relevant and plausible but must not be answerable from
any supplied evidence. Check the complete inventory; do not merely ask a hard
answerable question. Do not duplicate an existing question. Because it is
unanswerable, do not cite evidence or imply that outside knowledge may answer
it. Return only the requested fields.

Existing questions:
{existing}

Complete evidence inventory:
{inventory}
"""
        item, _ = client.call_json(
            model=GENERATION_MODEL,
            operation="evaluation-unanswerable-replacement",
            prompt=prompt,
            schema_name="unanswerable_video_question",
            schema=UNANSWERABLE_SCHEMA,
            max_tokens=800,
            estimated_cost_usd=0.008,
        )
        old = repaired[surplus_position]
        repaired[surplus_position] = EvaluationQuestion(
            id=old.id,
            category="unanswerable",
            question=str(item["question"]),
            expected_modalities=tuple(item.get("expected_modalities") or []),
            expected_chapter_indexes=tuple(
                int(value) for value in item.get("expected_chapter_indexes") or []
            ),
            generated_from_evidence_ids=(),
        )
        counts = question_category_counts(repaired)
        existing += f"\n- {item['question']}"
    return repaired


def parse_questions(
    result: dict[str, Any], evidence: list[EvidenceUnit]
) -> list[EvaluationQuestion]:
    known = {item.id for item in evidence}
    questions: list[EvaluationQuestion] = []
    for index, item in enumerate(result.get("questions") or [], start=1):
        source_ids = tuple(
            identifier
            for identifier in item.get("generated_from_evidence_ids") or []
            if identifier in known
        )
        questions.append(
            EvaluationQuestion(
                id=f"video-q-{index:03d}",
                category=str(item["category"]),
                question=str(item["question"]),
                expected_modalities=tuple(item.get("expected_modalities") or []),
                expected_chapter_indexes=tuple(
                    int(value) for value in item.get("expected_chapter_indexes") or []
                ),
                generated_from_evidence_ids=source_ids,
            )
        )
    if len(questions) != 30:
        raise RuntimeError(f"question generator returned {len(questions)}, expected 30")
    return questions


def question_category_counts(
    questions: list[EvaluationQuestion],
) -> dict[str, int]:
    counts = Counter(question.category for question in questions)
    return {category: counts.get(category, 0) for category in REQUIRED_CATEGORY_COUNTS}


def question_chapter_counts(
    questions: list[EvaluationQuestion],
) -> Counter[int]:
    return Counter(
        chapter
        for question in questions
        if question.category != "unanswerable"
        for chapter in set(question.expected_chapter_indexes)
    )


def question_chapter_coverage(
    questions: list[EvaluationQuestion], required: set[int]
) -> bool:
    counts = question_chapter_counts(questions)
    return all(counts[chapter] >= 2 for chapter in required)


def evaluate_questions(
    client: OpenRouterClient,
    questions: list[EvaluationQuestion],
    evidence: list[EvidenceUnit],
    *,
    per_question_hard_cap_usd: float = 0.05,
) -> list[EvaluationResult]:
    index = MultimodalIndex(evidence)
    by_id = {item.id: item for item in evidence}
    results: list[EvaluationResult] = []
    for question in questions:
        started = time.monotonic()
        question_cost = 0.0
        error: str | None = None
        try:
            reference_scope = reference_evidence(question, evidence)
            reference, reference_ids, reference_provenance = answer_from_evidence(
                client,
                question.question,
                reference_scope,
                operation="evaluation-reference-answer",
                reference=True,
            )
            question_cost += float(reference_provenance["cost_usd"])
            text_query, image_query, query_cost = embed_query(client, question.question)
            question_cost += query_cost
            retrieved = index.search(
                question.question,
                query_text_embedding=text_query,
                query_image_embedding=image_query,
                limit=12,
            )
            retrieved_units = [by_id[item.evidence_id] for item in retrieved]
            system, _, system_provenance = answer_from_evidence(
                client,
                question.question,
                retrieved_units,
                operation="evaluation-system-answer",
                reference=False,
            )
            question_cost += float(system_provenance["cost_usd"])
            judgement, judge_provenance = judge_answer(
                client,
                question.question,
                reference,
                system,
                retrieved_units,
            )
            question_cost += float(judge_provenance["cost_usd"])
            citation_score = citation_correctness(system, len(retrieved_units))
            semantic = float(judgement["semantic_agreement"])
            missing = tuple(judgement.get("missing_points") or [])
            unsupported = tuple(judgement.get("unsupported_claims") or [])
            judge_summary = str(judgement.get("summary") or "")
        except Exception as caught:  # noqa: BLE001 - diagnostic result survives
            error = str(caught)
            reference = ""
            reference_ids = ()
            system = ""
            retrieved = []
            citation_score = 0.0
            semantic = 0.0
            missing = ()
            unsupported = ()
            judge_summary = ""
        if question_cost > per_question_hard_cap_usd and not error:
            error = (
                f"question cost ${question_cost:.4f} exceeded "
                f"${per_question_hard_cap_usd:.4f} hard cap"
            )
        results.append(
            EvaluationResult(
                question=question,
                reference_answer=reference,
                reference_evidence_ids=tuple(reference_ids),
                system_answer=system,
                retrieved=tuple(retrieved),
                semantic_agreement=semantic,
                citation_correctness=citation_score,
                unsupported_claims=unsupported,
                missing_points=missing,
                judge_summary=judge_summary,
                cost_usd=round(question_cost, 6),
                latency_seconds=round(time.monotonic() - started, 3),
                error=error,
            )
        )
    return results


def answer_from_evidence(
    client: OpenRouterClient,
    question: str,
    evidence: list[EvidenceUnit],
    *,
    operation: str,
    reference: bool,
) -> tuple[str, tuple[str, ...], dict[str, Any]]:
    evidence_text = format_evidence(evidence, numbered=not reference, maximum_chars=160_000)
    images = unique_images(evidence, maximum=10)
    if reference:
        citation_rule = (
            "Cite source evidence IDs verbatim in square brackets, for example "
            "[visual-frame-001]."
        )
    else:
        citation_rule = (
            "Evidence is numbered. Cite every important claim with [E#] using "
            "only the provided evidence numbers."
        )
    prompt = f"""\
Answer the question strictly from the supplied lecture evidence. Combine
frames, visual transitions, transcript, and slides when they contribute.
Treat visible video content as first-class evidence. If evidence supports only
part of the question, answer that part and state the gap. Never add outside
knowledge. {citation_rule}

Question: {question}

Evidence:
{evidence_text}
"""
    result, provenance = client.call_json(
        model=GENERATION_MODEL,
        operation=operation,
        prompt=prompt,
        schema_name="grounded_video_answer",
        schema=ANSWER_SCHEMA,
        images=images,
        max_tokens=2_500,
        estimated_cost_usd=0.012 if reference else 0.008,
    )
    answer = str(result.get("answer") or "")
    gap = result.get("unsupported_gap")
    if gap:
        answer = f"{answer}\n\nEvidence gap: {gap}"
    return answer, tuple(result.get("used_evidence_ids") or []), provenance


def judge_answer(
    client: OpenRouterClient,
    question: str,
    reference: str,
    system: str,
    retrieved: list[EvidenceUnit],
) -> tuple[dict[str, Any], dict[str, Any]]:
    prompt = f"""\
Compare a retrieval-bound answer with a source-grounded reference answer for a
technical lecture. Score semantic/directional agreement, not wording. Identify
missing reference points and claims in the system answer that are unsupported
by the retrieved evidence. Do not use outside knowledge.

Question: {question}

Reference answer:
{reference}

System answer:
{system}

Retrieved evidence:
{format_evidence(retrieved, numbered=True, maximum_chars=45_000)}
"""
    return client.call_json(
        model=JUDGE_MODEL,
        operation="evaluation-answer-judge",
        prompt=prompt,
        schema_name="video_answer_judgement",
        schema=JUDGE_SCHEMA,
        max_tokens=1_200,
        estimated_cost_usd=0.006,
    )


def reference_evidence(
    question: EvaluationQuestion,
    evidence: list[EvidenceUnit],
) -> list[EvidenceUnit]:
    if question.category == "unanswerable":
        # The complete lecture is the evidence for abstention. Cap only the
        # serialized representation later, not the modality coverage here.
        return evidence
    expected = set(question.expected_chapter_indexes)
    explicit = set(question.generated_from_evidence_ids)
    selected = [
        item
        for item in evidence
        if item.id in explicit or (expected and item.chapter_index in expected)
    ]
    return selected or evidence


def evidence_inventory(evidence: list[EvidenceUnit], *, maximum_chars: int) -> str:
    # Visual evidence and slides lead the inventory. Transcript remains present
    # but cannot crowd frames out of the question-generation prompt.
    ordered = sorted(
        evidence,
        key=lambda item: (
            0 if item.modality.startswith("visual") else 1 if item.modality == "slide" else 2,
            item.chapter_index if item.chapter_index is not None else 10_000,
            item.start_ms or 0,
        ),
    )
    return format_evidence(ordered, numbered=False, maximum_chars=maximum_chars)


def format_evidence(
    evidence: list[EvidenceUnit],
    *,
    numbered: bool,
    maximum_chars: int,
) -> str:
    lines: list[str] = []
    used = 0
    for index, item in enumerate(evidence, start=1):
        marker = f"E{index}" if numbered else item.id
        locator = (
            f"{format_ms(item.start_ms or 0)}–{format_ms(item.end_ms or item.start_ms or 0)}"
            if item.start_ms is not None
            else f"slide {item.page}"
        )
        text = " ".join(item.text.split())
        line = f"[{marker}] modality={item.modality}; locator={locator}; id={item.id}\n{text}\n"
        if used + len(line) > maximum_chars:
            continue
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def unique_images(evidence: list[EvidenceUnit], *, maximum: int) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for item in evidence:
        if not item.image_path or item.image_path in seen:
            continue
        path = Path(item.image_path)
        if path.exists():
            paths.append(path)
            seen.add(item.image_path)
        if len(paths) >= maximum:
            break
    return paths


def citation_correctness(answer: str, evidence_count: int) -> float:
    citations = [int(value) for value in CITATION.findall(answer)]
    if not citations:
        return 0.0
    valid = sum(1 for value in citations if 1 <= value <= evidence_count)
    return valid / len(citations)


def format_ms(value: int) -> str:
    seconds = value // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
