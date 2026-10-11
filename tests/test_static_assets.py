"""Ensure the launcher's page can load every split frontend asset over HTTP."""
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from app import Bridge, make_handler


class StaticAssetsTests(unittest.TestCase):
    def test_page_assets_are_served_and_arbitrary_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(Path(directory) / 'test.json')
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(bridge))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base, timeout=2) as response:
                    page = response.read().decode()
                assets = re.findall(r'(?:src|href)="(/[^"#]+\.(?:js|css))"', page)
                self.assertGreater(len(assets), 5)
                for asset in assets:
                    with self.subTest(asset=asset), urlopen(base + asset, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('javascript' if asset.endswith('.js') else 'text/css', response.headers['Content-Type'])
                        self.assertTrue(response.read())
                for path in ['/../app.py', '/signal_bridge/bridge.py', '/shows/default.json']:
                    with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                        urlopen(base + path, timeout=2)
                    self.assertEqual(error.exception.code, 404)
                    error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                bridge.close()
