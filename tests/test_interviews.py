"""Adaptive interview planning, grounding, graph routing, and speech."""

from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

import httpx

from api.interviews import _public
from decks.contracts import DeckFigure
from decks.topics import ScopeInventory, Topic
from interviews.contracts import (
    AnswerEvaluation,
    InterviewCheckpoint,
    InterviewClarification,
    InterviewClarificationDraft,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
    ScoreCard,
    TopicState,
)
from interviews.evaluation import (
    InterviewValidationError,
    interviewer_reaction,
    resolve_citations,
    sanitize_evaluation,
    validate_question,
)
from interviews.graph import AnswerGraphContext, answer_graph
from interviews.models import structured_model
from interviews.planning import (
    detect_format,
    estimate_duration,
    initial_checkpoint,
    next_topic,
    planned_topics,
)
from interviews.prompts import build_evaluation_messages, build_question_messages
from interviews.question_generation import (
    apply_work_sample_policy,
    generate_question,
    grounded_fallback_question,
    repair_legacy_recall_fallback,
    repair_nonvisual_work_sample,
    validate_question_focus,
    validate_question_progression,
)
from interviews.service import clarify_interview_question
from interviews.speech import (
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
    SpeechError,
    spoken_question_text,
    synthesize_interviewer_speech,
)


def topic(*, evidence: str = "[N7:P42]\nLogistic regression models log odds.") -> Topic:
    return Topic(
        key="node:7",
        ordinal=0,
        label="Logistic regression",
        required=True,
        evidence_text=evidence,
        allowed_markers=frozenset({"[N7:P42]"}),
        node_id=7,
        start_page=42,
        end_page=45,
    )


def inventory(*, title: str = "Logistic regression", evidence: str | None = None):
    item = topic(evidence=evidence or "[N7:P42]\nLogistic regression models log odds.")
    return ScopeInventory(
        source_kind="book",
        scope_key="book:1:node:7",
        title=title,
        source_title="Machine Learning",
        outline=f"- {item.label}",
        topics=(item,),
    )


def question() -> InterviewQuestion:
    return InterviewQuestion(
        topic_key="node:7",
        topic_label="Logistic regression",
        text="What does logistic regression model?",
        expected_points=["Connect probability to log odds."],
        suggested_answer="It models log odds as a linear function. [N7:P42]",
        citation_markers=["[N7:P42]"],
        difficulty="mid",
    )


def evaluation(*, complete: bool, clarify: bool = False) -> AnswerEvaluation:
    return AnswerEvaluation(
        classification="source_aligned" if complete else "partially_correct",
        scores=ScoreCard(
            technical_correctness=5 if complete else 2,
            depth_completeness=4 if complete else 2,
            reasoning_structure=4 if complete else 3,
            tradeoff_awareness=3,
            communication_clarity=4,
            independence=5,
        ),
        strengths=["Clear definition"],
        gaps=[] if complete else ["Connect probability to log odds"],
        concise_feedback="Grounded feedback. [N7:P42]",
        recommended_answer="It models log odds linearly. [N7:P42]",
        citation_markers=["[N7:P42]"],
        question_complete=complete,
        needs_clarifying_probe=clarify,
        clarifying_probe=("How do probability and log odds connect?" if clarify else None),
        topic_complete=complete,
    )


def session() -> InterviewSession:
    return InterviewSession(
        session_id="00000000-0000-0000-0000-000000000001",
        source_kind="book",
        book_id=1,
        node_id=7,
        scope_key="book:1:node:7",
        title="Interview · Logistic regression",
        source_title="Machine Learning",
        interview_format="concept",
        format_source="detected",
        feedback_mode="realistic",
        target_level="mid",
        maximum_duration_minutes=30,
        estimated_min_minutes=10,
        estimated_max_minutes=15,
        status="active",
        elapsed_seconds=30,
        checkpoint=InterviewCheckpoint(
            topics=[TopicState(key="node:7", label="Logistic regression")],
            active_topic_key="node:7",
        ),
        turns=[],
    )


class RawResponse:
    response_metadata = {"cost": 0.001}


class FakeStructuredModel:
    def __init__(self, value) -> None:
        self.value = value

    def invoke(self, messages):
        return {"parsed": self.value, "raw": RawResponse()}


class SequenceStructuredModel:
    def __init__(self, *values) -> None:
        self.values = list(values)
        self.calls = 0

    def invoke(self, messages):
        value = self.values[self.calls]
        self.calls += 1
        return {"parsed": value, "raw": RawResponse()}


class FailingStructuredModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise TimeoutError("provider stalled")


class PlanningTests(unittest.TestCase):
    def test_detects_source_led_before_generic_system_design(self) -> None:
        scope = inventory(
            title="A system design interview walkthrough",
            evidence=(
                "[N7:P42]\nBegin the interview by clarifying requirements, "
                "then estimate throughput and discuss failure modes."
            ),
        )
        self.assertEqual(detect_format(scope), "source_led")

    def test_detects_system_design_from_repeated_source_vocabulary(self) -> None:
        scope = inventory(
            title="Scalable architecture",
            evidence=(
                "[N7:P42]\nArchitecture requirements include latency and "
                "throughput trade-offs and distributed failure modes."
            ),
        )
        self.assertEqual(detect_format(scope), "system_design")

    def test_duration_is_an_estimate_capped_at_two_hours(self) -> None:
        many = inventory()
        many = ScopeInventory(
            source_kind=many.source_kind,
            scope_key=many.scope_key,
            title=many.title,
            source_title=many.source_title,
            outline=many.outline,
            topics=tuple(
                Topic(
                    key=f"node:{index}",
                    ordinal=index,
                    label=f"Topic {index}",
                    required=True,
                    evidence_text=f"[N{index}:P42]\nEvidence",
                    allowed_markers=frozenset({f"[N{index}:P42]"}),
                )
                for index in range(100)
            ),
        )
        self.assertEqual(estimate_duration(many, target_level="senior"), (120, 120))


class GroundingTests(unittest.TestCase):
    @patch("langchain_openai.ChatOpenAI")
    def test_interactive_model_has_a_short_deadline_without_hidden_retries(
        self, chat_model
    ) -> None:
        with patch.dict(
            "os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True
        ):
            structured_model(InterviewQuestion)

        self.assertEqual(chat_model.call_args.kwargs["timeout"], 15.0)
        self.assertEqual(chat_model.call_args.kwargs["max_retries"], 0)

    def test_question_private_answer_must_stay_inside_topic(self) -> None:
        invalid = question().model_copy(
            update={
                "suggested_answer": "A claim. [N9:P99]",
                "citation_markers": ["[N9:P99]"],
            }
        )
        with self.assertRaises(InterviewValidationError):
            validate_question(invalid, topic())

    def test_question_prompt_forbids_hidden_memorization_rubrics(self) -> None:
        messages = build_question_messages(
            inventory=inventory(),
            topic=topic(),
            interview_format="concept",
            target_level="mid",
        )

        instruction = " ".join(str(messages[-1].content).split())
        self.assertIn("answerable through reasoning", instruction)
        self.assertIn("directly solicited by the audible question", instruction)

    def test_evaluation_prompt_scores_only_the_audible_scope(self) -> None:
        messages = build_evaluation_messages(
            inventory=inventory(),
            topic=topic(),
            question=question(),
            answer="It models log odds.",
            mode="realistic",
            target_level="mid",
            attempts=1,
            hints_used=0,
        )

        instruction = " ".join(str(messages[-1].content).split())
        self.assertIn("Score all six dimensions", instruction)
        self.assertIn("against that explicit scope", instruction)
        self.assertIn("must not appear in `gaps`", instruction)

    def test_malformed_evaluation_falls_back_to_validated_model_answer(self) -> None:
        invalid = evaluation(complete=True).model_copy(
            update={
                "recommended_answer": "Unsupported. [N9:P99]",
                "citation_markers": ["[N9:P99]"],
            }
        )
        repaired = sanitize_evaluation(invalid, question=question(), topic=topic())
        self.assertEqual(repaired.recommended_answer, question().suggested_answer)
        self.assertEqual(repaired.citation_markers, ["[N7:P42]"])

    def test_markers_resolve_to_reader_facing_pages(self) -> None:
        citations = resolve_citations(["[N7:P42]"], topic())
        self.assertEqual(citations[0].page, 42)
        self.assertEqual(citations[0].node_id, 7)

    def test_short_interview_samples_across_a_large_chapter(self) -> None:
        topics = tuple(
            replace(
                topic(),
                key=f"node:{index}",
                ordinal=index,
                label=f"Chapter :: Section {index}",
                node_id=index,
                figures=(
                    DeckFigure(
                        kind="book_image",
                        book_id=1,
                        node_id=index,
                        block_id=index,
                    ),
                ),
            )
            for index in range(20)
        )
        scope = replace(inventory(), topics=topics)

        selected = planned_topics(
            scope,
            maximum_duration_minutes=15,
            target_level="mid",
        )
        checkpoint = initial_checkpoint(
            scope,
            maximum_duration_minutes=15,
            target_level="mid",
        )

        self.assertEqual(len(selected), 4)
        self.assertEqual(len(checkpoint.required_topics), 4)
        self.assertLess(selected[0].ordinal, 5)
        self.assertGreaterEqual(selected[-1].ordinal, 15)

    def test_next_topic_finishes_breadth_before_revisiting_a_gap(self) -> None:
        second = replace(topic(), key="node:8", ordinal=1, label="Calibration")
        scope = replace(inventory(), topics=(topic(), second))
        checkpoint = InterviewCheckpoint(
            topics=[
                TopicState(
                    key="node:7",
                    label="Logistic regression",
                    attempts=1,
                    best_score=2.0,
                ),
                TopicState(key="node:8", label="Calibration"),
            ]
        )

        self.assertEqual(next_topic(scope, checkpoint).key, "node:8")

    def test_near_duplicate_question_is_rejected(self) -> None:
        duplicate = question().model_copy(
            update={"text": "What exactly does a logistic regression model?"}
        )

        with self.assertRaises(InterviewValidationError):
            validate_question_progression(duplicate, [question()])

    def test_compound_question_is_rejected(self) -> None:
        overloaded = question().model_copy(
            update={
                "text": (
                    "How would you design the user-data layer, and what challenges "
                    "would you address when preparing the data for modeling?"
                )
            }
        )

        with self.assertRaisesRegex(InterviewValidationError, "multiple objectives"):
            validate_question_focus(overloaded)

    def test_overlong_question_is_rejected(self) -> None:
        overloaded = question().model_copy(
            update={"text": " ".join(["detail"] * 33) + "?"}
        )

        with self.assertRaisesRegex(InterviewValidationError, "too broad"):
            validate_question_focus(overloaded)

    def test_work_sample_cannot_add_complexity_edges_and_testing(self) -> None:
        overloaded = question().model_copy(
            update={
                "work_sample": "code",
                "work_sample_prompt": (
                    "Share your screen and write pseudocode. Cover complexity, edge "
                    "cases, and one test."
                ),
            }
        )

        with self.assertRaisesRegex(
            InterviewValidationError, "work-sample instruction adds another objective"
        ):
            validate_question_focus(overloaded)

    def test_focused_screen_question_stays_within_one_turn_budget(self) -> None:
        focused = question().model_copy(
            update={
                "text": (
                    "Derive the log-odds equation that maps a feature vector to "
                    "a class probability."
                ),
                "work_sample": "equation_derivation",
                "work_sample_prompt": (
                    "Use the shared screen to show each step of the requested derivation."
                ),
            }
        )

        self.assertIs(validate_question_focus(focused), focused)

    def test_question_generation_retries_a_repeated_draft(self) -> None:
        replacement = question().model_copy(
            update={"text": "Why are log odds useful for this model?"}
        )
        model = SequenceStructuredModel(question(), replacement)

        generated, cost = generate_question(
            inventory=inventory(),
            topic=topic(),
            interview_format="concept",
            target_level="mid",
            kind="primary",
            recent_questions=[question()],
            model=model,
        )

        self.assertEqual(generated.text, replacement.text)
        self.assertEqual(model.calls, 2)
        self.assertEqual(cost, 0.002)

    def test_question_generation_retries_an_overloaded_draft(self) -> None:
        overloaded = question().model_copy(
            update={
                "text": (
                    "Explain logistic regression, and then discuss its objective, "
                    "failure modes, calibration, and implementation details?"
                )
            }
        )
        focused = question().model_copy(
            update={"text": "How do log odds connect to probability?"}
        )
        model = SequenceStructuredModel(overloaded, focused)

        generated, _ = generate_question(
            inventory=inventory(),
            topic=topic(),
            interview_format="concept",
            target_level="mid",
            kind="primary",
            recent_questions=[],
            model=model,
        )

        self.assertEqual(generated.text, focused.text)
        self.assertEqual(model.calls, 2)

    def test_invalid_retries_fall_back_without_losing_the_turn(self) -> None:
        overloaded = question().model_copy(
            update={
                "text": (
                    "Explain logistic regression, and then discuss calibration, "
                    "failure modes, and implementation?"
                )
            }
        )
        model = SequenceStructuredModel(overloaded, overloaded)

        generated, cost = generate_question(
            inventory=inventory(),
            topic=topic(),
            interview_format="concept",
            target_level="mid",
            kind="primary",
            recent_questions=[],
            model=model,
        )

        self.assertEqual(
            generated.text,
            "How would you use Logistic regression in a practical system?",
        )
        self.assertEqual(generated.citation_markers, ["[N7:P42]"])
        self.assertIn("[N7:P42]", generated.suggested_answer)
        self.assertEqual(generated.work_sample, "none")
        self.assertEqual(cost, 0.002)

    def test_provider_timeout_falls_back_without_a_second_wait(self) -> None:
        model = FailingStructuredModel()

        generated, cost = generate_question(
            inventory=inventory(),
            topic=topic(),
            interview_format="concept",
            target_level="mid",
            kind="primary",
            recent_questions=[],
            model=model,
        )

        self.assertEqual(model.calls, 1)
        self.assertEqual(generated.citation_markers, ["[N7:P42]"])
        self.assertEqual(cost, 0.0)

    def test_fallback_changes_shape_after_a_repeated_question(self) -> None:
        previous = question().model_copy(
            update={
                "text": (
                    "How would you use Logistic regression in a practical system?"
                )
            }
        )

        generated = grounded_fallback_question(
            topic=topic(),
            target_level="mid",
            kind="primary",
            recent_questions=[previous],
        )

        self.assertIn("other practical consideration", generated.text)
        self.assertNotIn("source", generated.text.lower())

    def test_follow_up_fallback_tests_reasoning_instead_of_source_recall(self) -> None:
        choosing = topic(
            evidence=(
                "[N7:P42]\nFrame Street View blurring as object detection before "
                "selecting a detector architecture."
            )
        )
        choosing = replace(choosing, label="Choosing the right ML category")

        generated = grounded_fallback_question(
            topic=choosing,
            target_level="mid",
            kind="follow_up",
            recent_questions=[],
        )

        self.assertEqual(
            generated.text,
            "What factor would most influence choosing the right ML category in this scenario?",
        )
        self.assertNotIn("source", generated.text.lower())
        self.assertNotIn("source", generated.expected_points[0].lower())

    def test_saved_source_recall_fallback_is_repaired_on_load(self) -> None:
        legacy = question().model_copy(
            update={
                "kind": "follow_up",
                "topic_label": "Choosing the right ML category",
                "text": (
                    "State one source-grounded point about Choosing the right ML category."
                ),
            }
        )

        repaired = repair_legacy_recall_fallback(legacy)

        self.assertEqual(
            repaired.text,
            "What factor would most influence choosing the right ML category in this scenario?",
        )
        self.assertNotIn("source", repaired.text.lower())
        self.assertNotIn("source", repaired.expected_points[0].lower())

    def test_punctuated_section_title_cannot_break_answer_progression(self) -> None:
        punctuated = replace(
            topic(),
            label=(
                "Choosing the right ML category? What assumptions should we make, "
                "and how should we validate them?"
            ),
        )

        generated = grounded_fallback_question(
            topic=punctuated,
            target_level="mid",
            kind="follow_up",
            recent_questions=[],
        )

        self.assertEqual(generated.text.count("?"), 1)
        self.assertNotIn("What assumptions", generated.text)
        self.assertIs(validate_question_focus(generated), generated)

    @patch(
        "interviews.question_generation._practical_fallback_scope",
        return_value=("First prompt? Second prompt?", ["invalid"]),
    )
    def test_fixed_final_fallback_cannot_block_answer_saving(self, _scope) -> None:
        generated = grounded_fallback_question(
            topic=topic(),
            target_level="mid",
            kind="follow_up",
            recent_questions=[],
        )

        self.assertEqual(
            generated.text,
            "What practical factor would guide your decision in this scenario?",
        )
        self.assertIs(validate_question_focus(generated), generated)

    def test_assumptions_are_answered_verbally(self) -> None:
        architecture_topic = topic(
            evidence=(
                "[N7:P42]\nEstimate throughput, then design the services, "
                "storage, and data flow."
            )
        )
        requested = apply_work_sample_policy(
            question().model_copy(
                update={"text": "What throughput assumptions would you start with?"}
            ),
            topic=architecture_topic,
            interview_format="system_design",
            recent_questions=[],
        )

        self.assertEqual(requested.work_sample, "none")
        self.assertIsNone(requested.work_sample_prompt)

    def test_saved_assumptions_screen_task_is_repaired_on_load(self) -> None:
        legacy = question().model_copy(
            update={
                "text": "Which throughput assumptions would you use?",
                "work_sample": "assumptions",
                "work_sample_prompt": "Write the capacity assumptions.",
            }
        )

        repaired = repair_nonvisual_work_sample(legacy)

        self.assertEqual(repaired.work_sample, "none")
        self.assertIsNone(repaired.work_sample_prompt)

    def test_business_objective_does_not_infer_an_equation_from_topic_evidence(self) -> None:
        objective_topic = topic(
            evidence=(
                "[N7:P42]\nThe business objective protects privacy. The model "
                "later uses a loss function and probability threshold."
            )
        )
        requested = apply_work_sample_policy(
            question().model_copy(
                update={
                    "text": (
                        "What core business objective should this blurring system satisfy?"
                    )
                }
            ),
            topic=objective_topic,
            interview_format="system_design",
            recent_questions=[],
        )

        self.assertEqual(requested.work_sample, "none")
        self.assertIsNone(requested.work_sample_prompt)

    def test_mismatched_equation_exercise_is_rejected(self) -> None:
        mismatched = question().model_copy(
            update={
                "text": "What business objective should this blurring system satisfy?",
                "work_sample": "equation_derivation",
                "work_sample_prompt": "Use the shared screen to show the derivation.",
            }
        )

        with self.assertRaisesRegex(InterviewValidationError, "does not match"):
            validate_question_focus(mismatched)

    def test_generation_retries_when_screen_task_does_not_match_question(self) -> None:
        mismatched = question().model_copy(
            update={
                "text": "What business objective should this blurring system satisfy?",
                "work_sample": "equation_derivation",
                "work_sample_prompt": "Use the shared screen to show the derivation.",
            }
        )
        corrected = question().model_copy(
            update={
                "text": "What business objective should this blurring system satisfy?",
                "work_sample": "none",
                "work_sample_prompt": None,
            }
        )
        model = SequenceStructuredModel(mismatched, corrected)

        generated, _ = generate_question(
            inventory=inventory(),
            topic=topic(),
            interview_format="system_design",
            target_level="mid",
            kind="primary",
            recent_questions=[],
            model=model,
        )

        self.assertEqual(model.calls, 2)
        self.assertEqual(generated.work_sample, "none")

    def test_vague_equation_request_is_rejected(self) -> None:
        vague = question().model_copy(
            update={"text": "Can you derive the key equation?"}
        )

        with self.assertRaisesRegex(InterviewValidationError, "name the relationship"):
            validate_question_focus(vague)

    def test_recall_question_about_a_source_heading_is_rejected(self) -> None:
        recall = question().model_copy(
            update={"text": "What is the central idea behind Choosing the right ML category?"}
        )

        with self.assertRaisesRegex(InterviewValidationError, "source recall"):
            validate_question_focus(recall)

    def test_private_rubric_is_limited_to_one_question_scope(self) -> None:
        overloaded = question().model_copy(
            update={"expected_points": ["one", "two", "three", "four"]}
        )

        with self.assertRaisesRegex(InterviewValidationError, "private rubric"):
            validate_question_focus(overloaded)

    def test_screen_work_is_not_requested_on_consecutive_questions(self) -> None:
        prior = question().model_copy(
            update={
                "work_sample": "equation_derivation",
                "work_sample_prompt": "Derive the log-odds equation.",
            }
        )

        requested = apply_work_sample_policy(
            question().model_copy(
                update={
                    "text": (
                        "State the throughput assumptions, then design the services "
                        "and data flow."
                    )
                }
            ),
            topic=topic(),
            interview_format="concept",
            recent_questions=[prior],
        )

        self.assertEqual(requested.work_sample, "none")
        self.assertIsNone(requested.work_sample_prompt)

    def test_screen_work_rotates_to_another_relevant_artifact(self) -> None:
        assumptions = question().model_copy(
            update={
                "work_sample": "assumptions",
                "work_sample_prompt": "Write the capacity assumptions.",
            }
        )
        verbal = question().model_copy(
            update={"text": "Which failure mode concerns you most?"}
        )
        architecture_topic = topic(
            evidence=(
                "[N7:P42]\nEstimate throughput, then design the services, "
                "storage, API, and data flow."
            )
        )

        requested = apply_work_sample_policy(
            question().model_copy(
                update={
                    "text": (
                        "State the throughput assumptions, then design the services "
                        "and data flow."
                    )
                }
            ),
            topic=architecture_topic,
            interview_format="system_design",
            recent_questions=[assumptions, verbal],
        )

        self.assertEqual(requested.work_sample, "architecture_diagram")
        self.assertIn("architecture", requested.work_sample_prompt.lower())


class ClarificationTests(unittest.TestCase):
    @patch("interviews.service.store.append_question_clarification")
    @patch("interviews.service.load_session_inventory")
    @patch("interviews.service.store.load_session")
    def test_candidate_can_clarify_without_settling_the_answer(
        self,
        load_session,
        load_inventory,
        append_clarification,
    ) -> None:
        active = session().model_copy(
            update={"turns": [InterviewTurn(turn_index=0, question=question())]}
        )
        load_session.side_effect = [active, active]
        load_inventory.return_value = inventory()
        model = FakeStructuredModel(
            InterviewClarificationDraft(
                interviewer_response=(
                    "Explain what quantity logistic regression models; you do not "
                    "need to derive an equation for this question."
                )
            )
        )

        result = clarify_interview_question(
            object(),
            active.session_id,
            owner_id="00000000-0000-0000-0000-000000000002",
            candidate_question="Which equation should I write?",
            model=model,
        )

        self.assertIs(result, active)
        saved = append_clarification.call_args.kwargs["clarification"]
        self.assertIsInstance(saved, InterviewClarification)
        self.assertEqual(saved.candidate_question, "Which equation should I write?")
        self.assertIsNone(active.turns[0].answer_text)

    def test_public_live_question_keeps_clarifications_but_hides_rubric(self) -> None:
        clarified = question().model_copy(
            update={
                "clarifications": [
                    InterviewClarification(
                        candidate_question="Which relationship?",
                        interviewer_response="The relationship between log odds and features.",
                    )
                ]
            }
        )
        active = session().model_copy(
            update={"turns": [InterviewTurn(turn_index=0, question=clarified)]}
        )

        public = _public(active)

        self.assertEqual(public.turns[0].question.expected_points, [])
        self.assertEqual(
            public.turns[0].question.clarifications[0].candidate_question,
            "Which relationship?",
        )


class GraphTests(unittest.TestCase):
    def test_strong_answer_finishes_when_source_is_exhausted(self) -> None:
        current = InterviewTurn(turn_index=0, question=question())
        output = answer_graph.invoke(
            {
                "session": session(),
                "inventory": inventory(),
                "current_turn": current,
                "answer_text": "It models the log odds as a linear function.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(evaluation(complete=True))
            ),
        )
        self.assertIsNone(output["next_question"])
        self.assertEqual(output["checkpoint"].coverage_ratio, 1.0)
        self.assertIn("covered", output["finish_reason"])
        self.assertEqual(output["evaluation_cost_usd"], 0.001)

    def test_ambiguous_weak_answer_gets_one_clarifying_probe(self) -> None:
        current = InterviewTurn(turn_index=0, question=question())
        clarification = question().model_copy(
            update={"text": "How do probability and log odds connect?"}
        )
        output = answer_graph.invoke(
            {
                "session": session(),
                "inventory": inventory(),
                "current_turn": current,
                "answer_text": "It predicts a class.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(
                    evaluation(complete=False, clarify=True)
                ),
                question_model=FakeStructuredModel(clarification),
            ),
        )
        self.assertEqual(output["next_question"].kind, "clarifying")
        self.assertEqual(
            output["next_question"].text,
            "How do probability and log odds connect?",
        )

    def test_complete_scoped_answer_gets_an_unpenalized_depth_follow_up(self) -> None:
        current = InterviewTurn(turn_index=0, question=question())
        scoped = evaluation(complete=False).model_copy(
            update={
                "question_complete": True,
                "needs_depth_follow_up": True,
                "depth_follow_up_focus": (
                    "the trade-off between one-stage and two-stage detectors"
                ),
                "gaps": ["Did not mention one-stage versus two-stage detectors"],
                "concise_feedback": "I expected detector architecture details.",
            }
        )
        follow_up = question().model_copy(
            update={
                "text": (
                    "How would you choose between one-stage and two-stage detectors "
                    "for this system?"
                ),
                "expected_points": ["Compare the detector families for this scenario."],
            }
        )

        output = answer_graph.invoke(
            {
                "session": session(),
                "inventory": inventory(),
                "current_turn": current,
                "answer_text": "It is an object detection task.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(scoped),
                question_model=FakeStructuredModel(follow_up),
            ),
        )

        self.assertEqual(output["next_question"].kind, "follow_up")
        self.assertIn("one-stage and two-stage", output["next_question"].text)
        self.assertFalse(output["checkpoint"].topics[0].completed)
        self.assertEqual(output["checkpoint"].topics[0].hints_used, 0)
        self.assertEqual(output["evaluation"].classification, "source_aligned")
        self.assertGreaterEqual(
            output["evaluation"].scores.depth_completeness,
            4,
        )
        self.assertEqual(output["evaluation"].gaps, [])
        self.assertNotIn("expected", output["evaluation"].concise_feedback.lower())

    def test_depth_is_deferred_until_planned_breadth_is_covered(self) -> None:
        second = Topic(
            key="node:8",
            ordinal=1,
            label="Model calibration",
            required=True,
            evidence_text="[N8:P50]\nCalibration aligns predicted and observed rates.",
            allowed_markers=frozenset({"[N8:P50]"}),
            node_id=8,
            start_page=50,
            end_page=52,
        )
        scope = replace(inventory(), topics=(topic(), second))
        live = session().model_copy(
            update={
                "checkpoint": InterviewCheckpoint(
                    topics=[
                        TopicState(key="node:7", label="Logistic regression"),
                        TopicState(key="node:8", label="Model calibration"),
                    ],
                    active_topic_key="node:7",
                )
            }
        )
        scoped = evaluation(complete=False).model_copy(
            update={
                "question_complete": True,
                "needs_depth_follow_up": True,
                "depth_follow_up_focus": "calibration after fitting",
                "gaps": [],
            }
        )
        breadth_question = InterviewQuestion(
            topic_key="node:8",
            topic_label="Model calibration",
            text="How would you assess whether this classifier is calibrated?",
            expected_points=["Compare predicted and observed rates."],
            suggested_answer="Compare predicted and observed rates. [N8:P50]",
            citation_markers=["[N8:P50]"],
            difficulty="mid",
        )

        output = answer_graph.invoke(
            {
                "session": live,
                "inventory": scope,
                "current_turn": InterviewTurn(turn_index=0, question=question()),
                "answer_text": "It models log odds as a linear function.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(scoped),
                question_model=FakeStructuredModel(breadth_question),
            ),
        )

        self.assertEqual(output["next_question"].topic_key, "node:8")
        self.assertEqual(output["next_question"].kind, "primary")
        self.assertFalse(output["checkpoint"].topics[0].completed)

    def test_no_new_question_starts_near_the_duration_ceiling(self) -> None:
        timed = session().model_copy(update={"elapsed_seconds": 30 * 60 - 20})
        output = answer_graph.invoke(
            {
                "session": timed,
                "inventory": inventory(),
                "current_turn": InterviewTurn(turn_index=0, question=question()),
                "answer_text": "It predicts a class.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(evaluation(complete=False))
            ),
        )
        self.assertIsNone(output["next_question"])
        self.assertIn("duration ceiling", output["finish_reason"])

    def test_second_weak_answer_advances_to_the_next_topic(self) -> None:
        second = Topic(
            key="node:8",
            ordinal=1,
            label="Model calibration",
            required=True,
            evidence_text="[N8:P50]\nCalibration aligns predicted and observed rates.",
            allowed_markers=frozenset({"[N8:P50]"}),
            node_id=8,
            start_page=50,
            end_page=52,
        )
        scope = inventory()
        scope = ScopeInventory(
            source_kind=scope.source_kind,
            scope_key=scope.scope_key,
            title=scope.title,
            source_title=scope.source_title,
            outline="- Logistic regression\n- Model calibration",
            topics=(topic(), second),
        )
        live = session().model_copy(
            update={
                "checkpoint": InterviewCheckpoint(
                    topics=[
                        TopicState(
                            key="node:7",
                            label="Logistic regression",
                            attempts=1,
                        ),
                        TopicState(key="node:8", label="Model calibration"),
                    ],
                    active_topic_key="node:7",
                )
            }
        )
        next_question = InterviewQuestion(
            topic_key="node:8",
            topic_label="Model calibration",
            text="How would you assess whether this classifier is calibrated?",
            expected_points=["Compare predicted and observed rates."],
            suggested_answer="Compare predicted and observed rates. [N8:P50]",
            citation_markers=["[N8:P50]"],
            difficulty="mid",
        )

        output = answer_graph.invoke(
            {
                "session": live,
                "inventory": scope,
                "current_turn": InterviewTurn(turn_index=1, question=question()),
                "answer_text": "It predicts a class.",
            },
            context=AnswerGraphContext(
                evaluation_model=FakeStructuredModel(evaluation(complete=False)),
                question_model=FakeStructuredModel(next_question),
            ),
        )

        self.assertEqual(output["checkpoint"].topics[0].attempts, 2)
        self.assertTrue(output["checkpoint"].topics[0].completed)
        self.assertEqual(output["next_question"].topic_key, "node:8")
        self.assertEqual(output["next_question"].kind, "primary")


class SpeechTests(unittest.TestCase):
    def test_question_narration_includes_the_requested_screen_task(self) -> None:
        requested = question().model_copy(
            update={
                "work_sample": "equation_derivation",
                "work_sample_prompt": "Derive the log-odds equation on screen.",
            }
        )

        spoken = spoken_question_text(requested)

        self.assertIn(question().text, spoken)
        self.assertIn("Derive the log-odds equation on screen.", spoken)

    def test_realistic_session_exposes_reaction_but_not_private_evaluation(self) -> None:
        live = session().model_copy(
            update={
                "turns": [
                    InterviewTurn(
                        turn_index=0,
                        question=question(),
                        answer_text="It models log odds.",
                        evaluation=evaluation(complete=True),
                    )
                ]
            }
        )

        public = _public(live)

        self.assertIsNone(public.turns[0].evaluation)
        self.assertEqual(public.turns[0].interviewer_reaction, "Grounded feedback.")
        self.assertEqual(public.turns[0].question.expected_points, [])

    def test_interviewer_reaction_is_spoken_and_hides_source_markers(self) -> None:
        result = interviewer_reaction(evaluation(complete=True))

        self.assertEqual(result, "Grounded feedback.")
        self.assertNotIn("[N7:P42]", result)

    def test_interviewer_reaction_has_a_natural_fallback(self) -> None:
        incomplete = evaluation(complete=False).model_copy(
            update={"concise_feedback": ""}
        )

        self.assertIn("right track", interviewer_reaction(incomplete))

    def test_voxtral_is_the_default_and_cost_is_character_bounded(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.dict("os.environ", {}, clear=False):
                result = synthesize_interviewer_speech("Why logistic regression?", client=client)

        body = requests[0].read().decode()
        self.assertIn(DEFAULT_TTS_MODEL, body)
        self.assertIn(DEFAULT_TTS_VOICE, body)
        self.assertEqual(result.content, b"mp3")
        self.assertLess(result.cost_usd, 0.001)

    def test_rejects_a_success_response_that_is_not_audio(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"error": "provider returned no audio"},
                headers={"content-type": "application/json"},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(SpeechError, "invalid audio"):
                synthesize_interviewer_speech("Begin the interview.", client=client)


if __name__ == "__main__":
    unittest.main()
