"""Bridge runtime lifecycle, input evaluation and status snapshots."""

import json
import os
import signal
import socket
import threading
import time
from collections import deque
from pathlib import Path

from .actions import ActionOutputMixin
from .configuration import ConfigurationMixin
from .constants import PROTOCOLS, STALE_SECONDS
from .followers import DMXFollowersMixin
from .network import network_addresses
from .protocols import parse_artdmx, parse_ddp, parse_e131
from .sequences import SequencePlaybackMixin
from .validation import condition_bounds, follows_dmx, select_rule, validate_input
from config_lock import ConfigLock


class Bridge(ConfigurationMixin, ActionOutputMixin, DMXFollowersMixin, SequencePlaybackMixin):
    """Own shared runtime state and coordinate input, configuration and outputs.

    Feature mixins use this same instance and RLock, preserving atomic edits,
    cancellation and output ordering. They never own a second copy of state.
    """

    def __init__(self, config_path):
        self.path = Path(config_path)
        self.config_lock = ConfigLock(self.path.parent / ".config-lock.json")
        self.shows = None
        self.armed = True
        self.scripts_enabled = False
        self.last_sent = {}
        self.script_slots = threading.BoundedSemaphore(4)
        self.script_processes = set()
        self.sequence_runs = []
        self.sequence_threads = set()
        self.fade_pending, self.fade_cache, self.cue_levels = {}, {}, {}
        self.fade_timer = None
        self.follow_generation = 0
        self.midi_outputs = {}
        self.addresses = network_addresses()
        self.artnet_bind = '0.0.0.0' 
        self.lock = threading.RLock()
        self.rules, self.active, self.universes = [], {}, {}
        self.selected = {}
        self.presets = []
        self.group_labels = {}
        self.events = deque(maxlen=100)
        self.packets = self.sent = 0
        self.receiver_errors = {}
        self.input = validate_input({'protocol': 'artnet'})
        self.ddp_pending = {}
        self.output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.output.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.output.settimeout(0.2)
        if self.path.exists():
            saved = self.validate_show(json.loads(self.path.read_text()))
            self.input, self.rules, self.presets, self.group_labels = (saved[k] for k in ('input', 'rules', 'presets', 'group_labels'))
        else:
            self.save([])

    def set_runtime(self, raw):
        if not isinstance(raw, dict) or any(k not in ('armed', 'scripts_enabled') or type(v) is not bool for k, v in raw.items()):
            raise ValueError('Runtime controls must be true or false.')
        with self.lock:
            self.armed = raw.get('armed', self.armed)
            self.scripts_enabled = raw.get('scripts_enabled', self.scripts_enabled)
            if not self.armed:
                self.cancel_sequences('Outputs paused')
            self.reset_followers()
            self.baseline({(r['universe'], r['channel']) for r in self.rules})
        return dict(armed=self.armed, scripts_enabled=self.scripts_enabled)

    def slot_live(self, frame, index, now):
        return bool(frame and index < len(frame['levels']) and frame['levels'][index] is not None and
                    now - frame.get('slot_seen', [frame['seen']] * len(frame['levels']))[index] < STALE_SECONDS)

    def baseline(self, keys):
        for universe, channel in keys:
            frame = self.universes.get(universe)
            if self.slot_live(frame, channel - 1, time.monotonic()):
                group = [r for r in self.rules if (r['universe'], r['channel']) == (universe, channel)]
                winner = select_rule(group, frame['levels'][channel - 1])
                self.selected[(universe, channel)] = winner['id'] if winner else None
                for rule in group:
                    self.active[rule['id']] = rule['enabled'] if follows_dmx(rule) else bool(winner and rule['id'] == winner['id'])

    def clear_log(self):
        with self.lock:
            self.events.clear()

    def log(self, rule, reason, status, detail):
        self.events.appendleft(dict(time=time.time(), name=rule['name'], reason=reason, status=status, detail=detail))

    def close(self):
        with self.lock:
            self.armed = self.scripts_enabled = False
            self.cancel_sequences('Bridge stopped')
            self.reset_followers()
            for process in list(self.script_processes):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for output in self.midi_outputs.values():
                output.close()
            self.midi_outputs.clear()
            self.output.close()

    def test(self, rule_id):
        with self.lock:
            rule = next((r for r in self.rules if r['id'] == rule_id), None)
            if not rule:
                raise KeyError('Trigger not found.')
            return self.send(rule, 'Manual test')

    def apply_levels(self, universe, levels, source, changed=None):
        if universe not in self.universes and len(self.universes) >= 256:
            oldest = min(self.universes, key=lambda u: self.universes[u]['seen'])
            del self.universes[oldest]
        now = time.monotonic()
        previous = self.universes.get(universe)
        stamps = previous.get('slot_seen', [0] * 512)[:] if previous and changed is not None else [now] * len(levels)
        if changed is not None:
            for index in changed:
                stamps[index] = now
        self.universes[universe] = dict(levels=levels, source=source, seen=now, slot_seen=stamps)
        groups = {}
        for rule in self.rules:
            if rule['universe'] == universe:
                groups.setdefault(rule['channel'], []).append(rule)
        for channel, group in groups.items():
            index = channel - 1
            if index >= len(levels) or levels[index] is None or (changed is not None and index not in changed):
                continue
            for follower in group:
                if not follows_dmx(follower):
                    continue
                old_level = self.cue_levels.get(follower['id']) if self.slot_live(previous, index, now) else None
                self.cue_levels[follower['id']] = levels[index]
                if follower['enabled'] and self.armed:
                    if follower['action'] == 'fade':
                        self.send_fade(follower, levels[index])
                    else:
                        self.cross_cues(follower, old_level, levels[index])
            winner = select_rule(group, levels[index])
            winner_id = winner['id'] if winner else None
            key = (universe, channel)
            if winner and self.selected.get(key) != winner_id:
                self.send(winner, f"CH {channel} → {levels[index]}")
            self.selected[key] = winner_id
            for rule in group:
                self.active[rule['id']] = rule['enabled'] if follows_dmx(rule) else rule['id'] == winner_id

    def receive(self, packet, source, protocol='artnet'):
        with self.lock:
            if protocol != self.input['protocol']:
                return
            parsed = {'artnet': parse_artdmx, 'e131': parse_e131, 'ddp': parse_ddp}[protocol](packet)
            if parsed is None:
                return
            self.packets += 1
            if protocol != 'ddp':
                self.apply_levels(*parsed, source)
                return
            offset, values, push = parsed
            now = time.monotonic()
            self.ddp_pending = {key: value for key, value in self.ddp_pending.items() if now - value['seen'] < STALE_SECONDS}
            if source not in self.ddp_pending and len(self.ddp_pending) >= 4:
                del self.ddp_pending[min(self.ddp_pending, key=lambda key: self.ddp_pending[key]['seen'])]
            pending = self.ddp_pending.setdefault(source, dict(seen=now, values={}))
            pending['seen'] = now
            pending['values'].update(enumerate(values, offset))
            if not push:
                return
            blocks = {}
            for absolute, value in pending['values'].items():
                block, index = divmod(absolute, 512)
                blocks.setdefault(block, {})[index] = value
            for block, changes in blocks.items():
                previous = self.universes.get(block)
                levels = previous['levels'][:] if previous else [None] * 512
                for index, value in changes.items():
                    levels[index] = value
                self.apply_levels(block, levels, source, changes)
            del self.ddp_pending[source]

    def snapshot(self, universe):
        with self.lock:
            now = time.monotonic()
            def frame_info(u):
                frame = self.universes.get(u)
                return dict(universe=u, age=round(now - frame['seen'], 2) if frame else None,
                            source=frame['source'] if frame else None,
                            live=bool(frame and now - frame['seen'] < STALE_SECONDS))
            frames = [frame_info(u) for u in sorted(set(self.universes) | {r['universe'] for r in self.rules} | {universe})]
            frame = self.universes.get(universe)
            rules = []
            for rule in self.rules:
                f = self.universes.get(rule['universe'])
                level = f['levels'][rule['channel'] - 1] if f and rule['channel'] <= len(f['levels']) else None
                rules.append(dict(rule, level=level, live=self.slot_live(f, rule['channel'] - 1, now),
                                  active=self.active.get(rule['id'], False),
                                  matches=bool(rule['enabled'] and level is not None and condition_bounds(rule)[0] <= level <= condition_bounds(rule)[1])))
            return dict(armed=self.armed, scripts_enabled=self.scripts_enabled,
                        receive_addresses=[self.artnet_bind] if self.input['protocol'] == 'artnet' and self.artnet_bind != '0.0.0.0' else self.addresses,
                        show=self.shows.info() if self.shows else None,
                        sequences=self.sequence_snapshot(),
                        rules=rules, group_labels=dict(self.group_labels), presets=list(self.presets), universes=frames, levels=frame['levels'] if frame else [],
                        live_slots=[self.slot_live(frame, i, now) for i in range(512)],
                        input=dict(self.input), input_port=PROTOCOLS[self.input['protocol']][1],
                        packets=self.packets, sent=self.sent, events=list(self.events),
                        receiver_error=self.receiver_errors.get(self.input['protocol']))

