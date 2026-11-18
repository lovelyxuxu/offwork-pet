"""Persistent identities, invitations and relationships on PostgreSQL or legacy SQLite."""
import hashlib
import json
import secrets
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
    def __init__(self, path, *, rally_max_members=100):
        if type(rally_max_members) is not int or not 1 <= rally_max_members <= 1000:
            raise ValueError('OFFWORK_RALLY_MAX_MEMBERS must be between 1 and 1000')
        self.rally_max_members = rally_max_members
        from .database import connect, migrate
        self.db = connect(path)
        migrate(self.db)

    def close(self):
        self.db.close()

    def update_device_info(self, device, info):
        self.db.execute('UPDATE devices SET device_info=?,info_reported_at=? WHERE id=?',
                        (json.dumps(info, ensure_ascii=False), time.time(), device))

    def admin_set_device_note(self, device, note):
        self.device(device)
        if not isinstance(note, str) or len(note) > 200 or any(ord(c) < 32 for c in note):
            raise DomainError('format', '备注须为不超过 200 字的单行文本')
        self.db.execute('UPDATE devices SET admin_note=? WHERE id=?', (note.strip(), device))

    @staticmethod
    def validate_client_type(value):
        if value not in ('mobile', 'desktop'):
            raise DomainError('format', '终端类型必须是 mobile 或 desktop')
        return value

    def client_type(self, device, value):
        self.db.execute('UPDATE devices SET client_type=? WHERE id=?', (self.validate_client_type(value), device))

    def register(self, client_type='desktop'):
        self.validate_client_type(client_type)
        device, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO devices(id,token_hash,client_type) VALUES(?,?,?)', (device, digest(token), client_type))
        return {'device_id': device, 'token': token}

    def authenticate(self, token):
        row = self.db.execute('SELECT id FROM devices WHERE token_hash=? AND disabled=0', (digest(token),)).fetchone()
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

    RALLY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

    def rally(self, device):
        row = self.db.execute(
            'SELECT r.* FROM rallies r JOIN rally_members m ON m.rally_id=r.id WHERE m.device_id=?', (device,)).fetchone()
        if not row:
            return None
        members = [dict(member) for member in self.db.execute(
            'SELECT device_id,name,joined FROM rally_members WHERE rally_id=? ORDER BY joined', (row['id'],))]
        settings = self.db.execute('SELECT member_limit FROM rally_settings WHERE rally_id=?', (row['id'],)).fetchone()
        limit = settings['member_limit'] if settings else 12
        return {'id': row['id'], 'code': row['code'], 'name': row['name'], 'owner': row['owner'], 'members': members,
                'member_limit': min(limit, self.rally_max_members)}

    def validate_rally_limit(self, limit):
        if type(limit) is not int or not 1 <= limit <= self.rally_max_members:
            raise DomainError('format', f'人数上限需要在 1–{self.rally_max_members} 人之间')
        return limit

    def rally_create(self, device, name, member_name='', now=None, *, member_limit=None):
        now = time.time() if now is None else now
        member_limit = self.validate_rally_limit(min(12, self.rally_max_members) if member_limit is None else member_limit)
        self.device(device)
        if self.rally(device):
            raise DomainError('joined', '请先离开当前集会')
        if not isinstance(member_name, str) or len(member_name) > 24:
            raise DomainError('format', '集会名字最多 24 字')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            while True:
                code = ''.join(secrets.choice(self.RALLY_ALPHABET) for _ in range(8))
                if not self.db.execute('SELECT 1 FROM rallies WHERE code=?', (code,)).fetchone():
                    break
            rally_id = uuid.uuid4().hex
            self.db.execute('INSERT INTO rallies VALUES(?,?,?,?,?)', (rally_id, code, name, device, now))
            self.db.execute('INSERT INTO rally_settings VALUES(?,?)', (rally_id, member_limit))
            self.db.execute('INSERT INTO rally_members VALUES(?,?,?,?)', (rally_id, device, member_name, now))
            self.db.execute('COMMIT')
            return self.rally(device)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def rally_join(self, device, code, name='', now=None):
        now = time.time() if now is None else now
        self.device(device)
        code = code.strip().upper() if isinstance(code, str) else ''
        if len(code) != 8 or any(c not in self.RALLY_ALPHABET for c in code):
            raise DomainError('format', '集会码格式不正确')
        if not isinstance(name, str) or len(name) > 24:
            raise DomainError('format', '集会名字最多 24 字')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM rallies WHERE code=?', (code,)).fetchone()
            if not row:
                raise DomainError('unavailable', '集会码不可用，请核对后重试')
            current = self.rally(device)
            if current and current['id'] == row['id']:
                self.db.execute('COMMIT')
                return current
            if current:
                raise DomainError('joined', '请先离开当前集会')
            count = self.db.execute('SELECT COUNT(*) FROM rally_members WHERE rally_id=?', (row['id'],)).fetchone()[0]
            settings = self.db.execute('SELECT member_limit FROM rally_settings WHERE rally_id=?', (row['id'],)).fetchone()
            limit = min(settings['member_limit'] if settings else 12, self.rally_max_members)
            if count >= limit:
                raise DomainError('full', f'集会人数已达到创建者设置的 {limit} 人上限')
            self.db.execute('INSERT INTO rally_members VALUES(?,?,?,?)', (row['id'], device, name, now))
            self.db.execute('COMMIT')
            return self.rally(device)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def rally_leave(self, device):
        current = self.rally(device)
        if not current:
            return None
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if current['owner'] == device:
                self.db.execute('DELETE FROM rally_settings WHERE rally_id=?', (current['id'],))
                self.db.execute('DELETE FROM rally_members WHERE rally_id=?', (current['id'],))
                self.db.execute('DELETE FROM rallies WHERE id=?', (current['id'],))
            else:
                self.db.execute('DELETE FROM rally_members WHERE device_id=?', (device,))
            self.db.execute('COMMIT')
            return current
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def rally_name(self, device, name):
        if not isinstance(name, str) or len(name) > 24:
            raise DomainError('format', '集会名字最多 24 字')
        self.db.execute('UPDATE rally_members SET name=? WHERE device_id=?', (name, device))

    def rally_set_limit(self, device, limit):
        self.validate_rally_limit(limit)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            current = self.rally(device)
            if not current or current['owner'] != device:
                raise DomainError('forbidden', '只有集会创建者可以调整人数上限')
            if limit < len(current['members']):
                raise DomainError('format', '人数上限不能小于当前成员数')
            self.db.execute('INSERT INTO rally_settings VALUES(?,?) ON CONFLICT(rally_id) DO UPDATE SET member_limit=excluded.member_limit', (current['id'], limit))
            self.db.execute('COMMIT')
            return self.rally(device)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def admin_counts(self):
        return {
            'devices': self.db.execute('SELECT COUNT(*) FROM devices').fetchone()[0],
            'relations': self.db.execute('SELECT COUNT(*) FROM relations').fetchone()[0],
            'rallies': self.db.execute('SELECT COUNT(*) FROM rallies').fetchone()[0],
        }

    def all_rallies(self):
        rallies = []
        for row in self.db.execute('SELECT * FROM rallies ORDER BY created'):
            members = [dict(member) for member in self.db.execute(
                'SELECT device_id,name,joined FROM rally_members WHERE rally_id=? ORDER BY joined', (row['id'],))]
            settings = self.db.execute('SELECT member_limit FROM rally_settings WHERE rally_id=?', (row['id'],)).fetchone()
            limit = settings['member_limit'] if settings else 12
            rallies.append({'id': row['id'], 'code': row['code'], 'name': row['name'], 'owner': row['owner'],
                            'created': row['created'], 'members': members,
                            'member_limit': min(limit, self.rally_max_members)})
        return rallies

    def admin_devices(self):
        return [dict(row) for row in self.db.execute(
            'SELECT id,character,relation_id,ended_id,client_type,device_info,admin_note,info_reported_at FROM devices ORDER BY id')]

    def rally_by_code(self, code):
        row = self.db.execute('SELECT * FROM rallies WHERE code=?', (code,)).fetchone()
        if not row:
            return None
        members = [dict(member) for member in self.db.execute(
            'SELECT device_id,name,joined FROM rally_members WHERE rally_id=? ORDER BY joined', (row['id'],))]
        settings = self.db.execute('SELECT member_limit FROM rally_settings WHERE rally_id=?', (row['id'],)).fetchone()
        limit = settings['member_limit'] if settings else 12
        return {'id': row['id'], 'code': row['code'], 'name': row['name'], 'owner': row['owner'],
                'created': row['created'], 'members': members, 'member_limit': min(limit, self.rally_max_members)}

    def admin_dissolve_rally(self, code):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT id FROM rallies WHERE code=?', (code,)).fetchone()
            if not row:
                raise DomainError('unavailable', '集会不存在')
            self.db.execute('DELETE FROM rally_settings WHERE rally_id=?', (row['id'],))
            self.db.execute('DELETE FROM rally_members WHERE rally_id=?', (row['id'],))
            self.db.execute('DELETE FROM rallies WHERE id=?', (row['id'],))
            self.db.execute('COMMIT')
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def admin_set_rally_limit(self, code, limit):
        self.validate_rally_limit(limit)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT id FROM rallies WHERE code=?', (code,)).fetchone()
            if not row:
                raise DomainError('unavailable', '集会不存在')
            count = self.db.execute('SELECT COUNT(*) FROM rally_members WHERE rally_id=?', (row['id'],)).fetchone()[0]
            if limit < count:
                raise DomainError('format', '人数上限不能小于当前成员数')
            self.db.execute('INSERT INTO rally_settings VALUES(?,?) ON CONFLICT(rally_id) DO UPDATE SET member_limit=excluded.member_limit', (row['id'], limit))
            self.db.execute('COMMIT')
            return self.rally_by_code(code)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
