import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from app import Bridge, ShowFiles, make_handler, validate_rule
from test_bridge import packet


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ShowFiles(Path(self.temp.name) / 'shows')
        self.bridge = self.store.bridge
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.settimeout(1)
        self.step = dict(action='osc', name='First', delay_ms=0, host='127.0.0.1',
                         port=self.listener.getsockname()[1], address='/first', args=[])
        self.raw = dict(name='Cue sequence', action='sequence', sequence_on_error='stop',
                        steps=[self.step, dict(self.step, name='Second', delay_ms=150, address='/second')])

    def tearDown(self):
        self.bridge.close()
        for thread in list(self.bridge.sequence_threads):
            thread.join(1)
        self.listener.close()
        self.temp.cleanup()

    def wait_finished(self, run, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.bridge.lock:
                if run['status'] != 'running':
                    return run['status']
            time.sleep(.005)
        self.fail('Sequence did not finish')

    def test_timed_order_real_udp_and_snapshot(self):
        rule = self.bridge.upsert(self.raw)
        start = time.monotonic()
        self.assertEqual(self.bridge.test(rule['id']), 'running')
        self.assertLess(time.monotonic() - start, .1)
        self.assertTrue(self.listener.recv(1024).startswith(b'/first'))
        first = time.monotonic()
        self.assertTrue(self.listener.recv(1024).startswith(b'/second'))
        self.assertGreaterEqual(time.monotonic() - first, .10)
        run = self.bridge.sequence_runs[0]
        self.assertEqual(self.wait_finished(run), 'completed')
        state = self.bridge.snapshot(0)['sequences'][0]
        self.assertEqual([s['at_ms'] for s in state['steps']], [0,150])
        self.assertEqual([s['status'] for s in state['steps']], ['sent','sent'])
        self.assertEqual(self.bridge.sent, 2)
        self.assertNotIn('stop', state)
        self.assertFalse(any(':' in key for key in self.bridge.last_sent))

    def test_first_delay_does_not_block_input_and_retrigger_is_ignored(self):
        rule = self.bridge.upsert(dict(self.raw, steps=[dict(self.step, delay_ms=200)]))
        self.bridge.receive(packet(200), 'test')
        started = time.monotonic()
        self.bridge.receive(packet(0), 'test')
        self.bridge.receive(packet(200), 'test')
        self.assertLess(time.monotonic()-started, .05)
        self.assertEqual(len(self.bridge.sequence_runs), 1)
        self.assertEqual(self.bridge.events[0]['status'], 'skipped')
        self.assertTrue(self.listener.recv(1024).startswith(b'/first'))
        self.assertEqual(self.wait_finished(self.bridge.sequence_runs[0]), 'completed')

    def test_pause_cancels_pending_steps_permanently(self):
        rule = self.bridge.upsert(self.raw)
        self.bridge.test(rule['id'])
        self.listener.recv(1024)
        self.bridge.set_runtime({'armed':False})
        self.bridge.set_runtime({'armed':True})
        self.assertEqual(self.bridge.sequence_runs[0]['status'], 'canceled')
        self.listener.settimeout(.25)
        with self.assertRaises(socket.timeout):
            self.listener.recv(1024)
        self.assertEqual(self.bridge.sequence_runs[0]['steps'][1]['status'],'skipped')

    def test_show_change_cancels_old_run(self):
        rule = self.bridge.upsert(self.raw)
        self.bridge.test(rule['id'])
        self.listener.recv(1024)
        old = self.bridge.sequence_runs[0]
        self.store.change(dict(operation='new', name='Other.json'))
        self.bridge.set_runtime({'armed':True})
        self.assertEqual(old['status'], 'canceled')
        self.assertEqual(self.bridge.snapshot(0)['sequences'], [])
        self.listener.settimeout(.25)
        with self.assertRaises(socket.timeout):
            self.listener.recv(1024)

    def test_stop_or_continue_on_error(self):
        bad = dict(name='Missing script',action='script',delay_ms=0,script_path='/missing/script',script_args=[])
        for behavior,expected in [('stop',['error','skipped']),('continue',['error','sent'])]:
            with self.subTest(behavior=behavior):
                rule = self.bridge.upsert(dict(self.raw, sequence_on_error=behavior,steps=[bad,self.step]))
                self.bridge.test(rule['id'])
                run = self.bridge.sequence_runs[0]
                self.assertEqual(self.wait_finished(run),'error')
                self.assertEqual([step['status'] for step in run['steps']], expected)
                if behavior=='continue':self.listener.recv(1024)

    def test_script_completion_is_tracked_and_does_not_delay_timeline(self):
        script = dict(name='Background work',action='script',delay_ms=0,script_path=sys.executable,
                      script_args=['-c','import time; time.sleep(.3)'])
        self.bridge.set_runtime({'scripts_enabled':True})
        rule = self.bridge.upsert(dict(self.raw,steps=[script,dict(self.step,delay_ms=30)]))
        self.bridge.test(rule['id'])
        self.listener.recv(1024)
        run = self.bridge.sequence_runs[0]
        self.assertEqual(run['status'],'running')
        self.assertEqual(run['steps'][0]['status'],'running')
        self.assertEqual(self.wait_finished(run),'completed')
        self.assertEqual(self.bridge.sent,2)

    def test_script_failure_cancels_later_steps(self):
        script = dict(name='Failing script',action='script',delay_ms=0,script_path=sys.executable,
                      script_args=['-c','raise SystemExit(3)'])
        self.bridge.set_runtime({'scripts_enabled':True})
        rule = self.bridge.upsert(dict(self.raw,steps=[script,dict(self.step,delay_ms=500)]))
        self.bridge.test(rule['id'])
        run = self.bridge.sequence_runs[0]
        self.assertEqual(self.wait_finished(run),'error')
        self.assertIn('code 3',run['steps'][0]['detail'])
        self.assertEqual(run['steps'][1]['status'],'skipped')

    def test_sequence_presets_update_and_survive_show_roundtrip(self):
        preset = self.bridge.upsert_preset(self.raw)
        rule = self.bridge.upsert(dict(name='Linked',preset_id=preset['id']))
        changed = copy.deepcopy(preset)
        changed['steps'][1]['delay_ms'] = 250
        self.bridge.upsert_preset(changed,preset['id'])
        state = self.bridge.snapshot(0)
        self.assertEqual(state['rules'][0]['steps'][1]['delay_ms'],250)
        self.assertEqual(state['rules'][0]['id'],rule['id'])
        document = json.loads(json.dumps(self.bridge.document()))
        self.store.change(dict(operation='upload',name='Imported.json',show=document))
        self.assertEqual(self.bridge.document(), document)

    def test_running_sequence_uses_immutable_snapshot(self):
        rule = self.bridge.upsert(self.raw)
        self.bridge.test(rule['id'])
        self.listener.recv(1024)
        changed=copy.deepcopy(rule)
        changed['steps'][1]['address']='/changed'
        self.bridge.upsert(changed,rule['id'])
        self.assertTrue(self.listener.recv(1024).startswith(b'/second'))
        self.assertEqual(self.wait_finished(self.bridge.sequence_runs[0]),'completed')

    def test_limits_nested_sequences_and_delay_validation(self):
        for steps in [[],[self.step]*65,[dict(self.step,delay_ms=-1)],
                      [dict(self.step,delay_ms=True)],[dict(self.step,delay_ms=1.5)],
                      [dict(self.step,delay_ms=600000),dict(self.step,delay_ms=1)],
                      [dict(self.step,action='sequence',steps=[self.step])],
                      [dict(self.step,preset_id='12345678-1234-1234-1234-123456789abc')]]:
            with self.subTest(steps=steps[:1]),self.assertRaises(ValueError):
                validate_rule(dict(self.raw,steps=steps))
        for _ in range(8):
            rule=self.bridge.upsert(dict(self.raw,steps=[dict(self.step,delay_ms=600000)]))
            self.assertEqual(self.bridge.test(rule['id']),'running')
        ninth=self.bridge.upsert(dict(self.raw,steps=[dict(self.step,delay_ms=600000)]))
        self.assertEqual(self.bridge.test(ninth['id']),'error')
        self.assertEqual(len(self.bridge.sequence_runs),8)

    def test_disable_and_delete_cancel_pending_steps(self):
        for operation in ('disable', 'delete'):
            rule = self.bridge.upsert(dict(self.raw, steps=[dict(self.step, delay_ms=600000)]))
            self.bridge.test(rule['id'])
            run = self.bridge.sequence_runs[0]
            if operation == 'disable':
                self.bridge.upsert(dict(rule, enabled=False), rule['id'])
            else:
                self.bridge.delete(rule['id'])
            self.assertEqual(run['status'], 'canceled')
            self.assertEqual(run['steps'][0]['status'], 'skipped')

    def test_canceled_script_is_not_launched_after_reenable(self):
        self.bridge.set_runtime({'scripts_enabled': True})
        step = validate_rule(dict(action='script', name='Never run', script_path=sys.executable))
        done = []
        self.bridge.script_slots.acquire()
        with patch('signal_bridge.actions.subprocess.Popen') as process:
            self.bridge.run_script(step, 'Canceled sequence', on_complete=lambda status, detail: done.append(status), permit=lambda: False)
        process.assert_not_called()
        self.assertEqual(done, ['skipped'])

    def test_run_history_is_bounded(self):
        rule = self.bridge.upsert(dict(self.raw, steps=[self.step]))
        for _ in range(36):
            self.bridge.test(rule['id'])
            self.assertEqual(self.wait_finished(self.bridge.sequence_runs[0]), 'completed')
        self.assertEqual(len(self.bridge.sequence_runs), 32)
        self.assertEqual(len(self.bridge.snapshot(0)['sequences']), 32)

    def test_http_cancel_and_frontend_asset(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bridge))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            rule=self.bridge.upsert(dict(self.raw,steps=[dict(self.step,delay_ms=600000)]))
            self.bridge.test(rule['id'])
            run=self.bridge.sequence_runs[0]
            with urlopen(Request(base+'/api/sequences/'+run['id'],method='DELETE')) as response:
                self.assertTrue(json.load(response)['ok'])
            self.assertEqual(run['status'],'canceled')
            with urlopen(base+'/sequences.js') as response:
                self.assertEqual(response.status,200)
                self.assertIn(b'Preview complete',response.read())
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
