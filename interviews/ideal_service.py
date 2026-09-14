"""Application service for generating complete ideal chapter interviews."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from .contracts import FormatChoice, TargetLevel
from .ideal_contracts import IdealInterviewFlow
from .ideal_generation import estimate_spoken_seconds, generate_ideal_exchange, prompt_version
from .models import model_name
from .planning import detect_format, load_source
from . import ideal_store


EXCHANGE_ATTEMPTS = 2


@dataclass(frozen=True)
class CreateIdealInterview:
    book_id: int
    node_id: int
    target_level: TargetLevel = "mid"
    format_choice: FormatChoice = "auto"


def create_ideal_interview(
    connection: Connection,
    *,
    owner_id: str | UUID,
    request: CreateIdealInterview,
    model: Any | None = None,
) -> IdealInterviewFlow:
    source = load_source(
        connection,
        owner_id=owner_id,
        source_kind="book",
        book_id=request.book_id,
        node_id=request.node_id,
    )
    inventory = source.inventory
    interview_format = (
        detect_format(inventory)
        if request.format_choice == "auto"
        else request.format_choice
    )
    version = prompt_version()
    generation_model = model_name()
    reusable = ideal_store.find_reusable_flow(
        connection,
        owner_id=owner_id,
        scope_key=inventory.scope_key,
        interview_format=interview_format,
        target_level=request.target_level,
        generation_model=generation_model,
        prompt_version=version,
    )
    if reusable is not None:
        return reusable
    exchanges = []
    total_cost = 0.0
    # Every deterministic coverage unit is assigned exactly one exchange.
    # Large flat chapters use page units; structured chapters use node units.
    # Generation cannot decide that a less convenient area is expendable.
    for index, topic in enumerate(inventory.topics):
        last_error: ValueError | None = None
        for _attempt in range(EXCHANGE_ATTEMPTS):
            try:
                exchange, cost = generate_ideal_exchange(
                    inventory=inventory,
                    topic=topic,
                    interview_format=interview_format,
                    target_level=request.target_level,
                    index=index,
                    previous=exchanges,
                    model=model,
                )
                break
            except ValueError as error:
                last_error = error
        else:
            assert last_error is not None
            raise last_error
        exchanges.append(exchange)
        total_cost += cost
    if {item.topic_key for item in exchanges} != {
        topic.key for topic in inventory.topics
    }:
        raise ValueError("ideal interview generation did not cover the full chapter")
    return ideal_store.create_flow(
        connection,
        owner_id=owner_id,
        book_id=request.book_id,
        node_id=request.node_id,
        scope_key=inventory.scope_key,
        title=f"Ideal interview · {inventory.title}",
        source_title=inventory.source_title,
        interview_format=interview_format,
        target_level=request.target_level,
        exchanges=exchanges,
        estimated_duration_seconds=estimate_spoken_seconds(exchanges),
        generation_model=generation_model,
        prompt_version=version,
        total_cost_usd=total_cost,
    )
