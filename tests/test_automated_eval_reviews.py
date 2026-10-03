"""Saved-output review identity, visual evidence and fail-closed resume."""
from copy import deepcopy
from hashlib import sha256
import json

import fitz
import pytest

from evals.automated_reviews import run_saved_reviews, review_verdict, saved_cases
from evals.budget import BudgetStop, atomic_json
from evals.suite_judge import pdf_pages
from tests.test_eval_reviews import make_bundle


def selection(tmp_path):
    directory = tmp_path / 'original'
    directory.mkdir()
    make_bundle(directory)
    return [{'run': 'original', 'case_id': 'sheet'}]


def test_saved_reviews_reuse_outputs_preserve_original_and_reject_tampering(tmp_path):
    chosen = selection(tmp_path)
    original = tmp_path / 'original/bundle.json'
    before = original.read_bytes()
    calls = []
    def judge(*args):
        calls.append(args[0].id)
        return {'grounding_status': 'supported', 'correctness': 4, 'coverage': 3, 'usefulness': 3,
                'layout': 'readable', 'review_kind': 'llm_judge'}
    config = {'model': 'fixture'}
    result = run_saved_reviews(chosen, tmp_path / 'review', runs_root=tmp_path, judge=judge, config=config)
    assert result['human_review_required'] is False
    assert result['cases'][0]['verdict'] == 'needs_work'  # Existing production check stays failed.
    assert original.read_bytes() == before
    run_saved_reviews(chosen, tmp_path / 'review', runs_root=tmp_path, judge=judge, config=config)
    assert calls == ['sheet']
    with pytest.raises(ValueError, match='configuration changed'):
        run_saved_reviews(chosen, tmp_path / 'review', runs_root=tmp_path, judge=judge, config={'model': 'different'})
    (tmp_path / 'original/requests/test.json').write_text('{}')
    with pytest.raises(ValueError, match='capture changed'):
        saved_cases(chosen, tmp_path)


def test_failed_judge_requires_explicit_retry_and_never_changes_source(tmp_path):
    chosen = selection(tmp_path)
    calls = []
    def fail(*args):
        calls.append('called')
        raise BudgetStop('Unpriced inference')
    result = run_saved_reviews(chosen, tmp_path / 'review', runs_root=tmp_path, judge=fail, config={})
    assert result['cases'][0]['status'] == 'blocked'
    assert 'judgment' not in result['cases'][0]
    run_saved_reviews(chosen, tmp_path / 'review', runs_root=tmp_path, judge=fail, config={})
    assert calls == ['called']
    run_saved_reviews(chosen, tmp_path / 'review', runs_root=tmp_path, judge=fail, config={}, retry_failed=True)
    assert len(calls) == 2


@pytest.mark.parametrize('change', ['outside', 'duplicate', 'pdf', 'missing_hash'])
def test_review_requires_owned_paths_and_verified_artifacts(tmp_path, change):
    chosen = selection(tmp_path)
    if change == 'outside':
        chosen[0]['run'] = '../foreign'
    elif change == 'duplicate':
        chosen.append(deepcopy(chosen[0]))
    elif change == 'pdf':
        (tmp_path / 'original/artifacts/sheet.pdf').write_bytes(b'changed')
    else:
        path = tmp_path / 'original/bundle.json'
        bundle = json.loads(path.read_text());row=bundle['cases'][0]
        row['evidence']['generation_capture_hashes'] = {}
        from evals.suite import fingerprint
        row['output_hash'] = fingerprint({key: row.get(key) for key in ('output','evidence','artifacts')})
        atomic_json(path,bundle)
    with pytest.raises(ValueError):
        saved_cases(chosen, tmp_path)


def test_pdf_review_renders_every_page_and_rejects_changed_or_excess_pages(tmp_path):
    path = tmp_path / 'artifacts/sheet.pdf';path.parent.mkdir()
    with fitz.open() as document:
        for index in range(2):
            page = document.new_page();page.insert_text((72,72),f'Visible page {index+1}')
        document.save(path)
    row = {'artifacts': {'pdf': {'path':'artifacts/sheet.pdf','sha256':sha256(path.read_bytes()).hexdigest()}}}
    images = pdf_pages(row,tmp_path)
    assert len(images)==2 and images[0]!=images[1]
    assert all(image['image_url']['url'].startswith('data:image/png;base64,') for image in images)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='PDF changed'):
        pdf_pages(row,tmp_path)
    with fitz.open() as document:
        for _ in range(7):document.new_page()
        document.save(path)
    row['artifacts']['pdf']['sha256']=sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='1–6 pages'):
        pdf_pages(row,tmp_path)


@pytest.mark.parametrize('judgment, expected', [
    ({'grounding_status':'supported','correctness':4,'coverage':3,'usefulness':3,'layout':'not_applicable'},'usable'),
    ({'grounding_status':'unsupported','correctness':4,'coverage':4,'usefulness':4},'needs_work'),
    ({'grounding_status':'supported','correctness':4,'coverage':2,'usefulness':3},'needs_work'),
    ({'grounding_status':'insufficient_evidence','correctness':4,'coverage':4,'usefulness':4},'unsure'),
    ({'grounding_status':'supported','correctness':None,'coverage':4,'usefulness':4},'unsure'),
    ({'grounding_status':'supported','correctness':4,'coverage':4,'usefulness':4,'layout':'needs_fix'},'needs_work'),
])
def test_model_verdict_does_not_hide_unsupported_or_unknown(judgment, expected):
    assert review_verdict(judgment)==expected


def test_unknown_visual_review_and_failed_contract_cannot_be_usable():
    judgment = {'grounding_status':'supported','correctness':4,'coverage':4,'usefulness':4,'layout':'unknown'}
    assert review_verdict(judgment) == 'unsure'
    assert review_verdict(judgment, flow='summary') == 'usable'
    assert review_verdict(judgment, flow='revision_sheet') == 'unsure'
    judgment['layout'] = 'not_applicable'
    assert review_verdict(judgment, {'citations_valid':False}) == 'needs_work'
    assert review_verdict(judgment, {'standalone_exact':False}) == 'usable'


def test_judge_receives_distinct_source_and_rendered_pdf_images(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from evals.suite import Case
    from evals.suite_judge import SuiteJudge
    bundle = make_bundle(tmp_path)
    row = bundle['cases'][0]
    path = tmp_path / 'artifacts/sheet.pdf'
    with fitz.open() as document:
        document.new_page().insert_text((72,72),'Derived output')
        document.save(path)
    row['artifacts']['pdf']['sha256'] = sha256(path.read_bytes()).hexdigest()
    recorded = []
    judgment = SimpleNamespace(model_dump=lambda **kwargs: {'grounding_status':'supported','correctness':3,
        'coverage':3,'usefulness':3,'layout':'readable'})
    judge = object.__new__(SuiteJudge)
    judge.model_name, judge.project, judge.client, judge.experiment_id = 'fixture', None, None, 'fixture'
    judge.blind = True
    judge.model = SimpleNamespace(invoke=lambda messages, **kwargs: (recorded.append(messages) or judgment))
    monkeypatch.setattr('langsmith.tracing_context',lambda **kwargs:nullcontext())
    result = judge(Case.model_validate(bundle['manifest']['cases'][0]),row,tmp_path)
    parts = recorded[0][1].content
    assert len([part for part in parts if part['type']=='image_url']) == 1  # Unclassified inputs are not source evidence; final PDF is output.
    assert parts[-2]['text'] == 'DERIVED OUTPUT PDF PAGES (not source evidence):'
    assert result['rendered_pdf_pages'] == 1 and result['human_review_status'] == 'optional'
    payload = json.loads(parts[0]['text'])
    assert all('model' not in context for context in payload['exact_generation_contexts'])
    assert result['writer_model_labels_hidden'] is True
    assert json.loads((tmp_path / row['request_capture'][0]).read_text())['model'] == 'fixture'


def test_interview_review_keeps_candidate_input_separate_from_feedback_artifact(tmp_path):
    from evals.suite import Case
    from evals.suite_judge import judge_payload
    capture = {'model': 'fixture', 'messages': [{'role': 'user', 'content': json.dumps({
        'question': 'Explain the consistency tradeoff', 'candidate_answer': 'It is fast.',
        'source_evidence': 'Strong consistency requires coordination.'})}]}
    path = tmp_path / 'requests/grade.json'
    atomic_json(path, capture)
    feedback = {'gaps': ['Coordination cost is missing.'], 'recommended_answer':
                'Coordination provides consistency at a latency cost.'}
    row = {'output': feedback, 'request_capture': ['requests/grade.json'], 'evidence': {
        'generation_capture_hashes': {'requests/grade.json': sha256(path.read_bytes()).hexdigest()}}}
    case = Case(id='weak-answer', flow='interview', adapter='interview_grade', title='Weak answer',
                tier='fixture', inputs={}, source={}, expected={'coverage_points': ['Coordination cost']})
    payload, images = judge_payload(case, row, tmp_path)
    assert payload['review_target'] == 'grader_feedback'
    assert payload['output'] == feedback
    original = json.loads(payload['exact_generation_contexts'][0]['messages'][0]['content'])
    assert original['candidate_answer'] == 'It is fast.'
    assert original['source_evidence'] == 'Strong consistency requires coordination.'
    assert not images
    case.adapter = 'ideal_interview'
    assert judge_payload(case, row, tmp_path)[0]['review_target'] == 'study_artifact'


def test_sheet_review_never_promotes_generated_draft_pixels_to_source(tmp_path):
    from evals.suite import Case
    from evals.suite_judge import judge_payload
    original = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,original'}}
    derived = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,generated'}}
    unknown = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,unbound'}}
    captures = [
        {'model': 'figure-reader', 'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Figure 7 [N12:P3]'}, original,
            {'type': 'text', 'text': 'Figure 8 [N99:P9]'}, unknown]}]},
        {'model': 'native-reviewer', 'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'CANONICAL SOURCE\ntext\nRENDERED CONTENT\ngenerated sheet'}, derived]}]},
    ]
    row = {'output': {}, 'evidence': {'references': {'[N12:P3]': {'page': 3}}}, 'request_capture': []}
    for index, capture in enumerate(captures):
        relative = f'requests/{index}.json'
        atomic_json(tmp_path / relative, capture)
        row['request_capture'].append(relative)
    case = Case(id='sheet', flow='revision_sheet', adapter='sheet', title='Sheet', tier='fixture', inputs={}, source={}, expected={})
    payload, images = judge_payload(case, row, tmp_path)
    assert images == [original]
    assert payload['omitted_non_source_images'] == 2
    assert 'RENDERED CONTENT' in payload['exact_generation_contexts'][1]['messages'][0]['content'][0]['text']
    assert all(part.get('type') != 'image_url' for context in payload['exact_generation_contexts'] for message in context['messages'] if isinstance(message['content'], list) for part in message['content'])


def test_trace_link_failure_preserves_paid_judgment():
    from types import SimpleNamespace
    from scripts.judge_saved_evaluations import attach_trace_link
    def unavailable():
        raise RuntimeError('Quota prevents hosted project lookup')
    review = {'coverage': 4}
    assert attach_trace_link(review,SimpleNamespace(get_url=unavailable)) == {
        'coverage': 4,'trace_url':None,'trace_link_error_kind':'RuntimeError'}


def test_recover_trace_link_failure_from_exact_captured_response(tmp_path):
    from scripts.recover_saved_judgments import recover
    chosen = selection(tmp_path)
    config={'judge_model':'fixture','review_version':'fixture-v1','writer_model_labels_hidden':True}
    def fail(*_):
        from langsmith.utils import LangSmithNotFoundError
        raise LangSmithNotFoundError('Project missing')
    directory=tmp_path/'review'
    result=run_saved_reviews(chosen,directory,runs_root=tmp_path,judge=fail,config=config)
    response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
        'grounding_status':'supported','correctness':4,'coverage':4,'usefulness':4,
        'criteria':[],'unsupported_claims':[],'limitations':[],'explanation':'Fixture','layout':'readable'})}}]}
    atomic_json(directory/'responses/one.json',response)
    digest=sha256((directory/'responses/one.json').read_bytes()).hexdigest()
    atomic_json(directory/'budget.json',{'calls':[{'case_id':'sheet','phase':'judging','status':'settled',
        'response_capture':'responses/one.json','response_sha256':digest}]})
    original=(directory/'automated_reviews.json').read_bytes()
    assert recover(directory,chosen,tmp_path)==1
    assert (directory/'automated_reviews.before-link-recovery.json').read_bytes()==original
    recovered=json.loads((directory/'automated_reviews.json').read_text())['cases'][0]
    assert recovered['status']=='completed' and recovered['judgment']['coverage']==4
    assert recovered['verdict']=='needs_work'  # Failed native check remains.
    with pytest.raises(ValueError):recover(directory,chosen,tmp_path)
