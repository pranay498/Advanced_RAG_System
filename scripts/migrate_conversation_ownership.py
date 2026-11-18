"""Safe database migration for conversation ownership.

Applies infra/migrations/001_conversation_ownership.sql to introduce the
conversations table, safely backfill legacy conversation rows from chat_messages,
and establish the foreign-key constraint.

Usage (from project root):
    python scripts/migrate_conversation_ownership.py
"""
from pathlib import Path
import sys
import os
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
MIGRATION_FILE = ROOT / 'infra' / 'migrations' / '001_conversation_ownership.sql'


def main():
    url = os.getenv('DATABASE_URL', '').strip()
    if not url or 'YOUR_' in url or 'USERNAME:' in url:
        print('ERROR: Set a real DATABASE_URL in the project .env file.')
        return 1

    if not MIGRATION_FILE.is_file():
        print(f'ERROR: Migration file not found: {MIGRATION_FILE}')
        return 1

    print('Connecting to database for conversation ownership migration...')
    try:
        with psycopg.connect(url, connect_timeout=15) as conn:
            with conn.transaction():
                migration_sql = MIGRATION_FILE.read_text(encoding='utf-8')
                conn.execute(migration_sql)

            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM conversations')
                conv_count = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM chat_messages')
                msg_count = cur.fetchone()[0]

        print(f'SUCCESS: Migration complete. Total conversations: {conv_count}, total chat messages: {msg_count}.')
        return 0
    except Exception as exc:
        print(f'ERROR: Migration failed: {type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
