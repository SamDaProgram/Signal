import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from app import Bridge, ROOT, encode_osc, preshow_template, validate_rule
from test_bridge import packet


class ScriptFieldsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.temp.name) / 'settings.json')
        self.bridge.set_runtime({'scripts_enabled': True})
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(.15)
        self.osc = self.bridge.upsert_preset(dict(name='FPP', host='127.0.0.1', port=self.sock.getsockname()[1], address='/original'))
        self.midi = self.bridge.upsert_preset(dict(name='QLab', action='midi', midi_port='QLab', midi_type='note_on', midi_number=60))
        self.rule = dict(preshow_template(), name='Preshow gate', id='gate')
        next(f for f in self.rule['script_fields'] if f['key']=='fpp')['value'] = self.osc['id']
        # Stale QLab fields from older shows must never produce MIDI from this gate.
        self.rule['script_fields'].append(dict(key='qlab',label='Legacy QLab',type='preset',value=self.midi['id']))

    def tearDown(self):
        self.bridge.close()
        self.sock.close()
        self.temp.cleanup()

    def result(self, value):
        return json.dumps({'preset_actions': value}).encode()

    def test_fields_validation_and_preset_roundtrip(self):
        preset = self.bridge.upsert_preset(self.rule)
        rule = self.bridge.upsert(dict(name='Linked', preset_id=preset['id']))
        self.assertEqual(rule['script_fields'], self.rule['script_fields'])
        restored = Bridge(self.bridge.path)
        try:
            self.assertEqual(restored.rules[0]['script_fields'], rule['script_fields'])
        finally:
            restored.close()
        for fields in [[{'key':'bad key'}], [{'key':'same'}, {'key':'same'}], [{'key':'number', 'type':'number','value':True}], [{'key':'flag','type':'boolean','value':'false'}]]:
            with self.assertRaises(ValueError):
                validate_rule(dict(self.rule, script_fields=fields))

    def test_validates_all_outputs_before_sending(self):
        with self.assertRaises(ValueError):
            self.bridge.script_actions(self.rule, self.result([{'preset_id':self.osc['id']}, {'preset_id':'missing'}]), self.bridge.presets, 'test')
        with self.assertRaises(socket.timeout):
            self.sock.recv(1024)

    def test_invalid_osc_argument_prevents_entire_dispatch(self):
        with self.assertRaises((ValueError, TypeError)):
            self.bridge.script_actions(self.rule, self.result([
                {'preset_id':self.osc['id'], 'address':'/preshow/fade'},
                {'preset_id':self.osc['id'], 'address':'/preshow/reg', 'args':[{'type':'i','value':'bad'}]}
            ]), self.bridge.presets, 'test')
        with self.assertRaises(socket.timeout): self.sock.recv(1024)

    def test_pause_and_cancel_prevent_outputs(self):
        output=self.result([{'preset_id':self.osc['id']}])
        self.assertEqual(self.bridge.script_actions(self.rule,output,self.bridge.presets,'test',lambda:False)[0], 'skipped')
        self.bridge.armed=False
        self.assertEqual(self.bridge.script_actions(self.rule,output,self.bridge.presets,'test')[0], 'skipped')
        with self.assertRaises(socket.timeout): self.sock.recv(1024)

    def run_gate(self, body):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(body)
            def log_message(self, *args): pass
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        fields=self.rule['script_fields']
        fields[0]['value']=f'http://127.0.0.1:{http.server_port}/'
        try:
            env=dict(os.environ,SIGNAL_SCRIPT_FIELDS=json.dumps({f['key']:f['value'] for f in fields}),SIGNAL_SCRIPT_PRESETS=json.dumps([self.osc['id'],self.midi['id']]))
            return subprocess.run([sys.executable,str(ROOT/'scripts/preshow_gate.py')],env=env,capture_output=True,timeout=5)
        finally:
            http.shutdown();http.server_close();thread.join()

    def test_true_false_and_invalid_status(self):
        result=self.run_gate(b'{"preshow": true}')
        self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout),{'preset_actions':[]})
        result=self.run_gate(b'{"preshow": false}')
        self.assertEqual(result.returncode,0)
        midi=Mock()
        with patch('signal_bridge.actions.midi_module',return_value=midi):
            status,_=self.bridge.script_actions(self.rule,result.stdout,self.bridge.presets,'test')
        self.assertEqual(status,'sent')
        self.assertEqual(self.sock.recv(1024),encode_osc('/stoppre',[]))
        self.assertEqual(self.sock.recv(1024),encode_osc('/preshow/fade',[]))
        with self.assertRaises(socket.timeout): self.sock.recv(1024)
        midi.open_output.assert_not_called()
        for body in [b'{"preshow":"false"}',b'{}',b'false',b'not JSON']:
            result=self.run_gate(body)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(result.stdout,b'')

    def test_dmx_gate_checks_each_transition_and_does_not_repeat_held_level(self):
        status = {'preshow': True}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(status).encode())
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.rule['script_fields'][0]['value'] = f'http://127.0.0.1:{http.server_port}/'
        rule = self.bridge.upsert(self.rule)
        def wait_done():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with self.bridge.lock:
                    if any(e['name'] == rule['name'] and e['status'] in ('sent', 'error') for e in self.bridge.events):
                        return
                time.sleep(.01)
            self.fail('Gate did not finish')
        midi = Mock()
        try:
            with patch('signal_bridge.actions.midi_module', return_value=midi):
                self.bridge.receive(packet(0), '127.0.0.1')
                self.bridge.receive(packet(100), '127.0.0.1')
                wait_done()
                with self.assertRaises(socket.timeout): self.sock.recv(1024)
                midi.open_output.assert_not_called()
                status['preshow'] = False
                self.bridge.clear_log()
                self.bridge.receive(packet(0), '127.0.0.1')
                self.bridge.receive(packet(100), '127.0.0.1')
                wait_done()
                self.assertEqual(self.sock.recv(1024), encode_osc('/stoppre', []))
                self.assertEqual(self.sock.recv(1024), encode_osc('/preshow/fade', []))
                with self.assertRaises(socket.timeout): self.sock.recv(1024)
                midi.open_output.assert_not_called()
                self.bridge.receive(packet(100), '127.0.0.1')
                with self.assertRaises(socket.timeout): self.sock.recv(1024)
        finally:
            http.shutdown()
            http.server_close()
            thread.join()

    def test_script_worker_passes_fields_and_dispatches(self):
        code='import os,json; f=json.loads(os.environ["SIGNAL_SCRIPT_FIELDS"]); print(json.dumps({"preset_actions":[{"preset_id":f["fpp"],"address":"/start"}]}))'
        rule=self.bridge.upsert(dict(self.rule,script_args=['-c',code]))
        self.bridge.test(rule['id'])
        self.sock.settimeout(3)
        self.assertEqual(self.sock.recv(1024),encode_osc('/start',[]))
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and self.bridge.events[0]['status']=='running':time.sleep(.01)
        self.assertFalse(any(e['status']=='error' for e in self.bridge.events))


if __name__ == '__main__': unittest.main()
