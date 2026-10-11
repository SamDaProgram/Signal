import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app import Bridge, ShowFiles, make_handler, validate_rule
from test_bridge import packet


class ShowActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ShowFiles(Path(self.temp.name) / 'shows')
        self.bridge = self.store.bridge
        self.raw = dict(name='Cue', host='127.0.0.1', port=9999, address='/go')

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def wait_scripts(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.bridge.events and self.bridge.events[0]['status'] in ('sent', 'error'):
                return
            time.sleep(.01)
        self.fail('Script did not complete')

    def test_missing_directory_and_active_file_created(self):
        self.assertTrue(self.bridge.path.is_file())
        self.assertEqual(json.loads(self.bridge.path.read_text())['rules'], [])
        self.store.change(dict(operation='new', name='Evening.json'))
        self.bridge.path.unlink()
        restored = ShowFiles(self.store.directory)
        try:
            self.assertEqual(restored.bridge.path.name, 'Evening.json')
            self.assertEqual(restored.bridge.rules, [])
            self.assertTrue(restored.bridge.path.exists())
        finally:
            restored.bridge.close()

    def test_roundtrip_all_configs_and_paused_load(self):
        preset = self.bridge.upsert_preset(dict(self.raw, name='Console'))
        self.bridge.upsert(dict(self.raw, preset_id=preset['id'], cooldown_ms=800))
        self.bridge.set_input(dict(protocol='e131', monitor_universe=7))
        self.bridge.set_group_label(dict(universe=0, channel=1, label='Stage'))
        expected = self.bridge.document()
        self.store.change(dict(operation='save_as', name='Copy.json'))
        self.assertEqual(self.bridge.document(), expected)
        self.store.change(dict(operation='new', name='Blank.json'))
        self.assertEqual(self.bridge.rules, [])
        self.assertFalse(self.bridge.armed)
        self.bridge.set_runtime(dict(armed=True, scripts_enabled=True))
        self.store.change(dict(operation='load', name='Copy.json'))
        self.assertEqual(self.bridge.document(), expected)
        self.assertFalse(self.bridge.armed)
        self.assertFalse(self.bridge.scripts_enabled)
        self.assertEqual(self.bridge.universes, {})
        restored = ShowFiles(self.store.directory)
        try:
            self.assertEqual(restored.bridge.path.name, 'Copy.json')
            self.assertEqual(restored.bridge.document(), expected)
        finally:
            restored.bridge.close()

    def test_legacy_migration_preserves_original(self):
        legacy = Path(self.temp.name) / 'legacy.json'
        legacy.write_text('{"rules": []}')
        store = ShowFiles(Path(self.temp.name) / 'other', legacy)
        try:
            self.assertEqual(store.bridge.rules, [])
            self.assertEqual(legacy.read_text(), '{"rules": []}')
        finally:
            store.bridge.close()

    def test_invalid_import_is_transactional(self):
        original = self.bridge.document()
        for bad in [None, [], {}, {'rules':[None]}, {'rules':[], 'version':2},
                    {'rules':[], 'presets':'bad'}, {'rules':[], 'group_labels':{'bad':'label'}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.change(dict(operation='upload', name='Invalid.json', show=bad))
            self.assertEqual(self.bridge.document(), original)
            self.assertFalse((self.store.directory / 'Invalid.json').exists())
        rule = self.bridge.upsert(self.raw)
        for bad in [dict(rules=[rule, rule]), dict(rules=[dict(rule, preset_id='12345678-1234-1234-1234-123456789abc')])]:
            with self.assertRaises(ValueError):
                self.store.change(dict(operation='upload', name='Invalid.json', show=bad))

    def test_filename_traversal_symlink_and_overwrite_rejected(self):
        for name in ['../outside.json', '/tmp/outside.json', 'bad.txt', '.hidden.json', 'a/b.json', 'a\\b.json']:
            with self.assertRaises(ValueError):
                self.store.change(dict(operation='new', name=name))
        for operation in ('new', 'save_as', 'upload'):
            with self.assertRaises(ValueError):
                self.store.change(dict(operation=operation, name='default.json', show={'rules':[]}))
        outside = Path(self.temp.name) / 'outside.json'
        outside.write_text('{"rules":[]}')
        (self.store.directory / 'linked.json').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.change(dict(operation='load', name='linked.json'))
        self.assertNotIn('linked.json', self.store.info()['files'])

    def test_save_failure_keeps_active_show(self):
        before = self.bridge.path
        with patch.object(self.store, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.change(dict(operation='new', name='Other.json'))
        self.assertEqual(self.bridge.path, before)
        self.assertEqual(self.store.marker.read_text(), 'default.json')

    def test_linked_fields_enforced_updated_and_detached(self):
        preset = self.bridge.upsert_preset(dict(self.raw, name='Device', address='/linked'))
        rule = self.bridge.upsert(dict(self.raw, preset_id=preset['id'], host='10.0.0.99'))
        self.assertEqual(rule['host'], preset['host'])
        self.assertEqual(rule['address'], '/linked')
        changed = self.bridge.upsert_preset(dict(preset, port=9001, address='/next'), preset['id'])
        self.assertEqual(self.bridge.rules[0]['port'], changed['port'])
        self.assertEqual(self.bridge.rules[0]['address'], '/next')
        self.assertEqual(self.bridge.snapshot(0)['rules'][0]['id'], rule['id'])
        restored = Bridge(self.bridge.path)
        try:
            self.assertEqual(restored.rules[0]['id'], rule['id'])
        finally:
            restored.close()
        self.assertEqual(json.loads(self.bridge.path.read_text())['rules'][0]['port'], 9001)
        with patch.object(self.bridge, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.bridge.upsert_preset(dict(preset, port=9002), preset['id'])
        self.assertEqual(self.bridge.rules[0]['port'], 9001)
        self.bridge.delete_preset(preset['id'])
        self.assertNotIn('preset_id', self.bridge.rules[0])
        self.assertEqual(self.bridge.rules[0]['port'], 9001)

    def test_blank_address_preserves_custom_address_and_action_changes(self):
        preset = self.bridge.upsert_preset(dict(self.raw, address=''))
        self.bridge.upsert(dict(self.raw, preset_id=preset['id'], address='/custom'))
        self.assertEqual(self.bridge.rules[0]['address'], '/custom')
        self.bridge.upsert_preset(dict(name='MIDI', action='midi', midi_port='Test device'), preset['id'])
        self.assertEqual(self.bridge.rules[0]['action'], 'midi')
        self.assertEqual(self.bridge.rules[0]['midi_port'], 'Test device')

    def test_pausing_and_cooldown_skip_without_replay(self):
        rule = self.bridge.upsert(dict(self.raw, cooldown_ms=10000))
        with patch.object(self.bridge, 'output') as out:
            self.bridge.set_runtime(dict(armed=False))
            self.bridge.receive(packet(200), 'test')
            self.bridge.set_runtime(dict(armed=True))
            self.bridge.receive(packet(200), 'test')
            out.sendto.assert_not_called()
            self.bridge.receive(packet(0), 'test')
            self.bridge.receive(packet(200), 'test')
            self.bridge.receive(packet(0), 'test')
            self.bridge.receive(packet(200), 'test')
            self.assertEqual(out.sendto.call_count, 1)
            self.assertEqual(self.bridge.events[0]['status'], 'skipped')
            self.assertEqual(self.bridge.test(rule['id']), 'sent')
            self.assertEqual(out.sendto.call_count, 2)

    def test_midi_message_mapping_and_recovery(self):
        module = Mock()
        raw = dict(self.raw, action='midi', midi_port='Test port', midi_channel=16, midi_number=64, midi_value=90)
        with patch('signal_bridge.actions.midi_module', return_value=module):
            for kind, args in [('note_on',dict(channel=15,note=64,velocity=90)),
                               ('note_off',dict(channel=15,note=64,velocity=90)),
                               ('control_change',dict(channel=15,control=64,value=90)),
                               ('program_change',dict(channel=15,program=64)),
                               ('start',{}),('stop',{}),('continue',{})]:
                rule = self.bridge.upsert(dict(raw, midi_type=kind))
                self.assertEqual(self.bridge.test(rule['id']), 'sent')
                module.Message.assert_called_with(kind, **args)
            module.open_output.assert_called_once_with('Test port')
            module.open_output.return_value.send.side_effect = OSError('disconnected')
            self.assertEqual(self.bridge.test(rule['id']), 'error')
            self.assertEqual(self.bridge.midi_outputs, {})
            module.open_output.return_value.send.side_effect = None
            self.assertEqual(self.bridge.test(rule['id']), 'sent')
            self.assertEqual(module.open_output.call_count, 2)

    def test_script_completion_literal_args_and_nonblocking(self):
        output = Path(self.temp.name) / 'result.txt'
        code = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])'
        literal = '$(echo should-not-run); spaces & text'
        rule = self.bridge.upsert(dict(self.raw, action='script', script_path=sys.executable,
                                      script_args=['-c', code, str(output), literal]))
        self.assertEqual(self.bridge.test(rule['id']), 'error')
        self.assertFalse(output.exists())
        self.bridge.set_runtime(dict(scripts_enabled=True))
        self.assertEqual(self.bridge.test(rule['id']), 'running')
        self.wait_scripts()
        self.assertEqual(output.read_text(), literal)
        self.assertEqual(self.bridge.sent, 1)
        self.assertEqual(self.bridge.events[0]['status'], 'sent')

    def test_script_timeout_failure_and_concurrency_limit(self):
        self.bridge.set_runtime(dict(scripts_enabled=True))
        rule = self.bridge.upsert(dict(self.raw, action='script', script_path=sys.executable,
                                      script_args=['-c', 'import time; time.sleep(10)'], script_timeout=1))
        self.bridge.test(rule['id'])
        self.wait_scripts()
        self.assertEqual(self.bridge.events[0]['status'], 'error')
        self.assertIn('timed out', self.bridge.events[0]['detail'])
        self.assertEqual(self.bridge.sent, 0)
        self.assertFalse(self.bridge.script_processes)
        for _ in range(4):
            self.assertTrue(self.bridge.script_slots.acquire(blocking=False))
        try:
            self.assertEqual(self.bridge.test(rule['id']), 'error')
            self.assertIn('Four scripts', self.bridge.events[0]['detail'])
        finally:
            for _ in range(4):
                self.bridge.script_slots.release()
        failed = self.bridge.upsert(dict(rule, script_args=['-c','raise SystemExit(7)']), rule['id'])
        self.assertEqual(self.bridge.test(failed['id']), 'running')
        self.wait_scripts()
        self.assertIn('code 7', self.bridge.events[0]['detail'])

    def test_inactive_osc_fields_do_not_break_midi_or_scripts(self):
        rule = self.bridge.upsert(dict(self.raw, action='midi', midi_port='Test', host='', port=0, args=None))
        self.assertEqual(rule['args'], [])
        self.assertEqual(self.bridge.snapshot(0)['rules'][0]['action'], 'midi')
        self.assertEqual(Bridge.validate_show(self.bridge.document())['rules'][0], rule)

    def test_action_validation(self):
        for change in [dict(action='bad'), dict(action=[]), dict(action='midi', midi_port=''),
                       dict(action='midi', midi_port='port', midi_channel=17),
                       dict(action='midi', midi_port='port', midi_number=128),
                       dict(action='script', script_path='relative'),
                       dict(action='script', script_path='/bin/echo', script_args='go'),
                       dict(action='script', script_path='/bin/echo', script_timeout=0),
                       dict(cooldown_ms=-1)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rule(dict(self.raw, **change))

    def test_show_http_roundtrip_and_stale_editor(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), make_handler(self.bridge))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def post(body, header=None):
            return urlopen(Request(base+'/api/shows', data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json', **(header or {})}), timeout=2)
        try:
            self.bridge.upsert(self.raw)
            with urlopen(base+'/api/shows/download') as response:
                exported = json.loads(response.read())
                self.assertIn('attachment', response.headers['Content-Disposition'])
            with post(dict(operation='upload', name='Imported.json', show=exported)) as response:
                self.assertEqual(json.loads(response.read())['current'], 'Imported.json')
            self.assertEqual(self.bridge.document(), exported)
            with self.assertRaises(HTTPError) as error:
                post(dict(operation='save'), {'X-Signal-Show':'default.json'})
            self.assertEqual(error.exception.code,409)
            error.exception.close()
            with self.assertRaises(HTTPError) as error:
                post(dict(operation='upload', name='Broken.json', show={'rules':[None]}))
            self.assertEqual(error.exception.code,400)
            error.exception.close()
            with urlopen(base+'/api/state') as response:
                state = json.loads(response.read())
                self.assertIn('127.0.0.1', state['receive_addresses'])
                self.assertFalse(state['armed'])
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
