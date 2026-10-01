#!/usr/bin/env python3
"""Run or serve the isolated Stanford CME295 video-course experiment."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observability import traced
from experiments.video_course import VideoCoursePilot


SOURCE_URL = "https://www.youtube.com/watch?v=Ub3GoFaUcds"
SLIDES_URL = "https://cme295.stanford.edu/slides/fall25-cme295-lecture1.pdf?v=1761094147"
DEFAULT_ROOT = Path("evaluation/runs/video-course/cme295-lecture1")


@traced("scripts.video_course_pilot.main", flow="evaluation")
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "serve"), nargs="?", default="run")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ingestion-cap", type=float, default=0.75)
    parser.add_argument("--evaluation-cap", type=float, default=0.75)
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    if arguments.command == "serve":
        os.chdir(root)
        server = ThreadingHTTPServer(("127.0.0.1", arguments.port), SimpleHTTPRequestHandler)
        print(f"Serving http://127.0.0.1:{arguments.port}/report.html")
        server.serve_forever()
        return
    manifest = VideoCoursePilot(
        root,
        source_url=SOURCE_URL,
        slides_url=SLIDES_URL,
        ingestion_cap_usd=arguments.ingestion_cap,
        evaluation_cap_usd=arguments.evaluation_cap,
    ).run()
    print(f"Report: {root / 'report.html'}")
    print(f"Ready: {manifest.metrics['ready']}")
    print(f"Total model cost: ${manifest.metrics['total_cost_usd']:.4f}")


if __name__ == "__main__":
    main()
