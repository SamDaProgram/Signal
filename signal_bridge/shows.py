"""Named show files, import/export and active show switching."""

import json
import re
from pathlib import Path

from .bridge import Bridge
from .storage import MAX_SHOW_BYTES, atomic_write


class ShowFiles:
    """Named, versioned JSON shows. Only basenames inside the configured directory."""
    MAX_BYTES = MAX_SHOW_BYTES

    def __init__(self, directory, legacy=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.marker = self.directory / '.active-show'
        name = self.marker.read_text().strip() if self.marker.exists() else 'default.json'
        path = self.file(name)
        if not path.exists() and not self.marker.exists() and legacy and Path(legacy).is_file():
            data = self.read(Path(legacy))
            self.write(path, data)
        if path.exists():
            self.read(path)  # Size and schema validation before loading.
        self.bridge = Bridge(path)  # Creates a blank show if missing.
        self.bridge.shows = self
        self.remember(path.name)

    def file(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,95}\.json', name):
            raise ValueError('Show filename must end in .json and use letters, numbers, spaces, dots, underscores or hyphens (maximum 101 characters).')
        path = self.directory / name
        if path.is_symlink() or path.resolve().parent != self.directory:
            raise ValueError('Show file must be inside the show directory and cannot be a symlink.')
        return path

    atomic = staticmethod(atomic_write)

    def remember(self, name):
        self.atomic(self.marker, name)

    def write(self, path, data):
        content = json.dumps(data, indent=2)
        if len(content.encode()) > self.MAX_BYTES:
            raise ValueError('Show file exceeds 8 MB.')
        self.atomic(path, content)

    def read(self, path):
        if path.stat().st_size > self.MAX_BYTES:
            raise ValueError('Show file exceeds 8 MB.')
        return Bridge.validate_show(json.loads(path.read_text()))

    def info(self):
        return dict(current=self.bridge.path.name, directory=str(self.directory),
                    files=sorted(p.name for p in self.directory.glob('*.json') if p.is_file() and not p.is_symlink()))

    def change(self, raw):
        if not isinstance(raw, dict):
            raise ValueError('Expected a show operation.')
        operation = raw.get('operation')
        if operation not in ('save', 'save_as', 'new', 'load', 'upload'):
            raise ValueError('Unknown show operation.')
        bridge = self.bridge
        with bridge.lock:
            if operation == 'save':
                bridge.save(bridge.rules)
                return self.info()
            path = self.file(raw.get('name'))
            if operation in ('new', 'upload', 'save_as') and path.exists():
                raise ValueError('That show already exists. Choose a new filename; existing shows are never overwritten by imports or copies.')
            if operation != 'save_as' and bridge.script_processes:
                raise ValueError('Wait for running scripts to finish before changing shows.')
            if operation == 'load':
                if not path.is_file():
                    raise ValueError('Show file no longer exists. Refresh the list or create a new show.')
                data = self.read(path)
            elif operation == 'upload':
                data = Bridge.validate_show(raw.get('show'))
            elif operation == 'new':
                data = Bridge.validate_show({'rules': []})
            else:
                data = bridge.document()
            if operation != 'load':
                self.write(path, data)
            self.remember(path.name)
            bridge.path = path
            bridge.input, bridge.rules, bridge.presets, bridge.group_labels = (data[k] for k in ('input', 'rules', 'presets', 'group_labels'))
            if operation != 'save_as':
                bridge.cancel_sequences('Show changed')
                bridge.reset_followers()
                bridge.sequence_runs.clear()
                bridge.armed = bridge.scripts_enabled = False
                bridge.universes.clear()
                bridge.active.clear()
                bridge.selected.clear()
                bridge.ddp_pending.clear()
                bridge.last_sent.clear()
                bridge.events.clear()
                bridge.packets = bridge.sent = 0
            return self.info()

