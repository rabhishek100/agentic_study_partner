"""Model-classified visual states, transitions, and price/performance calibration."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .artifacts import stable_hash
from .models import FrameCandidate, VisualObservation
from .openrouter import OpenRouterClient, OpenRouterError


DEFAULT_CANDIDATES = (
    "stepfun/step-3.7-flash",
    "openai/gpt-5.6-luna",
)
DEFAULT_REFERENCE_MODEL = "openai/gpt-5.6-terra"


ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["frames", "sequence_summary"],
    "properties": {
        "sequence_summary": {"type": "string", "maxLength": 200},
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "frame_index",
                    "content_types",
                    "importance",
                    "summary",
                    "visible_text",
                    "technical_details",
                    "image_embedding_recommended",
                    "transition_after_type",
                    "transition_after_summary",
                ],
                "properties": {
                    "frame_index": {"type": "integer"},
                    "content_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "slide",
                                "diagram",
                                "drawing",
                                "chart",
                                "code",
                                "terminal",
                                "notebook",
                                "ui",
                                "equation",
                                "presenter",
                                "whiteboard",
                                "mixed",
                                "other",
                            ],
                        },
                    },
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "summary": {"type": "string", "maxLength": 280},
                    "visible_text": {"type": "string", "maxLength": 500},
                    "technical_details": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {"type": "string", "maxLength": 180},
                    },
                    "image_embedding_recommended": {"type": "boolean"},
                    "transition_after_type": {
                        "type": ["string", "null"],
                        "enum": [
                            "slide_change",
                            "diagram_expansion",
                            "code_edit",
                            "command_executed",
                            "output_changed",
                            "ui_action",
                            "annotation_added",
                            "camera_change",
                            "other",
                            None,
                        ],
                    },
                    "transition_after_summary": {
                        "type": ["string", "null"],
                        "maxLength": 220,
                    },
                },
            },
        },
    },
}


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["winner", "a_grounding", "b_grounding", "a_coverage", "b_coverage", "reason"],
    "properties": {
        "winner": {"type": "string", "enum": ["a", "b", "tie"]},
        "a_grounding": {"type": "number", "minimum": 0, "maximum": 1},
        "b_grounding": {"type": "number", "minimum": 0, "maximum": 1},
        "a_coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "b_coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
}


def frame_groups(
    frames: list[FrameCandidate], *, group_size: int = 2
) -> list[list[FrameCandidate]]:
    return [frames[index : index + group_size] for index in range(0, len(frames), group_size)]


def analysis_prompt(group: list[FrameCandidate]) -> str:
    inventory = "\n".join(
        f"Frame {index}: id={frame.id}; timestamp={format_ms(frame.timestamp_ms)}; "
        f"Tesseract confidence={frame.ocr_confidence:.2f}; OCR={frame.ocr_text[:1200]!r}"
        for index, frame in enumerate(group)
    )
    return f"""\
Analyze this chronological sequence of frames from a technical ML lecture.
The images follow the frame inventory in exactly the same order.

{inventory}

For every frame, classify all applicable visual content types and describe
only technical information actually visible. Correct obvious OCR mistakes by
reading the image, but never complete hidden or illegible content. Capture
diagram components and relationships, chart axes/trends, equations, code,
terminal commands/output, UI state, and meaningful presenter gestures when
present. Describe the transition from each frame to the next only when a
technical state changes.

Set image_embedding_recommended=true only when visual/spatial structure is
needed for retrieval (drawings, diagrams, charts, or genuinely mixed visuals).
Textual slides, code, terminals, notebooks, and UI text should normally rely
on OCR/text retrieval. Importance means value as evidence for understanding
the lecture, not visual attractiveness. Do not use outside knowledge.

Be concise. OCR already stores the full screen text: visible_text must contain
only headings, labels, equations, code, or phrases needed to retrieve and
understand the visual. Never transcribe paragraphs or repeat OCR verbatim.
Keep each summary under 280 characters, visible_text under 500, each technical
detail under 180, and each transition summary under 220 characters.
"""


def calibrate_models(
    client: OpenRouterClient,
    groups: list[list[FrameCandidate]],
    *,
    candidate_models: tuple[str, str] = DEFAULT_CANDIDATES,
    reference_model: str = DEFAULT_REFERENCE_MODEL,
    sample_size: int = 6,
) -> dict[str, Any]:
    sample = stratified_sample(groups, sample_size)
    wins = {candidate_models[0]: 0.0, candidate_models[1]: 0.0}
    grounding = {candidate_models[0]: [], candidate_models[1]: []}
    coverage = {candidate_models[0]: [], candidate_models[1]: []}
    latencies = {candidate_models[0]: [], candidate_models[1]: []}
    costs = {candidate_models[0]: [], candidate_models[1]: []}
    failures: dict[str, list[str]] = {model: [] for model in candidate_models}
    disabled: set[str] = set()
    # Resume from paid viability failures. Repeating an already-invalid probe
    # would spend money without adding calibration evidence.
    for model in candidate_models:
        prior = [
            entry
            for entry in client.ledger.entries
            if entry.get("operation") == "visual-calibration"
            and entry.get("requested_model") == model
            and entry.get("status") in {"invalid_json", "empty_response"}
        ]
        prior_success = [
            entry
            for entry in client.ledger.entries
            if entry.get("operation") == "visual-calibration"
            and entry.get("requested_model") == model
            and entry.get("status") == "success"
        ]
        if len(prior) >= 2 and len(prior) > len(prior_success):
            failures[model].append(
                f"viability gate failed {len(prior)} paid attempts: "
                f"{prior[-1].get('status')}"
            )
            disabled.add(model)
    cases: list[dict[str, Any]] = []
    for case_index, group in enumerate(sample):
        outputs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for model in candidate_models:
            if model in disabled:
                continue
            try:
                output = client.call_json(
                    model=model,
                    operation="visual-calibration",
                    prompt=analysis_prompt(group),
                    schema_name="visual_sequence",
                    schema=ANALYSIS_SCHEMA,
                    images=[Path(frame.preview_path) for frame in group],
                    max_tokens=2_200,
                    estimated_cost_usd=0.01,
                )
                outputs[model] = output
                latencies[model].append(float(output[1]["latency_seconds"]))
                costs[model].append(float(output[1]["cost_usd"]))
            except Exception as error:  # noqa: BLE001 - measured disqualification
                failures[model].append(str(error))
                disabled.add(model)
        judgement: dict[str, Any] | None = None
        judge_provenance: dict[str, Any] = {"cost_usd": 0.0}
        if len(outputs) == 2:
            judge_prompt = f"""\
You are selecting the better low-cost visual extractor for technical lecture
RAG. Inspect the same chronological images and compare the two structured
outputs below. Prefer visible grounding, correct technical detail, temporal
change coverage, and refusal to invent. Do not reward verbosity.

Candidate A ({candidate_models[0]}):
{outputs[candidate_models[0]][0]}

Candidate B ({candidate_models[1]}):
{outputs[candidate_models[1]][0]}
"""
            judgement, judge_provenance = client.call_json(
                model=reference_model,
                operation="visual-calibration-judge",
                prompt=judge_prompt,
                schema_name="visual_model_comparison",
                schema=JUDGE_SCHEMA,
                images=[Path(frame.preview_path) for frame in group],
                max_tokens=800,
                reasoning_effort="low",
                estimated_cost_usd=0.02,
            )
            winner = judgement["winner"]
            if winner == "tie":
                wins[candidate_models[0]] += 0.5
                wins[candidate_models[1]] += 0.5
            else:
                wins[candidate_models[0 if winner == "a" else 1]] += 1.0
            grounding[candidate_models[0]].append(float(judgement["a_grounding"]))
            grounding[candidate_models[1]].append(float(judgement["b_grounding"]))
            coverage[candidate_models[0]].append(float(judgement["a_coverage"]))
            coverage[candidate_models[1]].append(float(judgement["b_coverage"]))
        cases.append(
            {
                "case": case_index,
                "frame_ids": [frame.id for frame in group],
                "outputs": {model: value[0] for model, value in outputs.items()},
                "costs": {model: value[1]["cost_usd"] for model, value in outputs.items()}
                | {"judge": judge_provenance["cost_usd"]},
                "judgement": judgement,
            }
        )
    summaries = {}
    for model in candidate_models:
        summaries[model] = {
            "wins": wins[model],
            "mean_grounding": mean(grounding[model]),
            "mean_coverage": mean(coverage[model]),
            "mean_latency_seconds": mean(latencies[model]),
            "mean_cost_usd": mean(costs[model]),
            "valid_cases": len(latencies[model]),
            "failures": failures[model],
            "prompt_price_per_token": client.price(model)[0],
            "completion_price_per_token": client.price(model)[1],
        }
    viable = [model for model in candidate_models if latencies[model]]
    if not viable:
        raise RuntimeError(f"all visual candidates failed viability: {failures}")
    if len(viable) == 1:
        selected = viable[0]
        return {
            "selected_model": selected,
            "selection_reason": "other candidate failed structured-output viability",
            "candidate_models": list(candidate_models),
            "reference_model": reference_model,
            "summaries": summaries,
            "cases": cases,
        }
    # Quality dominates. Price breaks a near-tie within two percentage points.
    scores = {
        model: summaries[model]["mean_grounding"] * 0.6
        + summaries[model]["mean_coverage"] * 0.4
        for model in candidate_models
    }
    best_quality = max(scores.values())
    eligible = [model for model in candidate_models if best_quality - scores[model] <= 0.02]
    selected = min(
        eligible,
        key=lambda model: summaries[model]["prompt_price_per_token"]
        + summaries[model]["completion_price_per_token"],
    )
    return {
        "selected_model": selected,
        "selection_reason": "reference-judged quality with price as a near-tie break",
        "candidate_models": list(candidate_models),
        "reference_model": reference_model,
        "summaries": summaries,
        "cases": cases,
    }


def analyze_groups(
    client: OpenRouterClient,
    groups: list[list[FrameCandidate]],
    *,
    model: str,
    fallback_model: str,
) -> list[VisualObservation]:
    observations: list[VisualObservation] = []
    prompt_hash = stable_hash(ANALYSIS_SCHEMA)
    for group in groups:
        error: str | None = None
        try:
            result, provenance = client.call_json(
                model=model,
                operation="visual-analysis",
                prompt=analysis_prompt(group),
                schema_name="visual_sequence",
                schema=ANALYSIS_SCHEMA,
                images=[Path(frame.preview_path) for frame in group],
                max_tokens=2_200,
                estimated_cost_usd=0.01,
            )
        except Exception as primary_error:  # noqa: BLE001 - explicit fallback
            error = str(primary_error)
            result, provenance = client.call_json(
                model=fallback_model,
                operation="visual-analysis-fallback",
                prompt=analysis_prompt(group),
                schema_name="visual_sequence",
                schema=ANALYSIS_SCHEMA,
                images=[Path(frame.preview_path) for frame in group],
                max_tokens=2_200,
                estimated_cost_usd=0.014,
            )
        by_index = {
            int(item.get("frame_index", -1)): item for item in result.get("frames", [])
        }
        for index, frame in enumerate(group):
            item = by_index.get(index)
            if not item:
                observations.append(
                    VisualObservation(
                        id=f"observation-{frame.id}",
                        frame_ids=tuple(value.id for value in group),
                        start_ms=frame.timestamp_ms,
                        end_ms=_end_ms(group, index),
                        content_types=(),
                        importance=0.0,
                        summary="",
                        visible_text="",
                        technical_details=(),
                        transition_type=None,
                        transition_summary=None,
                        image_embedding_recommended=False,
                        evidence_frame_ids=(frame.id,),
                        model_id=str(provenance["model"]),
                        prompt_hash=prompt_hash,
                        cost_usd=0.0,
                        latency_seconds=0.0,
                        chapter_index=frame.chapter_index,
                        error="model omitted this frame",
                    )
                )
                continue
            observations.append(
                VisualObservation(
                    id=f"observation-{frame.id}",
                    frame_ids=tuple(value.id for value in group),
                    start_ms=frame.timestamp_ms,
                    end_ms=_end_ms(group, index),
                    content_types=tuple(item.get("content_types") or []),
                    importance=float(item.get("importance") or 0.0),
                    summary=str(item.get("summary") or ""),
                    visible_text=str(item.get("visible_text") or ""),
                    technical_details=tuple(item.get("technical_details") or []),
                    transition_type=item.get("transition_after_type"),
                    transition_summary=item.get("transition_after_summary"),
                    image_embedding_recommended=bool(
                        item.get("image_embedding_recommended")
                    ),
                    evidence_frame_ids=(frame.id,),
                    model_id=str(provenance["model"]),
                    prompt_hash=str(provenance["prompt_hash"]),
                    # One provider call describes the whole sequence. Record
                    # the cost on its first observation to avoid double count.
                    cost_usd=float(provenance["cost_usd"]) if index == 0 else 0.0,
                    latency_seconds=(
                        float(provenance["latency_seconds"]) if index == 0 else 0.0
                    ),
                    chapter_index=frame.chapter_index,
                    error=error,
                )
            )
    return observations


def stratified_sample(groups: list[list[FrameCandidate]], size: int) -> list[list[FrameCandidate]]:
    if len(groups) <= size:
        return groups
    # Even temporal coverage captures the course's very different visual
    # sections without letting a dense animation dominate the calibration.
    indexes = {
        round(index * (len(groups) - 1) / (size - 1)) for index in range(size)
    }
    return [groups[index] for index in sorted(indexes)]


def format_ms(value: int) -> str:
    seconds = value // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _end_ms(group: list[FrameCandidate], index: int) -> int:
    if index + 1 < len(group):
        return group[index + 1].timestamp_ms
    return group[index].timestamp_ms + 30_000
