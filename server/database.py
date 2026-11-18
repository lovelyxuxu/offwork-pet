"""Versioned SQL storage. SQLite remains supported for existing installations/tests."""
from pathlib import Path
from contextlib import contextmanager
import re
import sqlite3

SCHEMA_VERSION = 3


@contextmanager
def transaction(db):
    if db.in_transaction:
        raise RuntimeError('Nested transactions are not supported')
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


class Record(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Postgres:
    def __init__(self, url):
        import psycopg
        def rows(cursor):
            names = [column.name for column in (cursor.description or [])]
            return lambda values: Record(zip(names, values))
        self.connection = psycopg.connect(url, autocommit=True, row_factory=rows)

    @property
    def in_transaction(self):
        return self.connection.info.transaction_status != 0

    def execute(self, sql, params=()):
        if sql.startswith('PRAGMA table_info('):
            table = sql.partition('(')[2].rstrip(')')
            return self.connection.execute('SELECT column_name AS name FROM information_schema.columns '
                                           'WHERE table_schema=current_schema() AND table_name=%s', (table,))
        if sql.startswith('BEGIN IMMEDIATE'):
            return self.connection.execute('BEGIN')
        sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql)
        return self.connection.execute(sql.replace('?', '%s'), params or None)

    def executescript(self, sql):
        for statement in sql.split(';'):
            if statement.strip() and not statement.strip().startswith('PRAGMA'):
                self.execute(statement)

    def close(self):
        self.connection.close()

    def executemany(self, sql, rows):
        with self.connection.cursor() as cursor:
            cursor.executemany(sql.replace('?', '%s'), rows)


def connect(path):
    if str(path).startswith(('postgresql://', 'postgres://')):
        return Postgres(str(path))
    if str(path) != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    return db


def migrate(db):
    from .legacy_schema import apply
    try:
        db.execute('BEGIN IMMEDIATE')
        if isinstance(db, Postgres):
            db.execute('SELECT pg_advisory_xact_lock(8131621)')
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)')
        versions = {row[0] for row in db.execute('SELECT version FROM schema_migrations')}
        if versions - set(range(1, SCHEMA_VERSION+1)):
            raise RuntimeError('Database is newer than this server; restore a compatible backup')
        if versions and versions != set(range(1, max(versions)+1)):
            raise RuntimeError('Database migration history is incomplete; restore a consistent backup')
        if 1 not in versions:
            # executescript commits on SQLite, so run each DDL in this transaction.
            class Schema:
                def execute(self, *args):
                    return db.execute(*args)
                def executescript(self, sql):
                    for statement in sql.split(';'):
                        if statement.strip() and not statement.strip().startswith('PRAGMA'):
                            db.execute(statement)
            apply(Schema())
            db.execute('INSERT INTO schema_migrations VALUES(1)')
        if 2 not in versions:
            statements = [
                'ALTER TABLE devices ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0',
                'CREATE TABLE server_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
                'CREATE TABLE access_invites(id TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, expires REAL NOT NULL, remaining INTEGER NOT NULL)',
                'CREATE TABLE chat_messages(id TEXT PRIMARY KEY, rally_id TEXT NOT NULL, sender TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL)',
                'CREATE INDEX chat_rally_created ON chat_messages(rally_id,created)',
                'CREATE TABLE admin_audit(id TEXT PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL, created REAL NOT NULL)',
            ]
            for sql in statements:
                db.execute(sql)
            db.execute('INSERT INTO schema_migrations VALUES(2)')
        if 3 not in versions:
            for sql in ('CREATE INDEX chat_created ON chat_messages(created)',
                        'CREATE INDEX access_expires ON access_invites(expires)',
                        'CREATE INDEX audit_created ON admin_audit(created)'):
                db.execute(sql)
            db.execute('INSERT INTO schema_migrations VALUES(3)')
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        db.close()
        raise
