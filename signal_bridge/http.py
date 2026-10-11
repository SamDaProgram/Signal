"""Local HTTP API, access checks and static asset serving."""

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from .actions import midi_module
from .constants import ROOT
from .network import network_addresses
from .shows import ShowFiles
from .validation import preshow_template
from .values import integer


def make_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body, mime='application/json'):
            data = json.dumps(body).encode() if mime == 'application/json' else body
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            if mime == 'application/octet-stream':
                self.send_header('Content-Disposition', f'attachment; filename="{bridge.path.name if bridge.shows else "signal-show.json"}"')
            if mime.startswith('text/csv'):
                self.send_header('Content-Disposition', 'attachment; filename="signal-channels.csv"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            # Loopback UI only; reject hostile Host headers and cross-origin writes.
            host = self.headers.get('Host', '')
            port = self.server.server_port
            valid = {f'localhost:{port}', f'127.0.0.1:{port}'}
            if port == 80:
                valid |= {'localhost', '127.0.0.1'}
            return host in valid and self.headers.get('Origin', f'http://{host}') == f'http://{host}'

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {'error': 'Local access only.'})
            url = urlsplit(self.path)
            if url.path == '/api/config-lock':
                with bridge.lock:
                    return self.respond(200, bridge.config_lock.status(self.headers.get('X-Signal-Edit', '')))
            if url.path == '/api/navigation':
                return self.respond(200, {'preshow': getattr(bridge, 'preshow_url', 'http://127.0.0.1:8081/')})
            if url.path == '/api/shows/download':
                with bridge.lock:
                    data = json.dumps(bridge.document(), indent=2).encode()
                return self.respond(200, data, 'application/octet-stream')
            if url.path == '/api/scripts/preshow-template':
                return self.respond(200, preshow_template())
            if url.path == '/api/midi':
                try:
                    return self.respond(200, {'ports': midi_module().get_output_names()})
                except Exception as exc:
                    return self.respond(200, {'ports': [], 'error': str(exc)})
            if url.path == '/api/shows':
                with bridge.lock:
                    return self.respond(200, bridge.shows.info() if bridge.shows else {})
            if url.path == '/api/export/channels':
                return self.respond(200, bridge.export_channels(), 'text/csv; charset=utf-8')
            if url.path == '/api/state':
                try:
                    universe = integer(int(parse_qs(url.query).get('universe', [str(bridge.input['monitor_universe'])])[0]), 0, 63999, 'Universe / block')
                    return self.respond(200, bridge.snapshot(universe))
                except ValueError as exc:
                    return self.respond(400, {'error': str(exc)})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/config-controls.js': ('config-controls.js', 'text/javascript; charset=utf-8'),
                     '/sequences.js': ('sequences.js', 'text/javascript; charset=utf-8'),
                     '/faders.js': ('faders.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            for name in ('core', 'actions', 'triggers', 'dashboard', 'shows'):
                files[f'/{name}.js'] = (f'{name}.js', 'text/javascript; charset=utf-8')
            if url.path not in files:
                return self.respond(404, {'error': 'Not found.'})
            name, mime = files[url.path]
            self.respond(200, (ROOT / 'static' / name).read_bytes(), mime)

        def read_json(self):
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected application/json.')
            length = integer(int(self.headers.get('Content-Length', '0')), 1, ShowFiles.MAX_BYTES + 65536, 'Request size')
            return json.loads(self.rfile.read(length))

        def mutation_locked(self):
            if not self.allowed():
                return self.respond(403, {'error': 'Local access only.'})
            try:
                parts = urlsplit(self.path).path.strip('/').split('/')
                if parts[:2] == ['api', 'config-lock'] and self.command == 'POST':
                    operation = parts[2] if len(parts) == 3 else ''
                    if operation == 'setup':
                        return self.respond(200, bridge.config_lock.setup(self.read_json().get('pin')))
                    if operation == 'unlock':
                        return self.respond(200, bridge.config_lock.unlock(self.read_json().get('pin')))
                    if operation == 'lock':
                        return self.respond(200, bridge.config_lock.lock())
                    return self.respond(404, {'error': 'Not found.'})
                if not bridge.config_lock.authorized(self.headers.get('X-Signal-Edit', '')):
                    # Live operation remains available; configuration writes require the PIN.
                    operating = (parts == ['api', 'network'] or parts == ['api', 'log'] or
                                 (parts[:2] == ['api', 'sequences'] and self.command == 'DELETE') or
                                 (parts[:2] == ['api', 'rules'] and len(parts) == 4 and parts[3] == 'test' and self.command == 'POST'))
                    if parts in (['api', 'input'], ['api', 'runtime']) and self.command == 'PUT':
                        body = self.read_json()
                        permitted = {'monitor_universe'} if parts[-1] == 'input' else {'armed'}
                        if not isinstance(body, dict) or not set(body) <= permitted:
                            return self.respond(403, {'error': 'Configuration is locked. Unlock with your PIN to edit.'})
                        return self.respond(200, bridge.set_input({**bridge.input, **body}) if parts[-1] == 'input' else bridge.set_runtime(body))
                    if not operating:
                        return self.respond(403, {'error': 'Configuration is locked. Unlock with your PIN to edit.'})
                expected_show = self.headers.get('X-Signal-Show')
                if bridge.shows and expected_show and expected_show != bridge.path.name:
                    return self.respond(409, {'error': 'The active show changed. Refresh and try again.'})
                if parts == ['api', 'network'] and self.command == 'POST':
                    bridge.addresses = network_addresses()
                    return self.respond(200, {'addresses': bridge.addresses})
                if parts[:2] == ['api', 'sequences'] and len(parts) == 3 and self.command == 'DELETE':
                    return self.respond(200, bridge.cancel_sequence(parts[2]))
                if parts == ['api', 'runtime'] and self.command == 'PUT':
                    return self.respond(200, bridge.set_runtime(self.read_json()))
                if parts == ['api', 'shows'] and self.command == 'POST':
                    if not bridge.shows:
                        raise ValueError('Show directory is not configured.')
                    return self.respond(200, bridge.shows.change(self.read_json()))
                if parts == ['api', 'groups'] and self.command == 'PUT':
                    return self.respond(200, bridge.set_group_label(self.read_json()))
                if parts[:2] == ['api', 'presets']:
                    if self.command == 'DELETE' and len(parts) == 3:
                        bridge.delete_preset(parts[2])
                        return self.respond(200, {'ok': True})
                    if (self.command, len(parts)) in [('POST', 2), ('PUT', 3)]:
                        return self.respond(200, bridge.upsert_preset(self.read_json(), parts[2] if len(parts) == 3 else None))
                    return self.respond(404, {'error': 'Not found.'})
                if parts == ['api', 'log'] and self.command == 'DELETE':
                    bridge.clear_log()
                    return self.respond(200, {'ok': True})
                if parts == ['api', 'input'] and self.command == 'PUT':
                    return self.respond(200, bridge.set_input({**bridge.input, **self.read_json()}))
                if parts[:2] != ['api', 'rules']:
                    return self.respond(404, {'error': 'Not found.'})
                if self.command == 'POST' and len(parts) == 4 and parts[3] == 'test':
                    return self.respond(200, {'status': bridge.test(parts[2])})
                if self.command == 'DELETE' and len(parts) == 3:
                    bridge.delete(parts[2])
                    return self.respond(200, {'ok': True})
                if (self.command, len(parts)) not in [('POST', 2), ('PUT', 3)]:
                    return self.respond(404, {'error': 'Not found.'})
                raw = self.read_json()
                return self.respond(200, bridge.upsert(raw, parts[2] if len(parts) == 3 else None))
            except (ValueError, TypeError) as exc:
                self.respond(400, {'error': str(exc)})
            except KeyError as exc:
                self.respond(404, {'error': exc.args[0]})
            except OSError as exc:
                self.respond(500, {'error': f'Could not save settings: {exc}'})

        def mutation(self):
            with bridge.lock:
                return self.mutation_locked()

        do_POST = do_PUT = do_DELETE = mutation
    return Handler

