#!/usr/bin/env python3
"""Preshow control page and JSON status, controlled by HTTP or OSC."""
import argparse
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socketserver
import struct
import threading
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError


def osc_string(packet, offset):
    end = packet.index(b'\0', offset)
    following = (end + 4) & ~3
    if following > len(packet) or any(packet[end:following]):
        raise ValueError('Invalid OSC string padding')
    return packet[offset:end].decode('ascii'), following


def decode_values(packet, depth=0):
    """Accept exact /preshow messages and immediate bundles; reject bad packets."""
    if not packet or len(packet) % 4 or depth > 8:
        raise ValueError('Invalid OSC packet')
    if packet.startswith(b'#bundle\0'):
        if len(packet) < 16 or packet[8:16] != struct.pack('>Q', 1):
            raise ValueError('Only immediate OSC bundles are supported')
        values, offset = [], 16
        while offset < len(packet):
            size = struct.unpack_from('>i', packet, offset)[0]
            offset += 4
            if size <= 0 or offset + size > len(packet):
                raise ValueError('Invalid OSC bundle element')
            values.extend(decode_values(packet[offset:offset + size], depth + 1))
            offset += size
        return values
    address, offset = osc_string(packet, 0)
    if address != '/preshow':
        return []
    tags, offset = osc_string(packet, offset)
    if tags in (',T', ',F'):
        value = tags == ',T'
    elif tags in (',i', ',f'):
        value = struct.unpack_from('>' + tags[1], packet, offset)[0]
        offset += 4
        if value not in (0, 1):
            raise ValueError('Expected 0 or 1')
        value = bool(value)
    elif tags == ',s':
        value, offset = osc_string(packet, offset)
        if value.lower() not in ('true', 'false', '1', '0'):
            raise ValueError('Expected true or false')
        value = value.lower() in ('true', '1')
    else:
        raise ValueError('Expected one boolean, integer, float, or string')
    if offset != len(packet):
        raise ValueError('Unexpected trailing OSC data')
    return [value]


def trigger_preshow(bridge_url, trigger_name):
    """Use the active show's saved start action and the bridge's output controls."""
    def request(path, data=None, show=None):
        headers = {'Accept': 'application/json'}
        if show:
            headers['X-Signal-Show'] = show
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(bridge_url.rstrip('/') + path,
                      data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urlopen(req, timeout=3) as response:
            return json.load(response)
    try:
        snapshot = request('/api/state')
        if not snapshot.get('armed'):
            raise ValueError('Signal outputs are paused. Enable outputs before starting preshow.')
        matches = [rule for rule in snapshot.get('rules', [])
                   if rule.get('name', '').casefold() == trigger_name.casefold()
                   and (rule.get('action') == 'midi' or
                        (rule.get('action') == 'sequence' and rule.get('sequence_source') == 'time'))]
        if len(matches) != 1:
            raise ValueError(f'Open the correct show in Signal and configure one MIDI or timed sequence trigger named {trigger_name!r}.')
        rule = matches[0]
        if not rule.get('enabled'):
            raise ValueError(f'Enable the {trigger_name!r} trigger in Signal first.')
        show = snapshot.get('show', {}).get('current')
        if not show:
            raise ValueError('Signal must have a saved show loaded.')
        result = request('/api/rules/' + rule['id'] + '/test', {}, show)
        accepted = ('sent', 'running') if rule.get('action') == 'sequence' else ('sent',)
        if result.get('status') not in accepted:
            raise ValueError('Preshow was not sent or started. Check Signal Activity, then try again.')
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError('Could not reach Signal or its active show changed. Check Signal, then try again.') from exc


class State:
    def __init__(self, start_action=None):
        self.start_action = start_action
        self.preshow = False
        self.lock = threading.Lock()

    def update(self, packet):
        try:
            values = decode_values(packet)
        except (ValueError, UnicodeError, struct.error):
            return
        if values:
            with self.lock:
                self.preshow = values[-1]

    def start(self):
        with self.lock:
            if not self.preshow:
                if self.start_action is None:
                    raise ValueError('No preshow start action is configured.')
                self.start_action()
                self.preshow = True
            return json.dumps({'preshow': self.preshow}).encode()

    def json(self):
        with self.lock:
            return json.dumps({'preshow': self.preshow}).encode() + b'\n'


def handlers(state, bridge_url='http://127.0.0.1'):
    class HTTPHandler(BaseHTTPRequestHandler):
        def respond(self, body, status=200, mime='application/json; charset=utf-8', head=False):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Vary', 'Accept')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self, head=False):
            path = urlsplit(self.path).path
            if path == '/api/navigation':
                return self.respond(json.dumps({'config': bridge_url.rstrip('/') + '/'}).encode(), head=head)
            # Preserve existing root API clients while browsers get the home page.
            if path == '/api/status' or (path == '/' and 'text/html' not in self.headers.get('Accept', '')):
                return self.respond(state.json(), head=head)
            files = {'/': ('preshow.html', 'text/html; charset=utf-8'),
                     '/preshow.js': ('preshow.js', 'text/javascript; charset=utf-8'),
                     '/preshow.css': ('preshow.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.respond(b'{"error":"Not found"}', 404, head=head)
            name, mime = files[path]
            return self.respond((Path(__file__).resolve().parent / 'static' / name).read_bytes(), mime=mime, head=head)

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ('/api/status', '/api/start-preshow'):
                return self.respond(b'{"error":"Not found"}', 404)
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + self.headers.get('Host', ''):
                return self.respond(b'{"error":"Same-origin requests only"}', 403)
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                return self.respond(b'{"error":"Expected application/json"}', 415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 1024:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(length))
                if path == '/api/start-preshow':
                    if data != {}:
                        raise ValueError('Expected an empty object')
                elif not isinstance(data, dict) or set(data) != {'preshow'} or type(data['preshow']) is not bool:
                    raise ValueError('Expected a preshow boolean')
            except (ValueError, UnicodeError):
                return self.respond(b'{"error":"Expected {preshow: true} or {preshow: false} as JSON"}', 400)
            if path == '/api/start-preshow':
                try:
                    return self.respond(state.start())
                except (ValueError, TypeError, KeyError) as exc:
                    return self.respond(json.dumps({'error': str(exc)}).encode(), 502)
            with state.lock:
                state.preshow = data['preshow']
                body = json.dumps({'preshow': state.preshow}).encode()
            self.respond(body)

        def log_message(self, format, *args):
            pass

    class OSCHandler(socketserver.BaseRequestHandler):
        def handle(self):
            state.update(self.request[0])

    return HTTPHandler, OSCHandler


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError('Port must be between 1 and 65535')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0', help='HTTP and OSC bind IPv4 (default: all interfaces)')
    parser.add_argument('--port', type=port, default=8081, help='HTTP TCP port (default: 8081)')
    parser.add_argument('--osc-port', type=port, default=9000, help='OSC UDP port (default: 9000)')
    parser.add_argument('--bridge-url', default='http://127.0.0.1', help='Signal bridge URL (default: http://127.0.0.1)')
    parser.add_argument('--preshow-trigger', default='START PRESHOW', help='Saved MIDI or timed sequence trigger to run in the active Signal show')
    args = parser.parse_args()
    http_handler, osc_handler = handlers(State(lambda: trigger_preshow(args.bridge_url, args.preshow_trigger)), args.bridge_url)
    with ExitStack() as stack:
        try:
            http = stack.enter_context(ThreadingHTTPServer((args.host, args.port), http_handler))
            osc = stack.enter_context(socketserver.UDPServer((args.host, args.osc_port), osc_handler))
        except OSError as exc:
            parser.exit(1, f'Cannot start preshow server: {exc}\n')
        thread = threading.Thread(target=osc.serve_forever, daemon=True)
        thread.start()
        print(f'Preshow controls: http://{"127.0.0.1" if args.host == "0.0.0.0" else args.host}:{args.port}/', flush=True)
        print(f'OSC: UDP {args.host}:{args.osc_port}, address /preshow, argument 1 or 0', flush=True)
        print('Initial value: false. Press Control+C to stop.', flush=True)
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            osc.shutdown()
            thread.join()


if __name__ == '__main__':
    main()
