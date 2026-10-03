"""View saved automated scores and optionally record separate manual labels."""
import argparse
from pathlib import Path
import secrets

import uvicorn

from evals.review_server import create_app
from evals.reviews import load_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535")
    directory = args.directory.resolve()
    root = Path(__file__).resolve().parents[1] / "evaluation" / "runs"
    if not directory.is_relative_to(root):
        parser.error("Review only private experiments inside evaluation/runs/")
    load_bundle(directory)
    token = secrets.token_urlsafe(32)
    print(f"Review saved outputs: http://127.0.0.1:{args.port}/?token={token}", flush=True)
    uvicorn.run(create_app(directory, token=token, port=args.port), host="127.0.0.1", port=args.port,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
