"""Run transcript-informed ideal-flow evaluations as a LangSmith experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

from observability import traced
from decks.topics import ScopeInventory, Topic
from evals.ideal_interview import langsmith_evaluators, semantic_quality_evaluator
from interviews.ideal_generation import generate_ideal_exchange
from interviews.models import structured_model
from interviews.ideal_contracts import IdealInterviewExchangeDraft


DEFAULT_SEED = Path("evaluation/ideal_interview_flow_seed.json")


def inventory(inputs: dict) -> ScopeInventory:
    topics = tuple(
        Topic(
            key=item["key"],
            ordinal=index,
            label=item["label"],
            required=True,
            evidence_text=f'{item["marker"]}\n{item["evidence"]}',
            allowed_markers=frozenset({item["marker"]}),
            node_id=int(item["marker"].split(":", 1)[0][2:]),
            start_page=int(item["marker"].split("P", 1)[1][:-1]),
            end_page=int(item["marker"].split("P", 1)[1][:-1]),
        )
        for index, item in enumerate(inputs["topics"])
    )
    return ScopeInventory(
        source_kind="book",
        scope_key=f'eval:{inputs["scope_title"]}',
        title=inputs["scope_title"],
        source_title=inputs["source_title"],
        outline="\n".join(f"- {item.label}" for item in topics),
        topics=topics,
    )


def target(inputs: dict) -> dict:
    source = inventory(inputs)
    exchanges = []
    model = structured_model(IdealInterviewExchangeDraft, temperature=0.25)
    for index, topic in enumerate(source.topics):
        exchange, _ = generate_ideal_exchange(
            inventory=source,
            topic=topic,
            interview_format=inputs["interview_format"],
            target_level=inputs["target_level"],
            index=index,
            previous=exchanges,
            model=model,
        )
        exchanges.append(exchange)
    return {"exchanges": [item.model_dump(mode="json") for item in exchanges]}


def sync_dataset(client: Client, seed: dict, dataset_name: str) -> None:
    existing = next(iter(client.list_datasets(dataset_name=dataset_name)), None)
    dataset = existing or client.create_dataset(
        dataset_name=dataset_name,
        description=seed["methodology"],
    )
    existing_examples = list(client.list_examples(dataset_id=dataset.id))
    if existing_examples:
        return
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "inputs": case["inputs"],
                "outputs": case["reference_outputs"],
                "metadata": {"case_id": case["id"], "sources": seed["sources"]},
            }
            for case in seed["cases"]
        ],
    )


@traced("scripts.evaluate_ideal_interview_flows.main", flow="evaluation")
def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--dataset", default="ideal-chapter-interview-v1")
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument("--judge", action="store_true")
    args = parser.parse_args()
    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    client = Client()
    if not args.no_sync:
        sync_dataset(client, seed, args.dataset)
    evaluators = langsmith_evaluators()
    if args.judge:
        evaluators.append(semantic_quality_evaluator())
    results = client.evaluate(
        target,
        data=args.dataset,
        evaluators=evaluators,
        experiment_prefix="ideal-chapter-interview",
        max_concurrency=2,
        metadata={"seed": seed["dataset_id"], "review_status": seed["review_status"]},
    )
    print(results)


if __name__ == "__main__":
    load_dotenv()
    main()
