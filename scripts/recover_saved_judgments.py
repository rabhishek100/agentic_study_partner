"""Recover captured paid judgments discarded only by hosted trace-link lookup."""
from hashlib import sha256
import argparse
import json
from pathlib import Path
from evals.automated_reviews import review_verdict, saved_cases
from evals.budget import atomic_json, experiment_lock
from evals.suite_judge import SuiteJudgment


def recover(directory, selection, runs_root):
    sources = saved_cases(selection, runs_root)
    path = directory / 'automated_reviews.json'
    with experiment_lock(directory):
        result = json.loads(path.read_text())
        calls = json.loads((directory / 'budget.json').read_text())['calls']
        failed = [(i, row) for i, row in enumerate(result['cases']) if row['status'] == 'failed']
        if any(row.get('error', {}).get('kind') != 'LangSmithNotFoundError' for _, row in failed):
            raise ValueError('Recovery is limited to trace-link project lookup failures')
        if len(calls) != len(failed):
            raise ValueError('Every failed review must have exactly one captured request')
        amended = []
        for call, (index, row) in zip(calls, failed):
            source, _, case, original = sources[index]
            if (call['case_id'] != row['case_id'] or call['phase'] != 'judging' or call['status'] != 'settled'
                    or row['output_hash'] != original['output_hash'] or row['run'] != source.name):
                raise ValueError('Paid review cannot be bound to its immutable output')
            response_path = (directory / call['response_capture']).resolve()
            if not response_path.is_relative_to(directory.resolve() / 'responses'):
                raise ValueError('Response must remain in private capture directory')
            raw = response_path.read_bytes()
            if sha256(raw).hexdigest() != call['response_sha256']:
                raise ValueError('Frozen response changed')
            response = json.loads(raw)
            choice = response['choices'][0]
            if choice['finish_reason'] != 'stop':
                raise ValueError('Incomplete review response')
            judgment = SuiteJudgment.model_validate_json(choice['message']['content']).model_dump(mode='json')
            judgment.update(judge_model=result['config']['judge_model'], review_kind='llm_judge',
                            review_version=result['config']['review_version'], trace_url=None,
                            writer_model_labels_hidden=result['config']['writer_model_labels_hidden'],
                            independence='automated judgment; not human-calibrated',
                            recovered_from_response_sha256=call['response_sha256'],
                            trace_link_error_kind='LangSmithNotFoundError')
            row.update(status='completed', judgment=judgment,
                       verdict=review_verdict(judgment, original.get('checks'), flow=case.flow))
            row.pop('error', None)
            amended.append({'index':index,'source_run':row['run'],'case_id':case.id,
                            'output_hash':row['output_hash'],'response_sha256':call['response_sha256']})
        backup = directory / 'automated_reviews.before-link-recovery.json'
        if backup.exists():
            raise ValueError('A recovery journal already exists; do not overwrite')
        atomic_json(backup,json.loads(path.read_text()))
        atomic_json(directory / 'trace-link-recovery.json',{'original_sha256':sha256(path.read_bytes()).hexdigest(),
                   'recovered':amended,'inference_requests':0})
        atomic_json(path,result)
    return len(amended)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--selection',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'evaluation/runs'
    directory=args.directory.resolve()
    if not directory.is_relative_to(root):parser.error('Private run directory required')
    print('Recovered',recover(directory,json.loads(args.selection.read_text())['cases'],root),'saved judgments without inference')
if __name__=='__main__':main()
