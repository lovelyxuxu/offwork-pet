"""Operator-only publication and legacy import. Run inside the service container."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from .store import Store
from .database import transaction


def import_sqlite(source, target):
    with TemporaryDirectory() as temp:
        copied = Path(temp)/'source.sqlite3'
        # Snapshot without modifying the source, including its WAL.
        with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(copied)) as snapshot:
                original.backup(snapshot)
        old = Store(copied)
        tables = ('devices', 'relations', 'invitations', 'rallies', 'rally_members', 'rally_settings',
                  'access_invites', 'chat_messages', 'admin_audit')
        try:
            with transaction(target.db):
                if any(target.db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in tables):
                    raise ValueError('目标实例必须为空，禁止覆盖现有业务数据')
                for table in tables:
                    cursor = old.db.execute(f'SELECT * FROM {table}')
                    columns = [column[0] for column in cursor.description]
                    expected = {row['name'] for row in target.db.execute(f'PRAGMA table_info({table})')}
                    if set(columns) != expected:
                        raise ValueError(f'{table}: 源库字段与目标版本不兼容')
                    names = ','.join('"'+column.replace('"', '""')+'"' for column in columns)
                    sql = f'INSERT INTO {table}({names}) VALUES({",".join("?" for _ in columns)})'
                    while batch := cursor.fetchmany(500):
                        target.db.executemany(sql, [tuple(row) for row in batch])
        finally:
            old.close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('import-pack').add_argument('archive', type=Path)
    sub.add_parser('import-sqlite').add_argument('source', type=Path)
    args = parser.parse_args()
    if args.command == 'import-pack':
        from .resources import publish
        print(publish(args.archive)['id'])
    else:
        store = Store(os.environ['OFFWORK_DATABASE_URL'])
        try:
            import_sqlite(args.source, store)
        finally:
            store.close()
        print('Imported legacy relay records; source unchanged')


if __name__ == '__main__':
    main()
