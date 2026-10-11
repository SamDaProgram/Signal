"""OSC, MIDI and script output execution."""

import copy
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .protocols import encode_osc
from .validation import validate_rule


def midi_module():
    try:
        import mido
        return mido
    except ImportError:
        raise ValueError('MIDI requires mido and python-rtmidi. See README for installation.') from None


class ActionOutputMixin:
    """OSC, MIDI and script output execution. Uses the Bridge runtime and its shared lock."""

    def run_script(self, rule, reason, on_complete=None, permit=None):
        status, detail = 'error', 'Script did not start.'
        output, process = None, None
        try:
            output = tempfile.TemporaryFile()
            with self.lock:
                if not self.armed or not self.scripts_enabled or (permit is not None and not permit()):
                    status, detail = 'skipped', 'Script execution is paused.'
                    return
                presets = copy.deepcopy(self.presets)
                env = dict(os.environ, SIGNAL_SCRIPT_FIELDS=json.dumps({f['key']: f['value'] for f in rule.get('script_fields', [])}),
                           SIGNAL_SCRIPT_PRESETS=json.dumps([f['value'] for f in rule.get('script_fields', []) if f['type'] == 'preset' and f['value']]))
                process = subprocess.Popen([rule['script_path'], *rule['script_args']],
                    cwd=rule['script_cwd'] or None, stdin=subprocess.DEVNULL,
                    stdout=output if rule.get('script_output') == 'preset_actions' else subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, env=env, start_new_session=True)
                self.script_processes.add(process)
            try:
                code = process.wait(timeout=rule['script_timeout'])
                status, detail = ('sent', 'Script completed (exit 0)') if code == 0 else ('error', f'Script exited with code {code}')
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                status, detail = 'error', f"Script timed out after {rule['script_timeout']}s"
            with self.lock:
                if status == 'sent' and rule.get('script_output') == 'preset_actions':
                    output.seek(0)
                    status, detail = self.script_actions(rule, output.read(65537), presets, reason, permit)
                if status == 'sent':
                    self.sent += 1
        except (OSError, ValueError, TypeError) as exc:
            status, detail = 'error', str(exc)
        finally:
            if output is not None:
                output.close()
            with self.lock:
                self.script_processes.discard(process)
                self.script_slots.release()
                self.log(rule, reason, status, detail)
            if on_complete:
                on_complete(status, detail)

    def script_actions(self, rule, output, presets, reason, permit=None):
        """Validate the complete request before dispatching any OSC/MIDI outputs."""
        if len(output) > 65536:
            raise ValueError('Script result exceeds 64 KB.')
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise ValueError('Script must return a JSON object with preset_actions.') from None
        requests = result.get('preset_actions') if isinstance(result, dict) else None
        if not isinstance(requests, list) or len(requests) > 32:
            raise ValueError('Script must return at most 32 preset actions.')
        allowed = {f['value'] for f in rule.get('script_fields', []) if f['type'] == 'preset' and f['value']}
        actions = []
        for request in requests:
            if not isinstance(request, dict) or set(request) - {'preset_id', 'address', 'args'}:
                raise ValueError('Invalid script preset action.')
            preset_id = request.get('preset_id')
            if not isinstance(preset_id, str) or preset_id not in allowed:
                raise ValueError('Script requested a preset that is not selected in its fields.')
            preset = next((p for p in presets if p['id'] == preset_id), None)
            if preset is None or preset.get('action', 'osc') not in ('osc', 'midi'):
                raise ValueError('Script output preset is missing or is not OSC/MIDI.')
            raw = dict(preset, args=request.get('args', []) if preset.get('action', 'osc') == 'osc' else [])
            if 'address' in request and preset.get('action', 'osc') == 'osc':
                raw['address'] = request['address']
            action = validate_rule(raw)
            action['id'] = str(uuid.uuid4())
            actions.append(action)
        if not self.armed or not self.scripts_enabled or (permit is not None and not permit()):
            return 'skipped', 'Script outputs canceled or paused.'
        if not actions:
            return 'sent', 'Script completed; no outputs requested.'
        for action in actions:
            if self.send(action, 'Script: ' + rule['name']) == 'error':
                return 'error', 'Script preset output failed; remaining outputs stopped.'
            self.last_sent.pop(action['id'], None)
        return 'sent', f'Script completed; sent {len(actions)} preset action(s).'

    def send(self, rule, reason, on_complete=None, permit=None):
        now = time.monotonic()
        if not self.armed:
            self.log(rule, reason, 'skipped', 'Outputs are paused.')
            return 'skipped'
        if reason != 'Manual test' and now - self.last_sent.get(rule['id'], -float('inf')) < rule.get('cooldown_ms', 0) / 1000:
            self.log(rule, reason, 'skipped', 'Cooldown active; this transition was skipped.')
            return 'skipped'
        try:
            action = rule.get('action', 'osc')
            if action == 'fade':
                frame = self.universes.get(rule['universe'])
                if not self.slot_live(frame, rule['channel']-1, now):
                    raise ValueError('Send live DMX input before testing this fade.')
                return self.send_fade(rule, frame['levels'][rule['channel']-1], force=True)
            if action == 'sequence':
                if rule['sequence_source'] == 'dmx':
                    raise ValueError('Move the live DMX fader to test cue crossings; preview timing sends no outputs.')
                return self.start_sequence(rule, reason)
            if action == 'script':
                if not self.scripts_enabled:
                    raise ValueError('Scripts are disabled. Enable scripts in Settings for this session.')
                if not Path(rule['script_path']).is_file() or not os.access(rule['script_path'], os.X_OK):
                    raise ValueError('Script executable is missing or not executable.')
                if not self.script_slots.acquire(blocking=False):
                    raise ValueError('Four scripts are already running; trigger skipped.')
                worker = threading.Thread(target=self.run_script, args=(dict(rule), reason, on_complete, permit), daemon=True)
                try:
                    worker.start()
                except RuntimeError:
                    self.script_slots.release()
                    raise
                self.last_sent[rule['id']] = now
                self.log(rule, reason, 'running', rule['script_path'])
                return 'running'
            if action == 'midi':
                mido = midi_module()
                kind = rule['midi_type']
                args = {} if kind in ('start', 'stop', 'continue') else {'channel': rule['midi_channel'] - 1}
                if kind in ('note_on', 'note_off'):
                    args.update(note=rule['midi_number'], velocity=rule['midi_value'])
                elif kind == 'control_change':
                    args.update(control=rule['midi_number'], value=rule['midi_value'])
                elif kind == 'program_change':
                    args.update(program=rule['midi_number'])
                name = rule['midi_port']
                try:
                    if name not in self.midi_outputs:
                        self.midi_outputs[name] = mido.open_output(name)
                    self.midi_outputs[name].send(mido.Message(kind, **args))
                except Exception:
                    output = self.midi_outputs.pop(name, None)
                    if output:
                        output.close()
                    raise
                detail = f'MIDI {name}: {kind}'
            else:
                self.output.sendto(encode_osc(rule['address'], rule['args']), (rule['host'], rule['port']))
                detail = f"{rule['host']}:{rule['port']} {rule['address']}"
            self.sent += 1
            self.last_sent[rule['id']] = now
            status = 'sent'
        except Exception as exc:
            status, detail = 'error', str(exc)
        self.log(rule, reason, status, detail)
        return status

