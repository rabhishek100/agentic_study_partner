"""Recover complete saved sheets rejected by the timing-field reporter bug.

No generation, rendering, inference or source mutation. Preserve the original
bundle and hashes in an amendment journal before restoring saved artifact rows.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from dotenv import load_dotenv
from evals.budget import atomic_json, experiment_lock
from evals.suite import Case, fingerprint, summary, coverage, Manifest


def recover(directory):
    from evals.adapters import NativeAdapters, remap_gold
    from revision_sheets.contracts import Sheet
    path = directory / 'bundle.json'
    with experiment_lock(directory):
        bundle = json.loads(path.read_text())
        native = NativeAdapters(bundle['config']['bindings'], retrieval_mode=bundle['config']['retrieval_mode'])
        calls = json.loads((directory / 'budget.json').read_text())['calls']
        manifest = Manifest.model_validate(bundle['manifest'])
        by_id = {case.id: case for case in manifest.cases}
        changes = []
        for row in bundle['cases']:
            if row.get('output') is not None or row['flow'] != 'revision_sheet':
                continue
            if row.get('error') != {'kind': 'ValueError', 'message': 'Adapter returned unknown or identity-overwriting fields'}:
                continue
            case = by_id[row['id']]
            base = directory / 'artifacts' / case.id
            files = ['draft.json', 'source.json', 'provenance.json', 'sheet.pdf', 'trace.json']
            if not all((base / name).is_file() for name in files):
                continue
            if any(call['status'] != 'settled' for call in calls if call['case_id'] == case.id):
                raise ValueError('Incomplete provider receipts cannot establish complete generation')
            hashes = {name: sha256((base / name).read_bytes()).hexdigest() for name in files}
            book = native.book_binding(case)  # Checks unchanged canonical source and ownership.
            native.dataset(case)
            sheet = Sheet.model_validate_json((base / 'draft.json').read_text())
            source = json.loads((base / 'source.json').read_text())
            provenance = json.loads((base / 'provenance.json').read_text())
            trace = json.loads((base / 'trace.json').read_text())
            if not provenance.get('review_history') or not (base / 'sheet.pdf').read_bytes().startswith(b'%PDF-'):
                raise ValueError('Final rendered and reviewed artifact is required')
            captures = trace['request_capture']
            value = {'output': sheet.model_dump(mode='json'),
                     'evidence': {'text': source['text'], 'references': source['references'],
                                  'bound_expected': remap_gold(case.expected, book),
                                  'generation_capture_hashes': {rel: sha256((directory / rel).read_bytes()).hexdigest() for rel in captures}},
                     'artifacts': {key: {'path': (base / name).relative_to(directory).as_posix(),
                                        'sha256': hashes[name], 'bytes': (base / name).stat().st_size}
                                   for key, name in [('pdf', 'sheet.pdf'), ('provenance', 'provenance.json')]},
                     'source_fingerprint': source['fingerprint'],
                     'checks': {'production_findings_clear': not provenance.get('outstanding_findings'),
                                'independent_concept_coverage': None, 'human_layout_review': None},
                     'trace_id': trace['trace_id'], 'trace_url': trace['trace_url'], 'request_capture': captures,
                     'metrics': {'source': 'langsmith_readback', 'status': 'unavailable', 'local_sdk_span': trace.get('local_sdk_span')}}
            changes.append({'case_id': case.id, 'artifact_hashes': hashes, 'restored_value': value})
        if not changes:
            return []
        backup = directory / 'bundle.before-sheet-recovery.json'
        if backup.exists():
            raise ValueError('Recovery is already recorded; do not overwrite its audit trail')
        atomic_json(backup, bundle)
        atomic_json(directory / 'sheet-recovery.json', {'reason': 'Reporter top-level timing field rejected completed adapter return',
            'original_bundle_sha256': sha256(backup.read_bytes()).hexdigest(), 'new_inference_calls': 0, 'changes': changes})
        rows = {row['id']: row for row in bundle['cases']}
        for change in changes:
            row = rows[change['case_id']]
            row.update(change['restored_value'], status='generated', error=None)
            row['output_hash'] = fingerprint({key: row.get(key) for key in ('output', 'evidence', 'artifacts')})
        bundle['summary'] = summary(bundle)
        bundle['coverage'] = coverage(manifest, bundle)
        atomic_json(path, bundle)
        return [item['case_id'] for item in changes]


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    if not directory.is_relative_to((root / 'evaluation/runs').resolve()):
        parser.error('Private recovery must remain inside evaluation/runs/')
    print(json.dumps({'recovered': recover(directory)}))


if __name__ == '__main__':
    main()
