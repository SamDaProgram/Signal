"""Configuration validation and persistence, trigger and preset editing."""

import csv
import io
import json
import uuid

from .constants import ACTION_FIELDS, PROTOCOLS
from .storage import MAX_SHOW_BYTES, atomic_write
from .validation import follows_dmx, linked_rule, validate_input, validate_preset
from .values import integer


class ConfigurationMixin:
    """Configuration validation and persistence, trigger and preset editing. Uses the Bridge runtime and its shared lock."""

    @classmethod
    def validate_show(cls, saved):
        if not isinstance(saved, dict) or type(saved.get('version', 1)) is not int or saved.get('version', 1) != 1:
            raise ValueError('Expected a version 1 show file.')
        if not isinstance(saved.get('rules'), list) or len(saved['rules']) > 512:
            raise ValueError('Show file must contain a rules array with at most 512 triggers.')
        presets_raw = saved.get('presets', [])
        if not isinstance(presets_raw, list) or len(presets_raw) > 128:
            raise ValueError('Show file may contain at most 128 presets.')
        def records(raws, validator):
            result = []
            for raw in raws:
                checked = validator(raw)
                try:
                    checked['id'] = str(uuid.UUID(raw['id']))
                except (KeyError, AttributeError, ValueError, TypeError):
                    raise ValueError('Every trigger and preset needs a valid UUID.') from None
                if any(item['id'] == checked['id'] for item in result):
                    raise ValueError('Duplicate ID in show file.')
                result.append(checked)
            return result
        presets = records(presets_raw, validate_preset)
        rules = records(saved['rules'], lambda raw: linked_rule(raw, presets))
        labels = saved.get('group_labels', {})
        if not isinstance(labels, dict) or len(labels) > 4096:
            raise ValueError('Group labels must be an object with at most 4096 entries.')
        checked_labels = {}
        for key, label in labels.items():
            try:
                universe, channel = map(int, key.split(':'))
            except (ValueError, AttributeError):
                raise ValueError('Invalid group location.') from None
            checked_labels[cls.group_key(universe, channel)] = cls.validate_group_label(label)
        return dict(version=1, input=validate_input(saved.get('input', {'protocol': 'artnet'})),
                    rules=rules, presets=presets, group_labels=checked_labels)

    def document(self):
        with self.lock:
            return dict(version=1, input=self.input, rules=self.rules, presets=self.presets, group_labels=self.group_labels)

    def save(self, rules, input_settings=None, presets=None, group_labels=None):
        document = {'version': 1, 'input': input_settings or self.input, 'rules': rules,
                    'group_labels': self.group_labels if group_labels is None else group_labels,
                    'presets': self.presets if presets is None else presets}
        content = json.dumps(document, indent=2)
        if len(content.encode()) > MAX_SHOW_BYTES:
            raise ValueError('Show file exceeds 8 MB.')
        atomic_write(self.path, content)

    @staticmethod
    def group_key(universe, channel):
        return f"{integer(universe, 0, 63999, 'Universe / block')}:{integer(channel, 1, 512, 'Channel')}"

    @staticmethod
    def validate_group_label(label):
        if not isinstance(label, str) or len(label.strip()) > 100 or any(ord(c) < 32 for c in label):
            raise ValueError('Group label must be text of at most 100 characters without control characters.')
        return label.strip()

    def set_group_label(self, raw):
        if not isinstance(raw, dict):
            raise ValueError('A group must be an object.')
        key = self.group_key(raw.get('universe'), raw.get('channel'))
        label = self.validate_group_label(raw.get('label', ''))
        with self.lock:
            labels = dict(self.group_labels)
            if label:
                labels[key] = label
            else:
                labels.pop(key, None)
            if len(labels) > 4096:
                raise ValueError('Maximum 4096 group labels per show.')
            self.save(self.rules, group_labels=labels)
            self.group_labels = labels
        return {'label': label}

    def export_channels(self):
        with self.lock:
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(['Input protocol', 'Universe / block', 'Channel', 'DDP absolute channel',
                             'Group label', 'Trigger', 'Enabled', 'Condition', 'OSC host',
                             'OSC port', 'OSC address', 'OSC arguments (JSON)', 'Saved order',
                             'Action type', 'Action configuration (JSON)', 'Preset ID', 'Cooldown (ms)'])
            for order, rule in sorted(enumerate(self.rules, 1), key=lambda item: (item[1]['universe'], item[1]['channel'])):
                operator = rule['operator']
                condition = (f"{rule['threshold']}–{rule['threshold_max']} (inclusive)" if operator == 'range'
                             else f"{dict(gte='>=', gt='>', lte='<=', lt='<', eq='=')[operator]} {rule['threshold']}")
                if follows_dmx(rule):
                    condition = 'DMX 0–255 (continuous)'
                row = [PROTOCOLS[self.input['protocol']][0], rule['universe'], rule['channel'],
                       rule['universe'] * 512 + rule['channel'] if self.input['protocol'] == 'ddp' else '',
                       self.group_labels.get(self.group_key(rule['universe'], rule['channel']), ''),
                       rule['name'], 'Yes' if rule['enabled'] else 'No', condition, rule['host'], rule['port'],
                       rule['address'], json.dumps(rule['args'], ensure_ascii=False), order,
                       rule.get('action', 'osc'), json.dumps({key: rule[key] for key in ACTION_FIELDS[rule.get('action', 'osc')]}, ensure_ascii=False),
                       rule.get('preset_id', ''), rule.get('cooldown_ms', 0)]
                if rule.get('action', 'osc') != 'osc':
                    row[8:12] = ['', '', '', '']
                # Prevent spreadsheet applications from interpreting text as formulas.
                writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@'))
                                 else value for value in row])
            return output.getvalue().encode('utf-8-sig')

    def upsert_preset(self, raw, preset_id=None):
        preset = validate_preset(raw)
        with self.lock:
            if preset_id and not any(p['id'] == preset_id for p in self.presets):
                raise KeyError('Preset not found.')
            preset['id'] = preset_id or str(uuid.uuid4())
            presets = [preset if p['id'] == preset_id else p for p in self.presets]
            if not preset_id:
                presets.append(preset)
            if len(presets) > 128:
                raise ValueError('Maximum 128 device presets.')
            rules = [dict(linked_rule(r, presets), id=r['id']) if r.get('preset_id') == preset['id'] else r for r in self.rules]
            self.save(rules, presets=presets)
            self.presets, self.rules = presets, rules
            self.reset_followers({r['id'] for r in rules if r.get('preset_id') == preset['id']})
            return preset

    def delete_preset(self, preset_id):
        with self.lock:
            presets = [p for p in self.presets if p['id'] != preset_id]
            if len(presets) == len(self.presets):
                raise KeyError('Preset not found.')
            rules = [{k: v for k, v in r.items() if k != 'preset_id'} if r.get('preset_id') == preset_id else r for r in self.rules]
            self.save(rules, presets=presets)
            self.presets, self.rules = presets, rules

    def set_input(self, raw):
        settings = validate_input(raw)
        with self.lock:
            self.save(self.rules, settings)
            changed_input = settings['protocol'] != self.input['protocol'] or settings['sacn_interface'] != self.input['sacn_interface']
            if changed_input:
                # Do not interpret old protocol values as a fresh threshold crossing.
                self.universes.clear()
                self.active.clear()
                self.selected.clear()
                self.ddp_pending.clear()
                self.packets = 0
            self.input = settings
            if changed_input:
                self.reset_followers()
            return dict(settings)

    def upsert(self, raw, rule_id=None):
        with self.lock:
            rule = linked_rule(raw, self.presets)
            if rule_id and not any(r['id'] == rule_id for r in self.rules):
                raise KeyError('Trigger not found.')
            rule['id'] = rule_id or str(uuid.uuid4())
            rules = [rule if r['id'] == rule_id else r for r in self.rules]
            if not rule_id:
                rules.append(rule)
            if len(rules) > 512:
                raise ValueError('Maximum 512 triggers.')
            affected = {(r['universe'], r['channel']) for r in self.rules if r['id'] == rule_id}
            affected.add((rule['universe'], rule['channel']))
            self.save(rules)
            self.rules = rules
            if not rule['enabled']:
                self.cancel_sequences('Trigger disabled', rule['id'])
            self.reset_followers({rule['id']})
            self.baseline(affected)
        return rule

    def delete(self, rule_id):
        with self.lock:
            rules = [r for r in self.rules if r['id'] != rule_id]
            if len(rules) == len(self.rules):
                raise KeyError('Trigger not found.')
            affected = {(r['universe'], r['channel']) for r in self.rules if r['id'] == rule_id}
            self.save(rules)
            self.rules = rules
            self.cancel_sequences('Trigger removed', rule_id)
            self.active.pop(rule_id, None)
            self.reset_followers({rule_id})
            self.baseline(affected)

