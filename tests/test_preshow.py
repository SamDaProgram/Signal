import json
import socket
import socketserver
import struct
import threading
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from preshow_server import State, handlers, trigger_preshow
from app import Bridge, make_handler


def string(value):
    data = value.encode() + b'\0'
    return data + b'\0' * (-len(data) % 4)


def message(tag, payload=b'', address='/preshow'):
    return string(address) + string(',' + tag) + payload


class PreshowTests(unittest.TestCase):
    def test_values_and_invalid_packets(self):
        state = State()
        for tag, yes, no in [('T', b'', None), ('i', struct.pack('>i', 1), struct.pack('>i', 0)),
                             ('f', struct.pack('>f', 1), struct.pack('>f', 0)),
                             ('s', string('true'), string('false'))]:
            state.update(message(tag, yes))
            self.assertTrue(json.loads(state.json())['preshow'])
            state.update(message('F' if no is None else tag, no or b''))
            self.assertFalse(json.loads(state.json())['preshow'])
        for packet in [b'', b'garbage', message('i'), message('i', struct.pack('>i', 2)),
                       message('s', string('yes')), message('T', b'junk'),
                       message('T', address='/other'), message('TT')]:
            state.update(packet)
            self.assertFalse(json.loads(state.json())['preshow'])

    def test_bundles(self):
        state = State()
        packet = message('T')
        bundle = b'#bundle\0' + struct.pack('>Q', 1) + struct.pack('>i', len(packet)) + packet
        state.update(bundle + b'bad!')
        self.assertFalse(json.loads(state.json())['preshow'])
        state.update(bundle)
        self.assertTrue(json.loads(state.json())['preshow'])

    def test_controller_runs_saved_qlab_midi_once_and_respects_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            from app import ShowFiles
            bridge = ShowFiles(Path(folder) / 'shows').bridge
            bridge.upsert(dict(name='START PRESHOW', action='midi', midi_port='QLab',
                               midi_type='note_on', midi_channel=1, midi_number=2, midi_value=127))
            http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(bridge))
            thread = threading.Thread(target=http.serve_forever)
            thread.start()
            state = State(lambda: trigger_preshow(f'http://127.0.0.1:{http.server_port}', 'START PRESHOW'))
            midi = Mock()
            try:
                with patch('signal_bridge.actions.midi_module', return_value=midi):
                    self.assertEqual(json.loads(state.start()), {'preshow': True})
                    state.start()
                    midi.open_output.assert_called_once_with('QLab')
                    midi.Message.assert_called_once_with('note_on', channel=0, note=2, velocity=127)
                    midi.open_output.return_value.send.assert_called_once()
                    state.update(message('F'))
                    bridge.set_runtime({'armed':False})
                    with self.assertRaisesRegex(ValueError, 'paused'):
                        state.start()
                    self.assertFalse(json.loads(state.json())['preshow'])
                    midi.open_output.return_value.send.assert_called_once()
                    bridge.set_runtime({'armed':True})
                    midi.open_output.return_value.send.side_effect=RuntimeError('Disconnected')
                    with self.assertRaisesRegex(ValueError, 'not sent'):
                        state.start()
                    self.assertFalse(json.loads(state.json())['preshow'])
            finally:
                http.shutdown()
                thread.join()
                http.server_close()
                bridge.close()

    def test_controller_starts_combined_timed_sequence(self):
        from app import ShowFiles, encode_osc
        with tempfile.TemporaryDirectory() as folder:
            bridge = ShowFiles(Path(folder) / 'shows').bridge
            rule = bridge.upsert(dict(name='Start Preshow', action='sequence', steps=[
                dict(name='Set Preshow',action='osc',host='127.0.0.1',port=9000,address='/preshow',args=[{'type':'i','value':1}],delay_ms=0),
                dict(name='FPP Preshow',action='osc',host='192.168.3.30',port=9000,address='/preshow/reg',args=[{'type':'i','value':0}],delay_ms=30),
                dict(name='QLab Start',action='midi',midi_port='QLab',midi_type='note_on',midi_channel=1,midi_number=2,midi_value=127,delay_ms=0)]))
            http = ThreadingHTTPServer(('127.0.0.1',0),make_handler(bridge))
            thread = threading.Thread(target=http.serve_forever)
            thread.start()
            try:
                with patch.object(bridge,'output') as output, patch('signal_bridge.actions.midi_module') as midi:
                    state = State(lambda: trigger_preshow(f'http://127.0.0.1:{http.server_port}', 'START PRESHOW'))
                    self.assertEqual(json.loads(state.start()),{'preshow':True})
                    deadline=time.monotonic()+2
                    while time.monotonic()<deadline:
                        with bridge.lock:
                            if bridge.sequence_runs and bridge.sequence_runs[0]['status']!='running':break
                        time.sleep(.01)
                    self.assertEqual(bridge.sequence_runs[0]['status'],'completed')
                    self.assertEqual(output.sendto.call_count,2)
                    self.assertEqual(output.sendto.call_args_list[0].args,(encode_osc('/preshow',[{'type':'i','value':1}]),('127.0.0.1',9000)))
                    self.assertEqual(output.sendto.call_args_list[1].args,(encode_osc('/preshow/reg',[{'type':'i','value':0}]),('192.168.3.30',9000)))
                    midi.return_value.Message.assert_called_once_with('note_on',channel=0,note=2,velocity=127)
                    midi.return_value.open_output.return_value.send.assert_called_once()
            finally:
                http.shutdown();thread.join();http.server_close();bridge.close()

    def test_start_endpoint_failure_and_status_only_updates(self):
        action = Mock(side_effect=ValueError('QLab unavailable'))
        state = State(action)
        handler, _ = handlers(state)
        with ThreadingHTTPServer(('127.0.0.1', 0), handler) as http:
            thread = threading.Thread(target=http.serve_forever)
            thread.start()
            url = f'http://127.0.0.1:{http.server_port}'
            def post(path, data):
                return urllib.request.urlopen(urllib.request.Request(url+path, data=json.dumps(data).encode(),
                    headers={'Content-Type':'application/json'}), timeout=2)
            try:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/start-preshow', {})
                self.assertEqual(error.exception.code, 502)
                error.exception.close()
                self.assertFalse(json.loads(state.json())['preshow'])
                action.side_effect=None
                with post('/api/start-preshow', {}) as response:
                    self.assertEqual(json.load(response), {'preshow':True})
                with post('/api/start-preshow', {}) as response: response.read()
                self.assertEqual(action.call_count,2)
                with post('/api/status', {'preshow':False}) as response: response.read()
                with post('/api/status', {'preshow':True}) as response: response.read()
                self.assertEqual(action.call_count,2)
            finally:
                http.shutdown()
                thread.join()

    def test_home_page_and_status_controls(self):
        state = State()
        handler, _ = handlers(state)
        with ThreadingHTTPServer(('127.0.0.1', 0), handler) as http:
            thread = threading.Thread(target=http.serve_forever)
            thread.start()
            url = f'http://127.0.0.1:{http.server_port}'
            def post(data, **headers):
                return urllib.request.urlopen(urllib.request.Request(url + '/api/status',
                    data=json.dumps(data).encode(), headers={'Content-Type':'application/json', **headers}), timeout=2)
            try:
                with urllib.request.urlopen(urllib.request.Request(url+'/', headers={'Accept':'text/html'})) as response:
                    page = response.read().decode()
                    self.assertIn('text/html', response.headers['Content-Type'])
                    self.assertIn('Start preshow', page)
                    self.assertIn('CURRENT STATUS', page)
                    self.assertIn('id="show-action"', page)
                    self.assertEqual(page.count('<button '), 1)
                for value in (True, False):
                    with post({'preshow':value}, Origin=url) as response:
                        self.assertEqual(json.load(response), {'preshow':value})
                    with urllib.request.urlopen(url+'/api/status') as response:
                        self.assertEqual(json.load(response), {'preshow':value})
                    self.assertEqual(json.loads(state.json()), {'preshow':value})
                state.update(message('T'))
                with urllib.request.urlopen(url+'/api/status') as response:
                    self.assertEqual(json.load(response), {'preshow':True})
                for data in [{'preshow':'false'}, {'preshow':0}, {}, {'preshow':False,'extra':1}]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post(data)
                    self.assertEqual(error.exception.code,400)
                    error.exception.close()
                    self.assertTrue(json.loads(state.json())['preshow'])
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post({'preshow':False}, Origin='http://other.example')
                self.assertEqual(error.exception.code,403)
                error.exception.close()
                self.assertTrue(json.loads(state.json())['preshow'])
                for path in ['/preshow.js','/preshow.css']:
                    with urllib.request.urlopen(url+path) as response:
                        self.assertEqual(response.status,200)
            finally:
                http.shutdown()
                thread.join()

    def test_real_udp_to_http(self):
        http_handler, osc_handler = handlers(State())
        with ThreadingHTTPServer(('127.0.0.1', 0), http_handler) as http, \
                socketserver.UDPServer(('127.0.0.1', 0), osc_handler) as osc, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            threads = [threading.Thread(target=server.serve_forever) for server in (http, osc)]
            for thread in threads:
                thread.start()
            url = f'http://127.0.0.1:{http.server_port}/'
            try:
                for value in (False, True, False):
                    sender.sendto(message('T' if value else 'F'), osc.server_address)
                    deadline = time.monotonic() + 2
                    while True:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            self.assertEqual(response.headers['Cache-Control'], 'no-store')
                            actual = json.load(response)
                        if actual == {'preshow': value} or time.monotonic() >= deadline:
                            break
                        time.sleep(.01)
                    self.assertEqual(actual, {'preshow': value})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + 'missing', timeout=2)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            finally:
                for server in (http, osc):
                    server.shutdown()
                for thread in threads:
                    thread.join()


if __name__ == '__main__':
    unittest.main()
