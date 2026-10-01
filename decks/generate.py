"""Generate one deck over an inventoried scope, then prove what it covered.

The shape is: batch the topics into calls small enough to stay well inside the
context window, validate every card deterministically, check the required
topics against what survived, run exactly one repair pass over what is still
missing, and report the result as numbers rather than as a claim.

One repair pass, not several. A second has nothing new to say — the same
evidence was already supplied twice — and the third mostly spends money to
reword. This is the same conclusion the lecture summary path reached.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Protocol

from .contracts import DeckCard, DeckMetrics, GeneratedCard, TopicCards
from .prompts import build_card_messages, prompt_version
from .topics import GENERATION_BATCH_TOKENS, ScopeInventory, Topic, generation_batches
from .validate import (
    DROP_OUT_OF_SCOPE,
    ValidationTally,
    build_metrics,
    normalized_front,
    validate_card,
)

logger = logging.getLogger("study_partner.decks")

DEFAULT_GENERATION_MODEL = "openai/gpt-6-luna"
# One card for a thin section, four for a dense one. Above four the model
# starts splitting one idea across cards, which reads as coverage and reviews
# as repetition.
DEFAULT_MINIMUM_CARDS = 1
DEFAULT_MAXIMUM_CARDS = 4
DEFAULT_MAXIMUM_CARDS_PER_SET = 15
REPAIR_ATTEMPTS = 1


class CardModel(Protocol):
    """A structured-output model returning `TopicCards`."""

    def invoke(self, messages): ...


ProgressCallback = Callable[[int, int], None]


class DeckGenerationError(RuntimeError):
    """Generation could not run at all."""


@dataclass(frozen=True)
class GenerationConfig:
    minimum_cards_per_topic: int = DEFAULT_MINIMUM_CARDS
    maximum_cards_per_topic: int = DEFAULT_MAXIMUM_CARDS
    maximum_cards_per_set: int = DEFAULT_MAXIMUM_CARDS_PER_SET
    batch_tokens: int = GENERATION_BATCH_TOKENS
    repair: bool = True


@dataclass(frozen=True)
class GeneratedDeck:
    inventory: ScopeInventory
    cards: tuple[DeckCard, ...]
    metrics: DeckMetrics
    model_name: str
    prompt_version: str

    @property
    def complete(self) -> bool:
        return self.metrics.complete


def card_model() -> CardModel:
    """Build the structured-output client used to write cards.

    Same base URL, retries, and timeout as every other generation call site.
    Reasoning stays off for the same reason answering keeps it off: this
    transforms supplied evidence under deterministic checks afterwards.
    """

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise DeckGenerationError("OPENROUTER_API_KEY is required to generate a deck")

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("OPENROUTER_DECK_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_GENERATION_MODEL,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=int(os.getenv("OPENROUTER_GENERATION_MAX_RETRIES", "2")),
        timeout=float(os.getenv("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "120")),
        temperature=0.2,
        extra_body={
            "usage": {"include": True},
            "reasoning": {
                "effort": os.getenv("OPENROUTER_GENERATION_REASONING", "none"),
                "exclude": True,
            },
        },
    )
    return model.with_structured_output(TopicCards, method="json_schema")


def model_name() -> str:
    return (
        os.getenv("OPENROUTER_DECK_MODEL")
        or os.getenv("OPENROUTER_GENERATION_MODEL")
        or DEFAULT_GENERATION_MODEL
    )


def attribute_topic(
    generated: GeneratedCard, batch: tuple[Topic, ...]
) -> Topic | None:
    """Which topic in the batch a card is really about.

    The model declares an ordinal; the markers decide. A declared ordinal is
    accepted only when that topic owns every marker the card used. Otherwise
    the unique topic in the batch that owns them all wins, and a card whose
    markers straddle two topics — or belong to none — is attributed nowhere and
    dropped downstream as out of scope.
    """

    markers = {marker.strip() for marker in generated.citation_markers if marker.strip()}
    declared = next(
        (topic for topic in batch if topic.ordinal + 1 == generated.topic_ordinal),
        None,
    )
    if declared is not None and markers and markers <= declared.allowed_markers:
        return declared

    owners = [topic for topic in batch if markers and markers <= topic.allowed_markers]
    if len(owners) == 1:
        return owners[0]
    # No markers at all is still a real card shape; let the declared topic take
    # it so validation can drop it with the accurate reason (`uncited`) rather
    # than the misleading one.
    return declared if not markers else None


def generate_deck(
    inventory: ScopeInventory,
    *,
    model: CardModel | None = None,
    config: GenerationConfig | None = None,
    progress: ProgressCallback | None = None,
    previous_fronts: tuple[str, ...] = (),
) -> GeneratedDeck:
    """Write, validate, repair, and measure one deck."""

    settings = config or GenerationConfig()
    client = model or card_model()
    if not inventory.topics:
        raise DeckGenerationError("this scope has no content to make cards from")

    tally = ValidationTally()
    seen_fronts = {
        key for front in previous_fronts if (key := normalized_front(front))
    }
    kept: list[DeckCard] = []
    completed = 0
    total = len(inventory.topics)

    for batch in generation_batches(
        inventory.topics, token_budget=settings.batch_tokens
    ):
        kept.extend(
            _run_batch(
                client,
                inventory,
                batch,
                settings=settings,
                tally=tally,
                seen_fronts=seen_fronts,
                previous_fronts=previous_fronts,
            )
        )
        completed += len(batch)
        if progress is not None:
            progress(completed, total)

    covered = {card.topic_key for card in kept}
    missing = tuple(
        topic
        for topic in inventory.required_topics
        if topic.key not in covered
    )

    repair_attempted = False
    if missing and settings.repair:
        repair_attempted = True
        logger.info(
            "deck repair pass",
            extra={"scope_key": inventory.scope_key, "missing": len(missing)},
        )
        for batch in generation_batches(missing, token_budget=settings.batch_tokens):
            kept.extend(
                _run_batch(
                    client,
                    inventory,
                    batch,
                    settings=settings,
                    tally=tally,
                    seen_fronts=seen_fronts,
                    previous_fronts=previous_fronts,
                    repair=True,
                )
            )
        covered = {card.topic_key for card in kept}

    curated = _curated(kept, inventory.topics, settings.maximum_cards_per_set)
    curated_fronts = {card.front for card in curated}
    for discarded in kept:
        if discarded.front not in curated_fronts:
            tally.remove_for_curation(discarded)
    covered = {card.topic_key for card in curated}
    ordered = _ordered(curated, inventory.topics)
    return GeneratedDeck(
        inventory=inventory,
        cards=ordered,
        metrics=build_metrics(
            tally,
            topics=inventory.topics,
            covered_keys=covered,
            repair_attempted=repair_attempted,
        ),
        model_name=model_name(),
        prompt_version=prompt_version(),
    )


def _curated(
    cards: list[DeckCard], topics: tuple[Topic, ...], limit: int
) -> list[DeckCard]:
    """Keep a high-signal set while preserving topic breadth when possible."""

    if limit < 1:
        return []
    if len(cards) <= limit:
        return list(cards)

    position = {topic.key: topic.ordinal for topic in topics}
    required = {topic.key for topic in topics if topic.required}
    ranked = sorted(
        cards,
        key=lambda card: (
            -card.interview_priority,
            position.get(card.topic_key, len(topics)),
            card.front,
        ),
    )

    # First reserve the strongest card for as many required topics as fit.
    representatives: dict[str, DeckCard] = {}
    for card in ranked:
        if card.topic_key in required and card.topic_key not in representatives:
            representatives[card.topic_key] = card
    selected = sorted(
        representatives.values(),
        key=lambda card: (
            -card.interview_priority,
            position.get(card.topic_key, len(topics)),
            card.front,
        ),
    )[:limit]
    selected_fronts = {card.front for card in selected}

    # Use remaining capacity for the strongest complementary questions.
    for card in ranked:
        if len(selected) >= limit:
            break
        if card.front not in selected_fronts:
            selected.append(card)
            selected_fronts.add(card.front)
    return selected


def _run_batch(
    client: CardModel,
    inventory: ScopeInventory,
    batch: tuple[Topic, ...],
    *,
    settings: GenerationConfig,
    tally: ValidationTally,
    seen_fronts: set[str],
    previous_fronts: tuple[str, ...],
    repair: bool = False,
) -> list[DeckCard]:
    """One generation call, fully validated. A failed call costs its batch."""

    messages = build_card_messages(
        inventory,
        batch,
        minimum_cards=settings.minimum_cards_per_topic,
        maximum_cards=settings.maximum_cards_per_topic,
        repair=repair,
        previous_fronts=previous_fronts,
    )
    try:
        response = client.invoke(messages)
    except Exception:
        # A provider failure is not a coverage failure to hide: the topics in
        # this batch simply stay uncovered and the deck reports them.
        logger.exception(
            "deck generation call failed",
            extra={"scope_key": inventory.scope_key, "topics": len(batch)},
        )
        return []

    cards = getattr(response, "cards", None) or []
    kept: list[DeckCard] = []
    for generated in cards:
        tally.generated += 1
        topic = attribute_topic(generated, batch)
        if topic is None:
            tally.drop(DROP_OUT_OF_SCOPE)
            continue
        card, reason = validate_card(
            generated,
            topic,
            # Provisional; the final index is assigned once the deck is ordered.
            card_index=0,
            seen_fronts=seen_fronts,
        )
        if card is None:
            tally.drop(reason or DROP_OUT_OF_SCOPE)
            continue
        tally.keep(card)
        kept.append(card)
    return kept


def _ordered(
    cards: list[DeckCard], topics: tuple[Topic, ...]
) -> tuple[DeckCard, ...]:
    """Source order across topics, highest interview priority within one.

    Reading order is what a reader browsing a deck expects. The daily queue
    ignores this entirely and orders by priority across every deck, which is
    where "top questions first" actually happens.
    """

    position = {topic.key: topic.ordinal for topic in topics}
    ranked = sorted(
        cards,
        key=lambda card: (
            position.get(card.topic_key, len(topics)),
            -card.interview_priority,
            card.front,
        ),
    )
    return tuple(
        card.model_copy(update={"card_index": index})
        for index, card in enumerate(ranked)
    )
