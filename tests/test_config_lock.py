import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app import Bridge, make_handler
from config_lock import ConfigLock


class ConfigLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge(Path(self.temp.name) / 'show.json')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.bridge))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.bridge.close()
        self.temp.cleanup()

    def request(self, path, method='GET', body=None, token=''):
        req = Request(f'http://127.0.0.1:{self.server.server_port}'+path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type':'application/json','X-Signal-Edit':token})
        with urlopen(req) as response:
            return json.load(response)

    def test_lock_guards_configuration_but_allows_monitoring_and_preshow_operation(self):
        session = self.request('/api/config-lock/setup', 'POST', {'pin':'1234'})
        token = session['token']
        for path, method, body in [('/api/rules','POST',{}),('/api/presets','POST',{}),
                                  ('/api/groups','PUT',{}),('/api/shows','POST',{}),
                                  ('/api/input','PUT',{'protocol':'ddp'}),
                                  ('/api/runtime','PUT',{'scripts_enabled':True}),
                                  ('/api/rules/missing','DELETE',None)]:
            with self.assertRaises(HTTPError) as failure:
                self.request(path,method,body)
            self.assertEqual(failure.exception.code,403)
            failure.exception.close()
        self.request('/api/state')
        self.request('/api/input','PUT',{'protocol':'ddp','monitor_universe':0},token)
        result = self.request('/api/input','PUT',{'monitor_universe':2})
        self.assertEqual(result['protocol'],'ddp')
        self.assertEqual(result['monitor_universe'],2)
        # Mock execution: this verifies preshow compatibility without sending cues.
        with patch.object(self.bridge,'test',return_value='sent') as execute:
            self.assertEqual(self.request('/api/rules/example/test','POST',{})['status'],'sent')
            execute.assert_called_once_with('example')
        self.request('/api/config-lock/lock','POST',{})
        self.assertFalse(self.request('/api/config-lock',token=token)['unlocked'])
        with self.assertRaises(HTTPError) as failure:
            self.request('/api/input','PUT',{'protocol':'artnet'},token)
        self.assertEqual(failure.exception.code,403)
        failure.exception.close()

    def test_persistence_pin_validation_and_session_expiry(self):
        lock=self.bridge.config_lock
        for pin in ['12','abcd','1'*13]:
            with self.assertRaises(ValueError):lock.setup(pin)
        session=lock.setup('5678')
        self.assertNotIn('5678',lock.path.read_text())
        self.assertTrue(lock.authorized(session['token']))
        lock.sessions[session['token']]=0
        self.assertFalse(lock.authorized(session['token']))
        restored=ConfigLock(lock.path)
        self.assertFalse(restored.status()['unlocked'])
        with self.assertRaises(ValueError):restored.setup('1234')
        for _ in range(5):
            with self.assertRaises(ValueError):restored.unlock('0000')
        with self.assertRaisesRegex(ValueError,'Wait 30'):restored.unlock('5678')
        restored.retry_at=0
        self.assertTrue(restored.unlock('5678')['unlocked'])

    def test_navigation_and_lock_assets(self):
        self.assertIn('preshow',self.request('/api/navigation'))
        with urlopen(f'http://127.0.0.1:{self.server.server_port}/config-controls.js') as response:
            self.assertEqual(response.status,200)
