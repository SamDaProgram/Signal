"""Timed sequence scheduling, cancellation and run history."""

import copy
import threading
import time
import uuid

from .validation import validate_rule


class SequencePlaybackMixin:
    """Timed sequence scheduling, cancellation and run history. Uses the Bridge runtime and its shared lock."""

    def start_sequence(self, rule, reason):
        running = [run for run in self.sequence_runs if run['status'] == 'running']
        if any(run['rule_id'] == rule['id'] for run in running):
            self.log(rule, reason, 'skipped', 'This sequence is already running; retrigger ignored.')
            return 'skipped'
        if len(running) >= 8:
            raise ValueError('Eight sequences are already running; trigger skipped.')
        total = 0
        steps = []
        for step in copy.deepcopy(rule['steps']):
            total += step['delay_ms']
            steps.append(dict(config=step, at_ms=total, status='pending', detail=''))
        run = dict(id=str(uuid.uuid4()), rule_id=rule['id'], name=rule['name'], status='running',
                   started=time.monotonic(), ended=None, duration_ms=total, steps=steps,
                   on_error=rule['sequence_on_error'], stop=threading.Event(), detail='Waiting for steps')
        # Retain bounded history without discarding active runs.
        while len(self.sequence_runs) >= 32:
            old = next((item for item in self.sequence_runs if item['status'] != 'running'), None)
            if old is None:
                break
            self.sequence_runs.remove(old)
        self.sequence_runs.insert(0, run)
        worker = threading.Thread(target=self.run_sequence, args=(run,), daemon=True)
        self.sequence_threads.add(worker)
        try:
            worker.start()
        except RuntimeError:
            self.sequence_threads.discard(worker)
            self.sequence_runs.remove(run)
            raise
        self.last_sent[rule['id']] = time.monotonic()
        self.log(rule, reason, 'running', f"Sequence started · {len(steps)} steps")
        return 'running'

    def finish_sequence(self, run, status, detail):
        # Caller holds self.lock. A canceled run can never resume after re-arming.
        if run['status'] != 'running':
            return
        run.update(status=status, detail=detail, ended=time.monotonic())
        run['stop'].set()
        for step in run['steps']:
            if step['status'] == 'pending':
                step.update(status='skipped', detail=detail)
        self.log(run, 'Sequence', status, detail)

    def cancel_sequences(self, reason, rule_id=None):
        for run in self.sequence_runs:
            if rule_id is not None and run['rule_id'] != rule_id:
                continue
            self.finish_sequence(run, 'canceled', reason + '; pending steps canceled. Running scripts finish.')

    def cancel_sequence(self, run_id):
        with self.lock:
            run = next((item for item in self.sequence_runs if item['id'] == run_id), None)
            if run is None:
                raise KeyError('Sequence run not found.')
            self.finish_sequence(run, 'canceled', 'Pending steps canceled. Running scripts finish.')
            return {'ok': True}

    def sequence_step_done(self, run, step, status, detail):
        with self.lock:
            step.update(status=status, detail=detail)
            if status in ('error', 'skipped') and run['on_error'] == 'stop':
                self.finish_sequence(run, 'error', f"Stopped at {step['config']['name']}: {detail}")

    def run_sequence(self, run):
        try:
            for index, step in enumerate(run['steps'], 1):
                remaining = run['started'] + step['at_ms'] / 1000 - time.monotonic()
                if run['stop'].wait(max(0, remaining)):
                    return
                with self.lock:
                    if run['status'] != 'running' or not self.armed:
                        self.finish_sequence(run, 'canceled', 'Outputs paused')
                        return
                    run['detail'] = f"Step {index} of {len(run['steps'])}: {step['config']['name']}"
                    step['status'] = 'running'
                    action = validate_rule(step['config'])
                    action['id'] = f"{run['id']}:{index}"
                    callback = lambda status, detail, step=step: self.sequence_step_done(run, step, status, detail)
                    status = self.send(action, f"{run['name']} · Step {index}", on_complete=callback,
                                       permit=lambda: run['status'] == 'running')
                    if status != 'running':
                        self.sequence_step_done(run, step, status, self.events[0]['detail'])
                    # One-use step IDs must not accumulate in the cooldown cache.
                    self.last_sent.pop(action['id'], None)
            while True:
                with self.lock:
                    if run['status'] != 'running':
                        return
                    if not any(step['status'] == 'running' for step in run['steps']):
                        failed = any(step['status'] in ('error', 'skipped') for step in run['steps'])
                        self.finish_sequence(run, 'error' if failed else 'completed',
                                             'Sequence finished with errors' if failed else 'All sequence steps completed')
                        return
                    run['detail'] = 'Timeline finished; waiting for scripts to complete'
                if run['stop'].wait(.05):
                    return
        except Exception as exc:
            with self.lock:
                self.finish_sequence(run, 'error', str(exc))
        finally:
            with self.lock:
                self.sequence_threads.discard(threading.current_thread())

    def sequence_snapshot(self):
        now = time.monotonic()
        return [dict(id=run['id'], rule_id=run['rule_id'], name=run['name'], status=run['status'],
                     duration_ms=run['duration_ms'], elapsed_ms=round(((run['ended'] or now) - run['started']) * 1000),
                     detail=run['detail'], steps=[dict(name=step['config']['name'], action=step['config']['action'],
                           at_ms=step['at_ms'], status=step['status'], detail=step['detail']) for step in run['steps']])
                for run in self.sequence_runs]

