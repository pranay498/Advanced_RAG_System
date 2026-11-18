import json
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from app.config import settings

@contextmanager
def connection():
    # Render provides a PostgreSQL connection URL. Prefer its external URL
    # for a FastAPI process running on your computer.
    url = settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is missing from .env")
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as conn:
        yield conn

def audit(conn, actor, tenant, action, detail):
    conn.execute('INSERT INTO audit_events (actor,tenant_id,action,detail) VALUES (%s,%s,%s,%s)',
                 (actor, tenant, action, json.dumps(detail)))

def get_chunks_by_ids(tenant: str, chunk_ids: list[str]):
    if not chunk_ids:
        return []

    with connection() as conn:
        rows = conn.execute("""
            SELECT chunk_id,document_id,version,chunk_index,
                   title,page,text,
                   owner_sub,allowed_groups
            FROM document_chunks
            WHERE tenant_id = %s
              AND chunk_id = ANY(%s)
        """, (tenant, chunk_ids)).fetchall()

    return [dict(r) for r in rows]