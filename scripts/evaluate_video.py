"""Run the frozen lecture conversations through the real video coordinator."""

import argparse
from datetime import datetime
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from evals.judge import OpenRouterAnswerJudge
from evals.video import (
    VideoProjectRunner,
    evaluate_retrieval_only,
    evaluate_video_conversations,
)
from evals.video_report import render_video_report
from storage.database import connection, environment_owner_id, parse_owner_id
from video.answers import VideoAnswerDependencies
from video.embeddings import OpenRouterRegionEmbedder, OpenRouterTextEmbedder
from video.media_store import FilesystemMediaStore, MediaStoreError


DEFAULT_GOLD = Path("evaluation/video_gold.json")
logger = logging.getLogger(__name__)


def _arguments():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument(
        "--conversation",
        action="append",
        help="Repeatable. Run only these conversation IDs.",
    )
    selection.add_argument(
        "--smoke",
        action="store_true",
        help="vc-002 and vc-006: one rewriting thread and the abstentions.",
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--database-url")
    parser.add_argument("--owner-id", help="Owner UUID; defaults to DEFAULT_OWNER_ID")
    parser.add_argument("--video-id", help="Override the lecture in the gold set")
    parser.add_argument(
        "--judge-answers",
        action="store_true",
        help="Add the optional semantic rubric (extra calls and cost).",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help=(
            "Score retrieval against the anchors using each turn's gold "
            "rewrite. No generation, no routing: one query embedding per turn. "
            "This is the loop to iterate retrieval changes in."
        ),
    )
    parser.add_argument(
        "--no-rewrite-ablation",
        action="store_true",
        help="Skip the three-arm rewriting replay (saves two retrievals per probe).",
    )
    return parser.parse_args()


SMOKE = ("vc-002", "vc-006")


def _select(dataset, args):
    conversations = dataset["conversations"]
    if args.all:
        return conversations
    wanted = set(SMOKE if args.smoke else args.conversation)
    selected = [item for item in conversations if item["id"] in wanted]
    missing = wanted - {item["id"] for item in selected}
    if missing:
        raise SystemExit("Unknown selection: " + ", ".join(sorted(missing)))
    return selected


def _dependencies() -> VideoAnswerDependencies:
    """The same retrieval space the published version was indexed in.

    Mirrors `api.video_chat._answer_dependencies` deliberately. An evaluation
    that retrieves differently from production measures something production
    never does.
    """

    try:
        text_embedder = OpenRouterTextEmbedder()
        image_embedder = OpenRouterRegionEmbedder()
    except ValueError:
        logger.warning(
            "No OPENROUTER_API_KEY: retrieval will be lexical only, which is "
            "not what production does. Recall figures are not comparable."
        )
        text_embedder = image_embedder = None
    try:
        store = FilesystemMediaStore()
    except MediaStoreError:
        store = None
    if store is None or not any(store.root.iterdir()):
        # Frame images live on the deployed volume. Running against the hosted
        # database from a laptop retrieves visual evidence and its OCR text but
        # cannot attach the images themselves, so visual answers are weaker
        # here than in production. Said out loud rather than discovered later.
        logger.warning(
            "No local video media: visual evidence will be supplied as text "
            "only, without the frame images production attaches."
        )
        store = None
    return VideoAnswerDependencies(
        media_store=store,
        text_embedder=text_embedder,
        image_embedder=image_embedder,
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    args = _arguments()
    owner_id = (
        parse_owner_id(args.owner_id) if args.owner_id else environment_owner_id()
    )
    dataset = json.loads(args.gold.read_text(encoding="utf-8"))
    selected = _select(dataset, args)
    lecture = dataset["lecture"]
    output = args.output or Path("evaluation/runs/video") / datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )
    output.mkdir(parents=True, exist_ok=True)

    with connection(args.database_url) as database:
        runner = VideoProjectRunner(
            connection=database,
            owner_id=owner_id,
            video_id=args.video_id or lecture["video_id"],
            video_title=lecture["title"],
            dependencies=_dependencies(),
        )
        if args.retrieval_only:
            evaluation = evaluate_retrieval_only(
                selected,
                runner,
                on_turn=lambda turn_id: print(f"{turn_id}...", flush=True),
            )
            output.joinpath("retrieval.json").write_text(
                json.dumps(evaluation, indent=2), encoding="utf-8"
            )
            print(json.dumps(evaluation["summary"], indent=2))
            for row in evaluation["turns"]:
                if row["recall"] < 1.0:
                    print(
                        f"  {row['turn_id']:12s} recall={row['recall']:.2f} "
                        f"{row['modalities']}"
                    )
            print(f"Results: {output / 'retrieval.json'}")
            return
        evaluation = evaluate_video_conversations(
            selected,
            runner,
            answer_judge=OpenRouterAnswerJudge() if args.judge_answers else None,
            run_rewrite_ablation=not args.no_rewrite_ablation,
            on_turn=lambda turn_id: print(f"Running {turn_id}...", flush=True),
        )

    evaluation["dataset"] = {
        "dataset_id": dataset["dataset_id"],
        "version": dataset["version"],
        "review_status": dataset["review"]["status"],
        "conversations": [item["id"] for item in selected],
    }
    results = output / "results.json"
    results.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    report = render_video_report(evaluation, output / "report.html")

    print(json.dumps(evaluation["summary"], indent=2))
    print(json.dumps(evaluation["summary_coverage"], indent=2))
    print(json.dumps(evaluation["rewrite_ablation"], indent=2))
    print(f"Results: {results}")
    print(f"Report:  {report}")


if __name__ == "__main__":
    main()
