"""Review saved five-flow outputs with Luna; no regeneration or manual task."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, default=root / 'evaluation/automated_review_selection.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-usd', default='1')
    parser.add_argument('--judge-model', default='openai/gpt-6-luna')
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    runs = root / 'evaluation/runs'
    if not output.is_relative_to(runs):
        parser.error('--output must remain inside evaluation/runs/')
    selection = json.loads(args.selection.read_text())['cases']
    from evals.automated_reviews import run_saved_reviews
    from evals.budget import Budget, pricing_snapshot
    from evals.suite_judge import REVIEW_VERSION, SuiteJudge
    from langsmith import Client, tracing_context
    from observability import flush_traces, safe_value, span
    project = f'study-partner-evals-{output.name}'
    config = {'judge_model': args.judge_model, 'review_version': REVIEW_VERSION,
              'project': project, 'review_kind': 'llm_judge', 'human_calibration': False,
              'implementation': {name: sha256((root / name).read_bytes()).hexdigest() for name in
                                 ['evals/automated_reviews.py', 'evals/suite_judge.py']}}
    budget = Budget(output, cap=args.max_usd, prices=None if (output / 'budget.json').exists() else pricing_snapshot())
    client = Client(anonymizer=safe_value)
    native = SuiteJudge(model=args.judge_model, project=project, client=client, experiment_id=output.name)
    def judge(case, row, source):
        print(f'Reviewing {case.id} ({case.flow}), saved output only', flush=True)
        with tracing_context(project_name=project, client=client, enabled=True,
                             tags=['evaluation', 'automated_review', case.flow]):
            with span('evaluation.case.review', metadata={'case_id': case.id, 'flow': case.flow,
                       'output_hash': row['output_hash'], 'experiment_id': output.name}) as run:
                review = native(case, row, source)
                review['trace_url'] = run.get_url() if run else None
                return review
    with budget.guard():
        result = run_saved_reviews(selection, output, runs_root=runs, judge=judge, config=config,
                                   budget=budget, retry_failed=args.retry_failed)
    flush_traces()
    print(json.dumps({'statuses': {row['case_id']: row['status'] for row in result['cases']},
                      'budget': result['budget'], 'manual_review_required': False}, indent=2))
    if any(row['status'] != 'completed' for row in result['cases']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
