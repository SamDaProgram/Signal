"""Network interface discovery and UDP receiver lifecycle."""

import ipaddress
import re
import socket
import subprocess
import time
from pathlib import Path


def network_addresses():
    addresses = {'127.0.0.1'}
    try:
        addresses.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    if Path('/sbin/ifconfig').exists():
        try:
            result = subprocess.run(['/sbin/ifconfig'], capture_output=True, text=True, timeout=2)
            addresses.update(re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)', result.stdout))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return sorted(addresses - {'0.0.0.0'}, key=lambda ip: (ipaddress.ip_address(ip).is_loopback, ip))


class MulticastSubscriptions:
    """Track memberships on the receiver thread; rejoin when the interface changes."""
    def __init__(self, sock):
        self.sock = sock
        self.joined = set()
        self.last_attempt = 0
        self.last_target = None

    def sync(self, bridge):
        with bridge.lock:
            settings = dict(bridge.input)
            desired = {r['universe'] for r in bridge.rules if r['enabled'] and 1 <= r['universe'] <= 63999}
        desired.add(settings['monitor_universe'])
        target = {(u, settings['sacn_interface']) for u in desired} if settings['protocol'] == 'e131' else set()
        now = time.monotonic()
        if target == self.last_target and now - self.last_attempt < 3:
            return
        self.last_target, self.last_attempt = target, now
        errors = []
        for entry in self.joined - target:
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, self.membership(*entry))
                self.joined.remove(entry)
            except OSError as exc:
                errors.append(str(exc))
        for entry in sorted(target - self.joined):
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self.membership(*entry))
                self.joined.add(entry)
            except OSError as exc:
                errors.append(f'Universe {entry[0]}: {exc}')
        with bridge.lock:
            bridge.receiver_errors['e131'] = ('Multicast join failed; unicast still works. ' + '; '.join(errors[:3])) if errors else None

    @staticmethod
    def membership(universe, interface):
        return socket.inet_aton(f'239.255.{universe >> 8}.{universe & 255}') + socket.inet_aton(interface)


def receiver(bridge, sock, stop, protocol='artnet'):
    sock.settimeout(0.25)
    multicast = MulticastSubscriptions(sock) if protocol == 'e131' else None
    while not stop.is_set():
        try:
            if multicast:
                multicast.sync(bridge)
            packet, peer = sock.recvfrom(65535)
            bridge.receive(packet, peer[0], protocol)
        except socket.timeout:
            continue
        except OSError as exc:
            if not stop.is_set():
                with bridge.lock:
                    bridge.receiver_errors[protocol] = str(exc)
            break

