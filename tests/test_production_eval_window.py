"""Production credential windows restore on failure; no network or cloud writes."""
from datetime import UTC, datetime, timedelta
import json
import pytest
from scripts import production_eval_window as window

@pytest.fixture
def harness(tmp_path, monkeypatch):
    state = {'key': 'normal-fixture', 'changes': [], 'usage': '0', 'fail': None}
    info = {'limit': '.75', 'limit_reset': None, 'limit_remaining': '.75', 'usage': '0',
            'expires_at': (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    monkeypatch.setattr(window, 'dotenv_values', lambda _: {'OPENROUTER_PROD_EVAL_API_KEY': 'temporary-fixture'})
    monkeypatch.setattr(window, 'variables', lambda: {'OPENROUTER_API_KEY': state['key']})
    monkeypatch.setattr(window, 'key_info', lambda _: {**info, 'usage': state['usage']})
    def switch(key):
        state['changes'].append(key)
        state['key'] = key
        if key == 'temporary-fixture' and state['fail']:
            raise state['fail']
        return 'previous-id'
    monkeypatch.setattr(window, 'switch', switch)
    monkeypatch.setattr(window, 'wait_deployment', lambda *_: {'status': 'ok', 'revision': 'fixture'})
    monkeypatch.setattr(window.time, 'sleep', lambda _: None)
    return tmp_path, state, info

@pytest.mark.parametrize('failure', [RuntimeError('operation failed'), KeyboardInterrupt()])
def test_restores_even_when_switch_is_interrupted(harness, failure):
    root, state, _ = harness
    state['fail'] = failure
    with pytest.raises(type(failure)):
        window.run_window(root, 'before')
    assert state['key'] == 'normal-fixture'
    assert state['changes'] == ['temporary-fixture', 'normal-fixture']
    proof = json.loads((root/'production-paired/before-window.json').read_text())
    assert proof['status'] == 'interrupted-restored' and not proof['tests_finished']
    assert proof['normal_key_restored']
    budget = json.loads((root/'production-paired/budget.json').read_text())
    assert budget['calls'][0]['status'] == 'reserved'
    assert (root/'production-paired/normal-key.json').stat().st_mode & 0o777 == 0o600


def test_two_completed_windows_share_cap_and_settle_only_after_restoration(harness):
    root, state, _ = harness
    directory = root/'production-paired'; directory.mkdir()
    (directory/'finish-before').touch(); (directory/'finish-after').touch()
    window.run_window(root, 'before')
    budget = json.loads((directory/'budget.json').read_text())
    assert len(budget['calls']) == 1 and budget['calls'][0]['status'] == 'reserved'
    state['usage'] = '.04'
    window.run_window(root, 'after')
    budget = json.loads((directory/'budget.json').read_text())
    assert len(budget['calls']) == 1 and budget['calls'][0]['cost_usd'] == '0.04'
    assert state['key'] == 'normal-fixture'


def test_after_cannot_start_without_successful_before(harness):
    root, state, _ = harness
    with pytest.raises(ValueError, match='before window'):
        window.run_window(root, 'after')
    assert not state['changes']


def test_expiry_and_total_cap_checked_before_cloud_mutation(harness):
    root, state, info = harness
    info['expires_at'] = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
    with pytest.raises(ValueError, match='headroom'):
        window.run_window(root, 'before')
    assert not state['changes']
