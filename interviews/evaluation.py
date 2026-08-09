"""Deterministic validation and aggregation around model-written evaluations."""

from __future__ import annotations

from collections import Counter, defaultdict

from decks.topics import Topic
from decks.validate import ANY_MARKER, parse_marker

from .contracts import (
    AnswerEvaluation,
    InterviewCheckpoint,
    InterviewCitation,
    InterviewMetrics,
    InterviewQuestion,
    InterviewTurn,
)


class InterviewValidationError(RuntimeError):
    pass


REACTION_FALLBACKS = {
    "source_aligned": "That was a strong, well-grounded answer.",
    "correct_extension": (
        "That was a sound answer, and the added perspective was useful."
    ),
    "partially_correct": (
        "You are on the right track, though part of the explanation needs more "
        "precision."
    ),
    "incorrect": (
        "There is an important issue in that reasoning that we should correct."
    ),
    "insufficient": (
        "I need a little more substance before I can assess that idea fully."
    ),
}


def interviewer_reaction(evaluation: AnswerEvaluation) -> str:
    """Return a concise candidate-facing transition that is safe to speak.

    The full rubric, scores, citations, and recommended answer remain private
    in realistic mode. Citation markers are removed because hearing source IDs
    between questions is unnatural even when the written feedback included
    one by mistake.
    """

    reaction = ANY_MARKER.sub("", evaluation.concise_feedback)
    reaction = " ".join(reaction.split()).strip()
    return reaction or REACTION_FALLBACKS[evaluation.classification]


def validate_question(question: InterviewQuestion, topic: Topic) -> InterviewQuestion:
    """Reject a private answer whose evidence markers leave its active topic."""

    declared = {marker.strip() for marker in question.citation_markers if marker.strip()}
    inline = set(ANY_MARKER.findall(question.suggested_answer))
    markers = declared | inline
    if not markers:
        raise InterviewValidationError("the generated question has no cited model answer")
    if not markers <= topic.allowed_markers:
        raise InterviewValidationError("the generated model answer cites outside its topic")
    if ANY_MARKER.search(question.text):
        raise InterviewValidationError("the candidate-facing question reveals source markers")
    return question.model_copy(update={"citation_markers": sorted(markers)})


def sanitize_evaluation(
    evaluation: AnswerEvaluation,
    *,
    question: InterviewQuestion,
    topic: Topic,
) -> AnswerEvaluation:
    """Keep only resolvable citations and fall back to the validated model answer."""

    declared = {
        marker.strip()
        for marker in evaluation.citation_markers
        if marker.strip() in topic.allowed_markers
    }
    inline = set(ANY_MARKER.findall(evaluation.recommended_answer))
    valid = (declared | inline) & topic.allowed_markers
    invalid = inline - topic.allowed_markers
    answer = evaluation.recommended_answer
    for marker in invalid:
        answer = answer.replace(marker, "")
    # The question's private answer passed the same source-boundary check. It
    # is safer than displaying uncited corrective prose after a malformed
    # evaluation response.
    if not valid or not ANY_MARKER.search(answer):
        answer = question.suggested_answer
        valid = set(question.citation_markers)
    return evaluation.model_copy(
        update={
            "recommended_answer": answer.strip(),
            "citation_markers": sorted(valid),
        }
    )


def resolve_citations(markers: list[str], topic: Topic) -> list[InterviewCitation]:
    resolved: list[InterviewCitation] = []
    for marker in dict.fromkeys(markers):
        if marker not in topic.allowed_markers:
            continue
        parsed = parse_marker(marker)
        if parsed is None:
            continue
        if parsed.node_id is not None:
            resolved.append(
                InterviewCitation(
                    marker=marker,
                    node_id=parsed.node_id,
                    page=parsed.page,
                )
            )
        else:
            resolved.append(
                InterviewCitation(
                    marker=marker,
                    evidence_rank=parsed.evidence_rank,
                    start_ms=topic.start_ms,
                )
            )
    return resolved


def aggregate_metrics(
    turns: list[InterviewTurn], checkpoint: InterviewCheckpoint
) -> InterviewMetrics:
    settled = [turn for turn in turns if turn.evaluation is not None]
    required = checkpoint.required_topics
    if not settled:
        return InterviewMetrics(
            topics_covered=sum(topic.completed for topic in required),
            topics_required=len(required),
        )

    dimensions: dict[str, list[int]] = defaultdict(list)
    classifications: Counter[str] = Counter()
    strengths: Counter[str] = Counter()
    revision: Counter[str] = Counter()
    for turn in settled:
        evaluation = turn.evaluation
        assert evaluation is not None
        classifications[evaluation.classification] += 1
        for name, value in evaluation.scores.model_dump().items():
            dimensions[name].append(int(value))
        strengths.update(item.strip() for item in evaluation.strengths if item.strip())
        revision.update(item.strip() for item in evaluation.gaps if item.strip())

    dimension_scores = {
        name: round(sum(values) / len(values), 2)
        for name, values in dimensions.items()
    }
    overall = round(
        sum(turn.evaluation.scores.weighted_score for turn in settled if turn.evaluation)
        / len(settled),
        2,
    )
    return InterviewMetrics(
        questions_answered=len(settled),
        topics_covered=sum(topic.completed for topic in required),
        topics_required=len(required),
        overall_score=overall,
        dimension_scores=dimension_scores,
        classification_counts=dict(classifications),
        strengths=[item for item, _ in strengths.most_common(6)],
        revision_topics=[item for item, _ in revision.most_common(8)],
    )
