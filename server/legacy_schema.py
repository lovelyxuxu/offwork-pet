"""Version 1: adopt existing relay databases without replacing records."""

def apply(db):
    db.executescript('''
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
        CREATE TABLE IF NOT EXISTS rallies (
            id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            owner TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rally_members (
            rally_id TEXT NOT NULL, device_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '', joined REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rally_settings (
            rally_id TEXT PRIMARY KEY, member_limit INTEGER NOT NULL
        );
    ''')
    if 'client_type' not in {row['name'] for row in db.execute('PRAGMA table_info(devices)')}:
        db.execute("ALTER TABLE devices ADD COLUMN client_type TEXT NOT NULL DEFAULT 'desktop'")
    columns = {row['name'] for row in db.execute('PRAGMA table_info(devices)')}
    for column, definition in (('device_info', "TEXT NOT NULL DEFAULT '{}'"),
                               ('admin_note', "TEXT NOT NULL DEFAULT ''"),
                               ('info_reported_at', 'REAL NOT NULL DEFAULT 0')):
        if column not in columns:
            db.execute(f'ALTER TABLE devices ADD COLUMN {column} {definition}')
