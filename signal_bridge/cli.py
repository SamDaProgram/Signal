"""Command-line startup, privilege drop and shutdown."""

import argparse
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .constants import PROTOCOLS, ROOT
from .http import make_handler
from .network import receiver
from .shows import ShowFiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=80, help='Local web port (default 80)')
    parser.add_argument('--preshow-url', default='http://127.0.0.1:8081/', help='Preshow tab URL')
    parser.add_argument('--artnet-bind', default='0.0.0.0', help='Art-Net interface IPv4 address')
    parser.add_argument('--config', type=Path, default=ROOT / 'settings.json', help='Legacy settings to import on first launch')
    parser.add_argument('--show-dir', type=Path, default=ROOT / 'shows', help='Directory for show files')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), BaseHTTPRequestHandler)
    except PermissionError as exc:
        parser.exit(77, f'Cannot open the requested ports: {exc}\nPort 80 may require administrator permission. Use Start.command or --port 8080.\n')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'Cannot start: {exc}\nCheck that port {args.port} (web) is free.\n')
    # Bind privileged ports first, then run handlers and settings writes as the invoking user.
    if os.geteuid() == 0:
        if not os.environ.get('SUDO_UID') or int(os.environ['SUDO_UID']) == 0:
            parser.exit(1, 'Run from your normal Mac account using sudo, not a root login.\n')
        uid, gid = int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID'])
        import pwd
        os.initgroups(pwd.getpwuid(uid).pw_name, gid)
        os.setgid(gid)
        os.setuid(uid)
    try:
        shows = ShowFiles(args.show_dir, args.config)
        bridge = shows.bridge
        bridge.preshow_url = args.preshow_url
        bridge.artnet_bind = args.artnet_bind
        server.RequestHandlerClass = make_handler(bridge)
    except (OSError, ValueError, KeyError) as exc:
        server.server_close()
        parser.exit(1, f'Cannot load show: {exc}\n')
    stop = threading.Event()
    workers, sockets = [], []
    for protocol, (name, port, _, _) in PROTOCOLS.items():
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp.bind((args.artnet_bind if protocol == 'artnet' else '0.0.0.0', port))
        except OSError as exc:
            bridge.receiver_errors[protocol] = f'Cannot listen on UDP {port}: {exc}'
            print(f'{name}: {bridge.receiver_errors[protocol]}', flush=True)
            udp.close()
            continue
        sockets.append(udp)
        worker = threading.Thread(target=receiver, args=(bridge, udp, stop, protocol), daemon=True)
        workers.append(worker)
        worker.start()
        print(f'{name} listening on UDP {port}', flush=True)
    print(f'Open http://localhost{":" + str(args.port) if args.port != 80 else ""} — Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for worker in workers:
            worker.join(1)
        for udp in sockets:
            udp.close()
        server.server_close()
        bridge.close()

