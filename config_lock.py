"""Persistent configuration PIN with expiring, in-memory editing sessions."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time


class ConfigLock:
    def __init__(self, path):
        self.path = Path(path)
        self.record = json.loads(self.path.read_text()) if self.path.exists() else None
        self.sessions = {}
        self.failures = 0
        self.retry_at = 0

    def authorized(self, token):
        return not self.record or self.sessions.get(token, 0) > time.monotonic()

    def status(self, token=''):
        return {'configured': bool(self.record), 'unlocked': self.authorized(token)}

    def setup(self, pin):
        if self.record:
            raise ValueError('A PIN is already configured.')
        if not isinstance(pin, str) or not re.fullmatch(r'[0-9]{4,12}', pin):
            raise ValueError('Use a PIN of 4–12 digits.')
        salt = secrets.token_hex(16)
        record = {'salt': salt, 'hash': self.digest(pin, salt)}
        temporary = self.path.with_suffix('.tmp')
        try:
            with open(temporary, 'w', encoding='utf-8') as stream:
                os.chmod(temporary, 0o600)
                json.dump(record, stream)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.record = record
        return self.unlock(pin)

    @staticmethod
    def digest(pin, salt):
        return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 200000).hex()

    def unlock(self, pin):
        if time.monotonic() < self.retry_at:
            raise ValueError('Too many attempts. Wait 30 seconds and try again.')
        if not self.record or not isinstance(pin, str) or len(pin) > 12 or not hmac.compare_digest(self.digest(pin, self.record['salt']), self.record['hash']):
            self.failures += 1
            if self.failures >= 5:
                self.retry_at = time.monotonic() + 30
            raise ValueError('Incorrect PIN.')
        self.failures = 0
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        self.sessions[token] = now + 8 * 60 * 60
        return {**self.status(token), 'token': token}

    def lock(self):
        self.sessions.clear()
        return self.status()
