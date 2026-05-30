"""Postgres layer: connection pool, schema bootstrap, and queue queries.

Progress lives entirely in Postgres so the migration is resume-safe across
Railway redeploys / restarts (Railway's container FS is ephemeral).
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

DATABASE_URL = os.environ["DATABASE_URL"]  # Railway injects this automatically

_pool: ThreadedConnectionPool | None = None


def init_pool(minconn: int = 1, maxconn: int = 10) -> None:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(minconn, maxconn, dsn=DATABASE_URL)


@contextmanager
def get_conn():
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          SERIAL PRIMARY KEY,
    filename    TEXT,
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    total       INT DEFAULT 0,
    inserted    INT DEFAULT 0,
    skipped     INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contacts (
    id               SERIAL PRIMARY KEY,
    job_id           INT REFERENCES jobs(id),
    user_ns          TEXT,
    phone            TEXT UNIQUE,          -- dedup across ALL uploads happens here
    name             TEXT,
    last_interaction TIMESTAMPTZ,
    status           TEXT DEFAULT 'pending',  -- pending|processing|done|failed|skipped|empty
    messages_count   INT DEFAULT 0,
    error            TEXT,
    processed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
"""


def init_schema() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)


def create_job(filename: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO jobs (filename) VALUES (%s) RETURNING id", (filename,))
        return cur.fetchone()[0]


def bulk_insert_contacts(job_id: int, rows: list[dict]) -> tuple[int, int]:
    """Insert contacts, deduping by phone. Returns (inserted, skipped_existing)."""
    inserted = 0
    with get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO contacts (job_id, user_ns, phone, name, last_interaction, status)
                VALUES (%(job_id)s, %(user_ns)s, %(phone)s, %(name)s, %(last_interaction)s, %(status)s)
                ON CONFLICT (phone) DO NOTHING
                """,
                {**r, "job_id": job_id},
            )
            inserted += cur.rowcount
        cur.execute(
            "UPDATE jobs SET total=%s, inserted=%s, skipped=%s WHERE id=%s",
            (len(rows), inserted, len(rows) - inserted, job_id),
        )
    return inserted, len(rows) - inserted


def claim_next_contact() -> dict | None:
    """Atomically grab the next pending contact (safe for multiple workers)."""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE contacts SET status='processing'
            WHERE id = (
                SELECT id FROM contacts
                WHERE status='pending'
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, user_ns, phone, name
            """
        )
        return cur.fetchone()


def finish_contact(contact_id: int, status: str, count: int = 0, error: str | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contacts
            SET status=%s, messages_count=%s, error=%s, processed_at=now()
            WHERE id=%s
            """,
            (status, count, error, contact_id),
        )


def requeue_contact(contact_id: int, error: str | None = None) -> None:
    """Put a contact back to pending (e.g. Chatwoot was temporarily down)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE contacts SET status='pending', error=%s WHERE id=%s", (error, contact_id))


def stats() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) FROM contacts GROUP BY status")
        by_status = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("SELECT COALESCE(SUM(messages_count),0) FROM contacts WHERE status='done'")
        total_msgs = cur.fetchone()[0]
    return {"by_status": by_status, "messages_injected": total_msgs}
