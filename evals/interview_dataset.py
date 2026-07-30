"""Contracts and structural validation for interview-answer evaluation data."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from study.contracts import (
    AnswerArchetype,
    ContractModel,
    ResponseDepth,
    Route,
)

EvidenceStatus = Literal[
    "verified_from_existing_gold",
    "candidate_anchor",
    "not_applicable",
]
ReviewStatus = Literal["pending_human_review", "model_adjudicated", "human_verified"]
Difficulty = Literal["foundational", "intermediate", "advanced"]


class DatasetBook(ContractModel):
    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str | None = None
    production_book_id_hint: int = Field(gt=0)
    source_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceAnchor(ContractModel):
    node_id: int = Field(gt=0)
    path: str = Field(min_length=1)
    pages: list[int] = Field(min_length=1)
    role: Literal["required", "supporting"] = "required"

    @model_validator(mode="after")
    def pages_are_positive_and_ordered(self) -> EvidenceAnchor:
        if any(page <= 0 for page in self.pages):
            raise ValueError("evidence pages must be positive")
        if self.pages != sorted(set(self.pages)):
            raise ValueError("evidence pages must be unique and sorted")
        return self


class PriorTurn(ContractModel):
    user: str = Field(min_length=1)
    answer_summary: str = Field(min_length=1)


class InterviewCase(ContractModel):
    id: str = Field(pattern=r"^int-\d{3}$")
    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    book_key: str = Field(min_length=1)
    category: Literal[
        "concept",
        "comparison",
        "scenario",
        "system_design",
        "chapter_review",
        "follow_up",
        "unanswerable",
    ]
    difficulty: Difficulty
    ui_requested_depth: ResponseDepth
    expected_depth: ResponseDepth
    expected_archetype: AnswerArchetype
    expected_route: Route
    answerable: bool
    evidence_status: EvidenceStatus
    candidate_evidence: list[EvidenceAnchor]
    prior_turns: list[PriorTurn] = Field(default_factory=list)
    must_cover: list[str]
    should_cover: list[str] = Field(default_factory=list)
    must_avoid: list[str]
    expected_follow_ups: list[str] = Field(default_factory=list)
    scoring_focus: list[
        Literal[
            "grounded_correctness",
            "interview_readiness",
            "coverage",
            "depth_adherence",
            "clarity_memorability",
            "follow_up_quality",
            "citation_quality",
        ]
    ] = Field(min_length=1)
    tags: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def answerability_matches_evidence(self) -> InterviewCase:
        if self.answerable:
            if self.evidence_status == "not_applicable":
                raise ValueError("answerable cases require evidence")
            if not self.candidate_evidence:
                raise ValueError("answerable cases require candidate evidence")
            if len(self.must_cover) < 2:
                raise ValueError(
                    "answerable cases require at least two must-cover items"
                )
        else:
            if self.evidence_status != "not_applicable":
                raise ValueError("unanswerable cases must use not_applicable evidence")
            if self.candidate_evidence:
                raise ValueError("unanswerable cases cannot have candidate evidence")
            if self.must_cover:
                raise ValueError("unanswerable cases cannot require answer content")
        if not self.must_avoid:
            raise ValueError("every case requires at least one failure guard")
        return self


class ScoreDimension(ContractModel):
    description: str = Field(min_length=1)
    score_0: str = Field(min_length=1)
    score_2: str = Field(min_length=1)
    score_4: str = Field(min_length=1)


class DatasetReview(ContractModel):
    status: ReviewStatus
    notes: str = Field(min_length=1)


class InterviewDataset(ContractModel):
    schema_version: str
    dataset_id: str
    created_on: str
    purpose: str
    provenance: str
    intended_use: list[str] = Field(min_length=1)
    exclusions: list[str] = Field(min_length=1)
    books: list[DatasetBook] = Field(min_length=1)
    score_dimensions: dict[str, ScoreDimension]
    cases: list[InterviewCase] = Field(min_length=1)
    review: DatasetReview

    @model_validator(mode="after")
    def references_are_unique_and_resolved(self) -> InterviewDataset:
        book_keys = [book.key for book in self.books]
        if len(book_keys) != len(set(book_keys)):
            raise ValueError("book keys must be unique")
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case ids must be unique")
        unknown_books = {
            case.book_key for case in self.cases if case.book_key not in set(book_keys)
        }
        if unknown_books:
            raise ValueError(
                "cases reference unknown books: " + ", ".join(sorted(unknown_books))
            )
        declared_dimensions = set(self.score_dimensions)
        referenced_dimensions = {
            dimension for case in self.cases for dimension in case.scoring_focus
        }
        missing_dimensions = referenced_dimensions - declared_dimensions
        if missing_dimensions:
            raise ValueError(
                "cases reference undeclared score dimensions: "
                + ", ".join(sorted(missing_dimensions))
            )
        return self


def load_interview_dataset(path: str | Path) -> InterviewDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return InterviewDataset.model_validate(payload)


def validate_canonical_anchors(dataset: InterviewDataset, connection) -> list[str]:
    """Check identity, node ownership, paths, and page bounds without judging meaning."""

    errors: list[str] = []
    resolved_books: dict[str, int] = {}
    for book in dataset.books:
        rows = connection.execute(
            """
            select id, title
            from books
            where file_hash = %s
            order by id
            """,
            (book.source_file_sha256,),
        ).fetchall()
        hinted_rows = [row for row in rows if row["id"] == book.production_book_id_hint]
        if len(hinted_rows) == 1:
            row = hinted_rows[0]
        elif len(rows) == 1:
            row = rows[0]
        else:
            errors.append(
                f"book {book.key}: source hash is ambiguous and id hint "
                f"{book.production_book_id_hint} did not resolve it"
            )
            continue
        resolved_books[book.key] = row["id"]
        if row["id"] != book.production_book_id_hint:
            errors.append(
                f"book {book.key}: id hint {book.production_book_id_hint} "
                f"does not match canonical id {row['id']}"
            )

    for case in dataset.cases:
        book_id = resolved_books.get(case.book_key)
        if book_id is None:
            continue
        for anchor in case.candidate_evidence:
            row = connection.execute(
                """
                select book_id, path_text, start_page, end_page
                from nodes
                where id = %s
                """,
                (anchor.node_id,),
            ).fetchone()
            prefix = f"{case.id}/node-{anchor.node_id}"
            if row is None:
                errors.append(f"{prefix}: node does not exist")
                continue
            if row["book_id"] != book_id:
                errors.append(
                    f"{prefix}: belongs to book {row['book_id']}, expected {book_id}"
                )
            if row["path_text"].strip() != anchor.path.strip():
                errors.append(
                    f"{prefix}: path mismatch; canonical is {row['path_text']!r}"
                )
            outside = [
                page
                for page in anchor.pages
                if page < row["start_page"] or page > row["end_page"]
            ]
            if outside:
                errors.append(
                    f"{prefix}: pages outside canonical range "
                    f"{row['start_page']}-{row['end_page']}: {outside}"
                )
    return errors


def coverage_summary(dataset: InterviewDataset) -> dict:
    return {
        "case_count": len(dataset.cases),
        "answerable_count": sum(case.answerable for case in dataset.cases),
        "unanswerable_count": sum(not case.answerable for case in dataset.cases),
        "books": dict(Counter(case.book_key for case in dataset.cases)),
        "categories": dict(Counter(case.category for case in dataset.cases)),
        "expected_depths": dict(Counter(case.expected_depth for case in dataset.cases)),
        "archetypes": dict(Counter(case.expected_archetype for case in dataset.cases)),
        "follow_up_cases": sum(bool(case.prior_turns) for case in dataset.cases),
        "explicit_depth_overrides": sum(
            case.ui_requested_depth != case.expected_depth for case in dataset.cases
        ),
        "evidence_statuses": dict(
            Counter(case.evidence_status for case in dataset.cases)
        ),
    }
