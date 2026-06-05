"""Background worker: drains the contacts queue one by one.

Run as a SEPARATE Railway service with start command:
    python -m app.worker

It shares the same Postgres (DATABASE_URL) and UChat/Chatwoot env vars as the
web service. Safe to run multiple replicas — claim_next_contact() uses
FOR UPDATE SKIP LOCKED so no contact is processed twice.
"""
import os
import time

from . import db
from .migrate import UChatFetchError, migrate_user

RATE_USER_DELAY = float(os.environ.get("RATE_USER_DELAY", "2"))      # between users
IDLE_POLL_DELAY = float(os.environ.get("IDLE_POLL_DELAY", "15"))     # when queue empty
DOWN_BACKOFF = float(os.environ.get("DOWN_BACKOFF", "30"))           # Chatwoot unreachable
BREAK_EVERY = int(os.environ.get("BREAK_EVERY", "500"))             # long pause cadence
BREAK_SECONDS = float(os.environ.get("BREAK_SECONDS", "60"))


def run():
    db.init_pool()
    db.init_schema()
    print("👷 Worker started. Polling queue...", flush=True)
    processed = 0

    while True:
        contact = db.claim_next_contact()
        if not contact:
            time.sleep(IDLE_POLL_DELAY)
            continue

        cid, phone = contact["id"], contact["phone"]
        try:
            status, count, assignment_error = migrate_user(
                phone,
                contact["user_ns"],
                contact["name"],
                contact.get("assignment_salesperson"),
                contact.get("assignment_assignee_id"),
            )
        except UChatFetchError as e:
            db.finish_contact(cid, "failed", 0, error=f"uchat_api_error: {e}")
            print(f"🛑 {phone} UChat API error, stopped: {e}", flush=True)
            time.sleep(DOWN_BACKOFF)
            continue
        except Exception as e:
            db.requeue_contact(cid, error=f"unexpected: {e}")
            print(f"⚠️  {phone} unexpected error, requeued: {e}", flush=True)
            time.sleep(DOWN_BACKOFF)
            continue

        if status == "failed":
            # Chatwoot was unreachable — put it back and back off so we don't burn the queue.
            db.requeue_contact(cid, error="chatwoot_unreachable")
            print(f"🛑 {phone} Chatwoot unreachable, requeued. Sleeping {DOWN_BACKOFF}s", flush=True)
            time.sleep(DOWN_BACKOFF)
            continue

        db.finish_contact(cid, status, count, error=assignment_error)
        processed += 1
        note = f" / {assignment_error}" if assignment_error else ""
        print(f"✅ [{processed}] {phone} -> {status} ({count} msgs){note}", flush=True)

        if processed % BREAK_EVERY == 0:
            print(f"🧘 Break {BREAK_SECONDS}s to protect the APIs...", flush=True)
            time.sleep(BREAK_SECONDS)
        else:
            time.sleep(RATE_USER_DELAY)


if __name__ == "__main__":
    run()
