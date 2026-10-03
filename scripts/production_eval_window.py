"""Brief capped-key production windows, with durable restoration and $5 accounting.

Operator drives the signed-in browser after READY, then creates finish-<stage>
in the private directory. Before/after share one $0.75 reservation, held until
both windows finish. An interruption restores the original Railway key; unknown
billing remains reserved. This script never generates test content itself.
"""
import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import httpx
from dotenv import dotenv_values
from evals.budget import Budget, atomic_json, money
from evals.round_budget import RoundBudget

API = 'https://api-production-08e6b.up.railway.app'
RAILWAY = '/Users/abhishek/.nvm/versions/node/v22.18.0/bin/railway'


def cli(arguments, value=None):
    result = subprocess.run([RAILWAY, *arguments], input=value, text=True, capture_output=True)
    if result.returncode:
        # CLI diagnostics can echo variable values; do not expose them.
        raise RuntimeError('Railway operation failed; private deployment diagnostics need inspection')
    return result.stdout


def variables():
    return json.loads(cli(['variable', 'list', '--service', 'api', '--environment', 'production', '--json']))


def switch(key):
    previous = json.loads(cli(['deployment', 'list', '--service', 'api', '--environment', 'production', '--limit', '1', '--json']))[0]['id']
    cli(['variable', 'set', '--service', 'api', '--environment', 'production', '--skip-deploys', '--stdin', 'OPENROUTER_API_KEY'], key)
    cli(['redeploy', '--service', 'api', '--environment', 'production', '--yes'])
    return previous


def key_info(key):
    with httpx.Client(timeout=30) as client:
        response = client.get('https://openrouter.ai/api/v1/key', headers={'Authorization': 'Bearer ' + key})
        response.raise_for_status()
        data = response.json()['data']
    return {name: data.get(name) for name in ('limit', 'limit_reset', 'limit_remaining', 'usage', 'expires_at')}


def wait_deployment(directory, label, previous=None):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        deployments = json.loads(cli(['deployment', 'list', '--service', 'api', '--environment', 'production', '--limit', '1', '--json']))
        latest = deployments[0]
        atomic_json(directory / (label + '-deployment.json'), latest)
        if latest['id'] == previous:
            time.sleep(3)
            continue
        if latest['status'] in {'FAILED', 'CRASHED', 'REMOVED'}:
            raise RuntimeError('Production redeployment failed; restore the normal key')
        if latest['status'] == 'SUCCESS':
            response = httpx.get(API + '/api/health', timeout=30)
            response.raise_for_status()
            health = response.json()
            atomic_json(directory / (label + '-health.json'), health)
            return health
        time.sleep(3)
    raise RuntimeError('Production redeployment did not become ready in ten minutes')


def run_window(round_directory, stage, *, minutes=35, restore_only=False):
    directory = round_directory / 'production-paired'
    values = dotenv_values(Path(__file__).resolve().parents[1] / '.env')
    key = values['OPENROUTER_PROD_EVAL_API_KEY']
    backup = directory / 'normal-key.json'
    if restore_only:
        if not backup.exists():
            raise ValueError('No recorded normal key to restore')
        previous = switch(json.loads(backup.read_text())['key'])
        wait_deployment(directory, 'emergency-restoration', previous)
        print('Normal production key restored', flush=True)
        return
    if stage == 'after':
        before = directory / 'before-window.json'
        if not before.exists() or not json.loads(before.read_text()).get('tests_finished') or not json.loads(before.read_text()).get('normal_key_restored'):
            raise ValueError('A completed and restored before window is required')
    with RoundBudget(round_directory).lease(directory, phase='production', cap='0.75'):
        budget = Budget(directory, cap='0.75', prices={'models': {}})
        info = key_info(key)
        if money(info['limit']) != Decimal('0.75') or info['limit_reset'] is not None:
            raise ValueError('Production key must have exactly the approved total cap and no reset')
        if datetime.fromisoformat(info['expires_at'].replace('Z', '+00:00')) <= datetime.now(UTC) + timedelta(minutes=minutes + 5):
            raise ValueError('Temporary production key lacks enough expiry headroom')
        if not budget.data['calls']:
            if money(info['usage']) != 0:
                raise ValueError('New production key must have zero usage before the shared reservation')
            budget.reserve('0.75', model='openrouter-production-comparisons', request={'scope': 'both real-account before/after windows', 'key_info': info})
        elif len(budget.data['calls']) != 1 or budget.data['calls'][0]['status'] != 'reserved':
            raise ValueError('The shared production reservation has already been settled or changed')
        current = variables()['OPENROUTER_API_KEY']
        if not backup.exists():
            atomic_json(backup, {'key': current})
        normal = json.loads(backup.read_text())['key']
        if current != normal:
            raise ValueError('Current Railway key differs from the saved normal key; inspect before changing it')
        state_path = directory / (stage + '-window.json')
        if state_path.exists():
            raise ValueError('This production window already exists; preserve its evidence')
        state = {'stage': stage, 'started_at': datetime.now(UTC).isoformat(), 'status': 'switching', 'key_before': info}
        atomic_json(state_path, state)
        switched = False
        state['tests_finished'] = False
        try:
            switched = True  # Restore even if the CLI is interrupted during the change.
            previous = switch(key)
            health = wait_deployment(directory, stage, previous)
            if variables()['OPENROUTER_API_KEY'] != key:
                raise RuntimeError('Railway temporary-key readback did not match')
            state.update(status='ready', ready_at=datetime.now(UTC).isoformat(), health=health)
            atomic_json(state_path, state)
            print('READY: capped real-account production window ' + stage, flush=True)
            deadline = time.monotonic() + minutes * 60
            while not (directory / ('finish-' + stage)).exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Production test window timed out')
                time.sleep(0.5)
            state['tests_finished'] = True
        except BaseException as error:
            state['failure_kind'] = type(error).__name__
            atomic_json(state_path, state)
            raise
        finally:
            if switched:
                previous = switch(normal)
                restored = wait_deployment(directory, stage + '-restored', previous)
                if variables()['OPENROUTER_API_KEY'] != normal:
                    raise RuntimeError('Normal-key restoration readback failed; restore before continuing')
                state.update(normal_key_restored=True, restored_health=restored,
                             ended_at=datetime.now(UTC).isoformat(),
                             status='closed' if state['tests_finished'] else 'interrupted-restored')
                # All prompted calls/jobs must have finished before the operator
                # closes the window. Provider key usage is aggregate, not per-flow.
                state['key_after'] = key_info(key)
                atomic_json(state_path, state)
                print('Normal production key restored after ' + stage, flush=True)
        if stage == 'after':
            # Hold the full reserve until the final restored-key observation.
            time.sleep(3)
            final = key_info(key)
            if final['usage'] != state['key_after']['usage']:
                atomic_json(directory / 'billing-unresolved.json', {'last': final, 'previous': state['key_after']})
                print('Usage still changing; production reserve remains held', flush=True)
                return
            budget.settle(budget.data['calls'][0]['id'], {'cost': final['usage']})
            atomic_json(directory / 'final-key-usage.json', final)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', type=Path, default=root / 'evaluation/runs/round3')
    parser.add_argument('--stage', choices=['before', 'after'], required=True)
    parser.add_argument('--restore-only', action='store_true')
    args = parser.parse_args()
    directory = args.round.resolve()
    if not directory.is_relative_to((root / 'evaluation/runs').resolve()):
        parser.error('Production proof and credentials must remain in ignored evaluation/runs/')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    run_window(directory, args.stage, restore_only=args.restore_only)


if __name__ == '__main__':
    main()
