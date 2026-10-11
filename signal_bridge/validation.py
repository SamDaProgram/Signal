"""Show action schemas, linked presets, conditions and fade interpolation."""

import ipaddress
import math
import re
import sys
import uuid
from pathlib import Path

from .constants import ACTION_FIELDS, PROTOCOLS, ROOT
from .protocols import encode_osc
from .values import integer, text_value


def validate_script_fields(fields):
    if not isinstance(fields, list) or len(fields) > 32:
        raise ValueError('Script fields must be an array of at most 32 fields.')
    result, keys = [], set()
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError('Each script field must be an object.')
        key = text_value(field.get('key'), 'Field key', 64)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or key in keys:
            raise ValueError('Script field keys must be unique identifiers.')
        keys.add(key)
        label = text_value(field.get('label', key), 'Field label', 100)
        kind = field.get('type', 'text')
        value = field.get('value', False if kind == 'boolean' else 0 if kind == 'number' else '')
        if kind in ('text', 'preset'):
            value = text_value(value, 'Field value', 4096, empty=True)
            if kind == 'preset' and value:
                value = str(uuid.UUID(value))
        elif kind == 'number':
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Number fields require finite numbers.')
        elif kind == 'boolean':
            if type(value) is not bool:
                raise ValueError('Boolean fields require true or false.')
        else:
            raise ValueError('Field type must be text, number, boolean, or preset.')
        result.append(dict(key=key, label=label, type=kind, value=value))
    return result


def preshow_template():
    return dict(action='script', script_path=sys.executable,
                script_args=[str(ROOT / 'scripts' / 'preshow_gate.py')],
                script_cwd='', script_timeout=10, script_output='preset_actions',
                script_fields=[
                    dict(key='preshow_url', label='Preshow status URL', type='text', value='http://127.0.0.1:8081/api/status'),
                    dict(key='fpp', label='FPP preset', type='preset', value=''),
                    dict(key='fpp_stop_address', label='FPP stop preshow OSC address', type='text', value='/stoppre'),
                    dict(key='fpp_fade_address', label='FPP fade OSC address', type='text', value='/preshow/fade')])


def linked_rule(raw, presets):
    if not isinstance(raw, dict):
        raise ValueError('A trigger must be an object.')
    preset_id = raw.get('preset_id')
    if not preset_id:
        return validate_rule(raw)
    preset = next((p for p in presets if p['id'] == preset_id), None)
    if preset is None:
        raise ValueError('Linked preset does not exist. Select another preset or Custom action.')
    action = preset.get('action', 'osc')
    values = {key: preset[key] for key in ACTION_FIELDS[action] if key in preset and (key != 'address' or preset[key])}
    return validate_rule(dict(raw, **values, action=action))


def validate_input(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('protocol'), str) or raw['protocol'] not in PROTOCOLS:
        raise ValueError('Select Art-Net, E1.31, or DDP.')
    protocol = raw['protocol']
    interface = raw.get('sacn_interface', '0.0.0.0')
    try:
        ipaddress.IPv4Address(interface)
    except (ValueError, TypeError):
        raise ValueError('sACN interface must be this Mac’s IPv4 address or 0.0.0.0 for automatic selection.') from None
    low, high = PROTOCOLS[protocol][2:]
    return dict(protocol=protocol, sacn_interface=interface,
                monitor_universe=integer(raw.get('monitor_universe', low), low, high, 'Monitor universe / block'))


def follows_dmx(rule):
    return rule.get('action') == 'fade' or (rule.get('action') == 'sequence' and rule.get('sequence_source') == 'dmx')


def validate_fade(raw):
    kind, curve = raw.get('fade_type', 'f'), raw.get('fade_curve', 'linear')
    if kind not in ('f', 'i') or curve not in ('linear', 'smooth', 'hold'):
        raise ValueError('Choose float or integer values and linear, smooth, or hold interpolation.')
    rate = integer(raw.get('fade_rate', 30), 1, 60, 'Fade output rate')
    frames = raw.get('fade_keyframes')
    if not isinstance(frames, list) or not 2 <= len(frames) <= 64:
        raise ValueError('A DMX fade needs 2–64 keyframes.')
    checked, width, previous = [], None, -1
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError('Each keyframe needs a DMX level and numeric values.')
        level = integer(frame.get('level'), 0, 255, 'Keyframe DMX level')
        values = frame.get('values')
        if level <= previous:
            raise ValueError('Keyframe DMX levels must be unique and in increasing order.')
        if not isinstance(values, list) or not 1 <= len(values) <= 8 or (width is not None and len(values) != width):
            raise ValueError('Use the same 1–8 values in every keyframe (one for brightness, three for RGB).')
        for value in values:
            if kind == 'i':
                integer(value, -2147483648, 2147483647, 'Keyframe integer')
            elif type(value) not in (int, float) or abs(value) > 3.402823466e38 or not math.isfinite(value):
                raise ValueError('Keyframe values must be finite OSC floats.')
        checked.append(dict(level=level, values=list(values)))
        width, previous = len(values), level
    values = [value for frame in checked for value in frame['values']]
    bounds = raw.get('fade_range', dict(min=min(0, *values), max=max(1, *values)))
    if not isinstance(bounds, dict):
        raise ValueError('Fade output range needs a minimum and maximum.')
    low, high = bounds.get('min'), bounds.get('max')
    if any(type(v) not in (int, float) or abs(v) > 3.402823466e38 or not math.isfinite(v) for v in (low, high)) or low >= high:
        raise ValueError('Fade output minimum must be smaller than its maximum; both must be finite OSC numbers.')
    if any(value < low or value > high for value in values):
        raise ValueError('Keyframe values must be within the fade output range.')
    display = raw.get('fade_input_display', 'dmx')
    if display not in ('dmx', 'normalized', 'percent'):
        raise ValueError('Input level display must be DMX, normalized, or percent.')
    return dict(fade_type=kind, fade_curve=curve, fade_rate=rate, fade_keyframes=checked,
                fade_range=dict(min=low, max=high), fade_input_display=display)


def fade_args(rule, level):
    frames = rule['fade_keyframes']
    left = right = frames[0]
    for frame in frames:
        right = frame
        if level <= frame['level']:
            break
        left = frame
    t = 0 if left['level'] == right['level'] else max(0, min(1, (level-left['level'])/(right['level']-left['level'])))
    if rule['fade_curve'] == 'smooth':
        t = t*t*(3-2*t)
    elif rule['fade_curve'] == 'hold':
        t = 1 if t >= 1 else 0
    values = [a*(1-t)+b*t for a, b in zip(left['values'], right['values'])]
    if rule['fade_type'] == 'i':
        values = [math.floor(value+.5) for value in values]
    return [dict(type=rule['fade_type'], value=value) for value in values]


def validate_rule(raw):
    if not isinstance(raw, dict):
        raise ValueError('A trigger must be an object.')
    action = raw.get('action', 'osc')
    if not isinstance(action, str) or action not in ACTION_FIELDS:
        raise ValueError('Select OSC, MIDI, Script, Sequence, or DMX fade.')
    if action not in ('osc', 'fade'):
        raw = dict(raw, host='127.0.0.1', port=8000, address='/go', args=[])
    if follows_dmx(raw):
        raw = dict(raw, operator='gte', threshold=0, threshold_max=255, cooldown_ms=0)
    name = raw.get('name', 'Untitled trigger')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Name must have 1–100 characters.')
    host = raw.get('host', '127.0.0.1')
    try:
        ipaddress.IPv4Address(host)
    except (ValueError, TypeError):
        raise ValueError('OSC destination must be an IPv4 address.') from None
    enabled = raw.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError('Enabled must be true or false.')
    rule = dict(name=name.strip(), enabled=enabled, host=host,
                universe=integer(raw.get('universe', 0), 0, 63999, 'Universe / block'),
                channel=integer(raw.get('channel', 1), 1, 512, 'Channel'),
                operator=raw.get('operator', 'gte'),
                threshold_max=integer(raw.get('threshold_max', 255), 0, 255, 'Upper level'),
                threshold=integer(raw.get('threshold', 100), 0, 255, 'Activation level'),
                port=integer(raw.get('port', 8000), 1, 65535, 'OSC port'),
                address=raw.get('address', '/go'), args=raw.get('args', []))
    if rule['operator'] not in ('gte', 'gt', 'lte', 'lt', 'eq', 'range'):
        raise ValueError('Choose a valid comparison operator.')
    low, high = condition_bounds(rule)
    if low > high:
        raise ValueError('The condition must match at least one DMX level (0–255).')
    action = raw.get('action', 'osc')
    if not isinstance(action, str) or action not in ACTION_FIELDS:
        raise ValueError('Select OSC, MIDI, Script, Sequence, or DMX fade.')
    # Keep legacy OSC records compatible when the new fields are absent.
    if 'action' in raw or action != 'osc':
        rule['action'] = action
    if raw.get('preset_id'):
        rule['preset_id'] = str(uuid.UUID(text_value(raw['preset_id'], 'Preset ID', 36)))
    if 'cooldown_ms' in raw:
        rule['cooldown_ms'] = integer(raw['cooldown_ms'], 0, 3600000, 'Cooldown (ms)')
    if action == 'osc':
        encode_osc(rule['address'], rule['args'])
    elif action == 'fade':
        rule['args'] = []
        rule.update(validate_fade(raw))
        encode_osc(rule['address'], fade_args(rule, 0))
    elif action == 'midi':
        rule['midi_port'] = text_value(raw.get('midi_port'), 'MIDI output port', 256)
        rule['midi_type'] = raw.get('midi_type', 'note_on')
        if rule['midi_type'] not in ('note_on', 'note_off', 'control_change', 'program_change', 'start', 'stop', 'continue'):
            raise ValueError('Unsupported MIDI message type.')
        rule['midi_channel'] = integer(raw.get('midi_channel', 1), 1, 16, 'MIDI channel')
        rule['midi_number'] = integer(raw.get('midi_number', 60), 0, 127, 'MIDI note / controller / program')
        rule['midi_value'] = integer(raw.get('midi_value', 127), 0, 127, 'MIDI velocity / value')
    elif action == 'script':
        rule['script_path'] = text_value(raw.get('script_path'), 'Executable path')
        if not Path(rule['script_path']).is_absolute():
            raise ValueError('Executable path must be absolute.')
        rule['script_cwd'] = text_value(raw.get('script_cwd', ''), 'Working directory', empty=True)
        if rule['script_cwd'] and not Path(rule['script_cwd']).is_absolute():
            raise ValueError('Working directory must be absolute.')
        args = raw.get('script_args', [])
        if not isinstance(args, list) or len(args) > 64:
            raise ValueError('Script arguments must be an array of at most 64 strings.')
        rule['script_args'] = [text_value(v, 'Script argument', 4096, empty=True) for v in args]
        rule['script_fields'] = validate_script_fields(raw.get('script_fields', []))
        rule['script_output'] = raw.get('script_output', 'ignore')
        if rule['script_output'] not in ('ignore', 'preset_actions'):
            raise ValueError('Invalid script output mode.')
        rule['script_timeout'] = integer(raw.get('script_timeout', 30), 1, 300, 'Script timeout (seconds)')
    elif action == 'sequence':
        rule['sequence_source'] = raw.get('sequence_source', 'time')
        rule['sequence_direction'] = raw.get('sequence_direction', 'forward')
        if rule['sequence_source'] not in ('time', 'dmx') or rule['sequence_direction'] not in ('forward', 'both'):
            raise ValueError('Choose a valid sequence source and crossing direction.')
        steps = raw.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            raise ValueError('A sequence needs 1–64 steps.')
        rule['sequence_on_error'] = raw.get('sequence_on_error', 'stop')
        if rule['sequence_on_error'] not in ('stop', 'continue'):
            raise ValueError('Sequence error behavior must be stop or continue.')
        rule['steps'] = []
        duration = 0
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or step.get('action') not in ('osc', 'midi', 'script'):
                raise ValueError(f'Step {index}: select OSC, MIDI, or Script; sequences cannot be nested.')
            if step.get('preset_id'):
                raise ValueError(f'Step {index}: preset references are not supported inside steps. Use a sequence preset.')
            try:
                delay = integer(step.get('delay_ms', 0), 0, 600000, 'Delay (ms)')
                checked = validate_rule(step)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Step {index}: {exc}') from None
            fields = ACTION_FIELDS[checked['action']] + (('args',) if checked['action'] == 'osc' else ())
            rule['steps'].append({key: checked[key] for key in fields} |
                                 dict(name=checked['name'], action=checked['action'], delay_ms=delay))
            duration += delay
        if rule['sequence_source'] == 'dmx' and duration == 0:
            raise ValueError('A DMX cue sequence needs a nonzero timeline duration.')
        if duration > 600000:
            raise ValueError('A sequence timeline cannot exceed 10 minutes.')
    return rule


def validate_preset(raw):
    if not isinstance(raw, dict):
        raise ValueError('A device preset must be an object.')
    address = raw.get('address', '')
    if not isinstance(address, str):
        raise ValueError('Default OSC address must be text.')
    action = raw.get('action', 'osc')
    if action == 'fade' and not address:
        raise ValueError('A DMX fade preset needs an OSC address.')
    checked = validate_rule(dict(raw, address=address or '/go', args=[]))
    preset = {key: checked[key] for key in ACTION_FIELDS[action]}
    preset['name'] = checked['name']
    if action == 'osc':
        preset['address'] = address
    if 'action' in raw or action != 'osc':
        preset['action'] = action
    return preset


def condition_bounds(rule):
    level = rule['threshold']
    return {'gte': (level, 255), 'gt': (level + 1, 255), 'lte': (0, level),
            'lt': (0, level - 1), 'eq': (level, level),
            'range': (level, rule.get('threshold_max', 255))}[rule.get('operator', 'gte')]


def select_rule(rules, level):
    matches = [r for r in rules if not follows_dmx(r) and r['enabled'] and condition_bounds(r)[0] <= level <= condition_bounds(r)[1]]
    # Narrowest matching interval wins. Python's stable min preserves saved order for ties.
    return min(matches, key=lambda r: condition_bounds(r)[1] - condition_bounds(r)[0], default=None)

