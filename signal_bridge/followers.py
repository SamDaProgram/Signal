"""Rate-limited DMX fades and cue crossings."""

import threading
import time

from .protocols import encode_osc
from .validation import fade_args, follows_dmx, validate_rule


class DMXFollowersMixin:
    """Rate-limited DMX fades and cue crossings. Uses the Bridge runtime and its shared lock."""

    def reset_followers(self, rule_ids=None):
        # Called under the bridge lock. Unrelated edits must not drop a final fade value.
        if rule_ids is None:
            self.follow_generation += 1
            if self.fade_timer:
                self.fade_timer.cancel()
            self.fade_timer = None
            self.fade_pending.clear()
            self.fade_cache.clear()
            self.cue_levels.clear()
        else:
            for rule_id in rule_ids:
                self.fade_pending.pop(rule_id, None)
                self.fade_cache.pop(rule_id, None)
                self.cue_levels.pop(rule_id, None)
        now = time.monotonic()
        for rule in self.rules:
            if rule_ids is not None and rule['id'] not in rule_ids:
                continue
            frame = self.universes.get(rule['universe'])
            if follows_dmx(rule) and self.slot_live(frame, rule['channel']-1, now):
                self.cue_levels[rule['id']] = frame['levels'][rule['channel']-1]

    def send_fade(self, rule, level, force=False):
        if not self.armed or (not rule['enabled'] and not force):
            return 'skipped'
        packet = encode_osc(rule['address'], fade_args(rule, level))
        now = time.monotonic()
        previous = self.fade_cache.get(rule['id'])
        if previous and previous[1] == packet and not force:
            self.fade_pending.pop(rule['id'], None)
            return 'skipped'
        if previous and now-previous[0] < 1/rule['fade_rate'] and not force:
            self.fade_pending[rule['id']] = (rule, level)
            self.schedule_fades()
            return 'pending'
        self.fade_pending.pop(rule['id'], None)
        try:
            self.output.sendto(packet, (rule['host'], rule['port']))
            self.sent += 1
            self.fade_cache[rule['id']] = (now, packet)
            if force:
                self.log(rule, 'Manual test', 'sent', f"DMX {level} → {rule['address']}")
            return 'sent'
        except OSError as exc:
            self.fade_cache[rule['id']] = (now, None)
            self.log(rule, 'DMX fade', 'error', str(exc))
            return 'error'

    def schedule_fades(self):
        if self.fade_timer is None and self.fade_pending:
            generation = self.follow_generation
            self.fade_timer = threading.Timer(1/60, self.flush_fades, args=(generation,))
            self.fade_timer.daemon = True
            self.fade_timer.start()

    def flush_fades(self, generation):
        with self.lock:
            if generation != self.follow_generation:
                return
            self.fade_timer = None
            for rule, level in list(self.fade_pending.values()):
                frame = self.universes.get(rule['universe'])
                current = next((r for r in self.rules if r['id'] == rule['id']), None)
                if self.armed and current == rule and self.slot_live(frame, rule['channel']-1, time.monotonic()):
                    self.send_fade(rule, level)
                else:
                    self.fade_pending.pop(rule['id'], None)
            self.schedule_fades()

    def cross_cues(self, rule, previous, level):
        if previous is None or previous == level or not self.armed:
            return
        duration = sum(step['delay_ms'] for step in rule['steps'])
        start, end = previous*duration, level*duration
        at, crossed = 0, []
        for index, step in enumerate(rule['steps']):
            at += step['delay_ms']*255
            if (start < at <= end) or (end < start and rule['sequence_direction'] == 'both' and end <= at < start):
                crossed.append((index, step))
            elif start == 0 and end > 0 and at == 0:
                crossed.append((index, step))
        if end < start:
            crossed.reverse()
        generation = self.follow_generation
        batch = {'stopped': False}
        def complete(status, detail):
            with self.lock:
                if status in ('error', 'skipped') and rule['sequence_on_error'] == 'stop':
                    batch['stopped'] = True
        for index, step in crossed:
            if batch['stopped']:
                break
            action = validate_rule(step)
            action['id'] = f"{rule['id']}:cue:{index}"
            status = self.send(action, f"{rule['name']} · DMX {previous} → {level} · Step {index+1}",
                               on_complete=complete,
                               permit=lambda: generation == self.follow_generation and not batch['stopped'] and any(r == rule and r['enabled'] for r in self.rules))
            if status != 'running':
                complete(status, '')
            self.last_sent.pop(action['id'], None)

