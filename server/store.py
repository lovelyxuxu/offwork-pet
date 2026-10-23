"""SQLite identity, invitation and atomic one-to-one relationship storage."""
import hashlib
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
import re


class DomainError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                character TEXT NOT NULL DEFAULT 'lulu', relation_id TEXT,
                ended_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invitations (
                code_hash TEXT PRIMARY KEY, owner TEXT NOT NULL UNIQUE,
                expires REAL NOT NULL, redeemed_by TEXT, relation_id TEXT
            );
        ''')

    def close(self):
        self.db.close()

    def register(self):
        device, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO devices(id,token_hash) VALUES(?,?)', (device, digest(token)))
        return {'device_id': device, 'token': token}

    def authenticate(self, token):
        row = self.db.execute('SELECT id FROM devices WHERE token_hash=?', (digest(token),)).fetchone()
        return row['id'] if row else None

    def device(self, device):
        row = self.db.execute('SELECT * FROM devices WHERE id=?', (device,)).fetchone()
        if not row:
            raise DomainError('identity', '设备身份失效，请在解除旧关系后重置连接身份')
        return dict(row)

    def relation(self, device):
        owner = self.device(device)
        if not owner['relation_id']:
            return None
        row = self.db.execute('SELECT * FROM relations WHERE id=?', (owner['relation_id'],)).fetchone()
        if not row:
            return None
        partner = row['b'] if row['a'] == device else row['a']
        return {'id': row['id'], 'partner_id': partner, 'character': self.device(partner)['character']}

    def character(self, device, character):
        if not isinstance(character, str) or character and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', character):
            raise DomainError('character', '不支持的形象')
        self.db.execute('UPDATE devices SET character=? WHERE id=?', (character, device))

    def invite(self, device, now=None):
        now = time.time() if now is None else now
        if self.relation(device):
            raise DomainError('paired', '请先解除当前搭子连接')
        self.db.execute('DELETE FROM invitations WHERE expires < ?', (now - 600,))
        self.db.execute('DELETE FROM invitations WHERE owner=?', (device,))
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        while True:
            code = ''.join(secrets.choice(alphabet) for _ in range(8))
            if not self.db.execute('SELECT 1 FROM invitations WHERE code_hash=?', (digest(code),)).fetchone():
                break
        self.db.execute('INSERT INTO invitations(code_hash,owner,expires) VALUES(?,?,?)', (digest(code), device, now + 600))
        return {'code': code, 'expires_at': now + 600}

    def revoke(self, device):
        self.db.execute('DELETE FROM invitations WHERE owner=?', (device,))

    def redeem(self, device, code, now=None):
        now = time.time() if now is None else now
        code = code.strip().upper()
        if len(code) != 8 or any(c not in 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789' for c in code):
            raise DomainError('format', '邀请码格式不正确')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            invitation = self.db.execute('SELECT * FROM invitations WHERE code_hash=?', (digest(code),)).fetchone()
            if not invitation:
                raise DomainError('unavailable', '邀请码不可用，请联系对方重新生成')
            # A retry from the original redeemer returns the original result only
            # while that exact relationship is still current.
            if invitation['redeemed_by'] == device and invitation['relation_id'] == self.device(device)['relation_id'] and invitation['relation_id']:
                self.db.execute('COMMIT')
                return self.relation(device)
            if invitation['expires'] <= now:
                raise DomainError('expired', '邀请码已过期，请重新获取')
            if invitation['owner'] == device:
                raise DomainError('self', '不能连接自己')
            if self.relation(device):
                raise DomainError('paired', '请先解除当前搭子连接')
            owner = invitation['owner']
            if invitation['redeemed_by'] or self.relation(owner):
                raise DomainError('unavailable', '邀请码不可用，请联系对方重新生成')
            relation = uuid.uuid4().hex
            self.db.execute('INSERT INTO relations VALUES(?,?,?)', (relation, owner, device))
            self.db.execute('UPDATE devices SET relation_id=?,ended_id=? WHERE id IN (?,?)', (relation, '', owner, device))
            self.db.execute('UPDATE invitations SET redeemed_by=?,relation_id=? WHERE code_hash=?', (device, relation, digest(code)))
            self.db.execute('DELETE FROM invitations WHERE owner=?', (device,))
            self.db.execute('COMMIT')
            return self.relation(device)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def unlink(self, device, expected):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            relation = self.relation(device)
            if relation and relation['id'] == expected:
                self.db.execute('UPDATE devices SET relation_id=NULL,ended_id=? WHERE relation_id=?', (expected, expected))
                self.db.execute('DELETE FROM relations WHERE id=?', (expected,))
                self.db.execute('DELETE FROM invitations WHERE owner IN (?,?)', (device, relation['partner_id']))
            self.db.execute('COMMIT')
            return relation['partner_id'] if relation and relation['id'] == expected else None
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
