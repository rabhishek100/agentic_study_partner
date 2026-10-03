"""Resumable LLM reviews of immutable saved outputs, without regeneration."""
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from evals.budget import BudgetStop, atomic_json, experiment_lock
from evals.reviews import load_bundle
from evals.suite import Case, fingerprint


def saved_cases(selection, runs_root):
    root = Path(runs_root).resolve()
    result, seen = [], set()
    for item in selection:
        directory = (root / item['run']).resolve()
        if not directory.is_relative_to(root):
            raise ValueError('Saved review source must remain inside evaluation/runs/')
        identity = (item['run'], item['case_id'])
        if identity in seen:
            raise ValueError('Duplicate saved review case')
        seen.add(identity)
        bundle = load_bundle(directory)
        row = next(row for row in bundle['cases'] if row['id'] == item['case_id'])
        if row.get('output') is None:
            raise ValueError('Automated review requires a generated output')
        captures = row.get('request_capture', [])
        hashes = row.get('evidence', {}).get('generation_capture_hashes', {})
        if not captures or any(path not in hashes for path in captures):
            raise ValueError('Automated review requires verified generation captures')
        for relative in captures:
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory / 'requests') or sha256(path.read_bytes()).hexdigest() != hashes[relative]:
                raise ValueError('Frozen generation capture changed')
        for artifact in row.get('artifacts', {}).values():
            path = (directory / artifact['path']).resolve()
            if not path.is_relative_to(directory / 'artifacts') or sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                raise ValueError('Frozen artifact changed')
        case = Case.model_validate(next(case for case in bundle['manifest']['cases'] if case['id'] == row['id']))
        result.append((directory, bundle['fingerprint'], case, row))
    return result


def review_verdict(judgment, checks=None, *, flow=None):
    if any(value is False for key, value in (checks or {}).items() if key != 'standalone_exact'):
        return 'needs_work'
    if judgment.get('grounding_status') == 'unsupported' or judgment.get('layout') == 'needs_fix':
        return 'needs_work'
    scores = [judgment.get(key) for key in ('correctness', 'coverage', 'usefulness')]
    if any(score is not None and score < 3 for score in scores):
        return 'needs_work'
    layout_unknown = judgment.get('layout') == 'unknown' and (flow is None or flow == 'revision_sheet')
    if judgment.get('grounding_status') != 'supported' or layout_unknown or any(score is None for score in scores):
        return 'unsure'
    return 'usable'


def run_saved_reviews(selection, directory, *, runs_root, judge, config, budget=None, retry_failed=False):
    sources = saved_cases(selection, runs_root)
    references = [{'run': source.name, 'experiment_fingerprint': experiment, 'case_id': case.id,
                   'flow': case.flow, 'output_hash': row['output_hash']}
                  for source, experiment, case, row in sources]
    identity = fingerprint({'sources': references, 'config': config})
    path = Path(directory) / 'automated_reviews.json'
    with experiment_lock(directory):
        import json
        result = json.loads(path.read_text()) if path.exists() else {
            'version': 'automated-review-v1', 'fingerprint': identity, 'config': config,
            'human_review_required': False, 'cases': [{**ref, 'status': 'queued'} for ref in references]}
        if result['fingerprint'] != identity:
            raise ValueError('Saved outputs or reviewer configuration changed; use a new review experiment')
        def save():
            if budget:
                result['budget'] = {'cap_usd': budget.data['cap_usd'], 'committed_usd': str(budget.committed),
                    'reported_usd': str(sum((Decimal(str(call.get('cost_usd', 0))) for call in budget.data['calls']), Decimal(0))),
                    'unknown_reservations': sum(call['status'] == 'reserved' for call in budget.data['calls'])}
            atomic_json(path, result)
        save()
        for entry, (source, _, case, row) in zip(result['cases'], sources):
            if entry['status'] == 'completed':
                continue
            if entry['status'] in {'judging', 'failed', 'blocked'} and not retry_failed:
                continue
            if budget:
                budget.case_id, budget.phase = case.id, 'judging'
            entry.update(status='judging')
            save()
            try:
                judgment = judge(case, row, source)
                entry.update(status='completed', judgment=judgment,
                             verdict=review_verdict(judgment, row.get('checks'), flow=case.flow))
                entry.pop('error', None)
                save()
            except BudgetStop as error:
                entry.update(status='blocked', error={'kind': 'budget', 'message': str(error)})
                save()
                break
            except Exception as error:
                entry.update(status='failed', error={'kind': type(error).__name__, 'message': str(error)})
                save()
        return result
