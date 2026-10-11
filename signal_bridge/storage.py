"""Atomic on-disk writes and show size limits."""

import os
import tempfile


MAX_SHOW_BYTES = 8 * 1024 * 1024

def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.signal-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
