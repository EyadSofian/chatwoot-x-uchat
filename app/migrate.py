"""UChat -> Chatwoot migration helpers.

Refactored from the original main.py. Tokens are ENV-ONLY (no hardcoded
defaults) so nothing sensitive ever lands in the repo.
"""
import os
import time
from datetime import datetime

import requests

UCHAT_API_TOKEN = os.environ["UCHAT_API_TOKEN"]
CHATWOOT_BASE_URL = os.environ["CHATWOOT_BASE_URL"].rstrip("/")
CHATWOOT_API_TOKEN = os.environ["CHATWOOT_API_TOKEN"]
ACCOUNT_ID = int(os.environ["ACCOUNT_ID"])
INBOX_ID = int(os.environ["INBOX_ID"])

# Pacing (seconds) — tune from env without redeploying code.
RATE_MSG_DELAY = float(os.environ.get("RATE_MSG_DELAY", "0.5"))

_CW_HEADERS = {"api_access_token": CHATWOOT_API_TOKEN, "Content-Type": "application/json"}


def fetch_uchat_messages(phone: str, user_ns: str, retries: int = 3) -> list:
    url = "https://www.uchat.com.au/api/subscriber/chat-messages"
    headers = {"Authorization": f"Bearer {UCHAT_API_TOKEN}", "Accept": "application/json"}
    params = {"user_ns": user_ns} if user_ns else {"user_id": phone.replace("+", "")}
    for _ in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            return r.json().get("data", []) if r.status_code == 200 else []
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(3)
        except Exception:
            return []
    return []


def get_or_create_contact(phone: str, name: str, retries: int = 3) -> int | None:
    safe_phone = phone if phone.startswith("+") else f"+{phone}"
    for attempt in range(retries):
        try:
            r = requests.get(
                f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/search",
                headers=_CW_HEADERS, params={"q": safe_phone}, timeout=30,
            )
            if r.status_code == 200 and r.json().get("payload"):
                return r.json()["payload"][0]["id"]
            r2 = requests.post(
                f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts",
                headers=_CW_HEADERS, json={"name": name, "phone_number": safe_phone}, timeout=30,
            )
            if r2.status_code == 200:
                return r2.json()["payload"]["contact"]["id"]
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(5)
        except Exception:
            return None
    return None  # signals "Chatwoot unreachable" -> caller requeues


def create_conversation(contact_id: int, retries: int = 3) -> int | None:
    payload = {
        "source_id": str(int(time.time() * 1000)),
        "inbox_id": INBOX_ID,
        "contact_id": contact_id,
        "status": "resolved",
    }
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations",
                headers=_CW_HEADERS, json=payload, timeout=30,
            )
            return r.json()["id"] if r.status_code == 200 else None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(5)
        except Exception:
            return None
    return None


def send_note(conv_id: int, content: str, retries: int = 3) -> bool:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    payload = {"content": content, "message_type": "outgoing", "private": True}
    for _ in range(retries):
        try:
            requests.post(url, headers=_CW_HEADERS, json=payload, timeout=30)
            return True
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(3)
        except Exception:
            return False
    return False


def migrate_user(phone: str, user_ns: str, name: str) -> tuple[str, int]:
    """Returns (status, messages_count).

    status: 'done' | 'empty' | 'failed'
      - 'failed' means Chatwoot was unreachable -> caller should requeue & wait.
    """
    messages = fetch_uchat_messages(phone, user_ns)
    if not messages:
        return "empty", 0

    messages.reverse()  # oldest first

    contact_id = get_or_create_contact(phone, name)
    if not contact_id:
        return "failed", 0

    conv_id = create_conversation(contact_id)
    if not conv_id:
        return "failed", 0

    count = 0
    for msg in messages:
        ts = msg.get("ts", 0)
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "وقت غير معروف"
        sender = "👤 [العميل]" if msg.get("type") == "in" else "🎧 [الموظف/البوت]"
        if msg.get("msg_type") in ["image", "file", "audio", "video"]:
            text = f"📎 مرفق:\n{msg.get('payload', {}).get('url', '')}"
        else:
            text = msg.get("content") or msg.get("payload", {}).get("text") or "رسالة غير مدعومة"
        if send_note(conv_id, f"📅 {t}\n{sender}:\n{text}"):
            count += 1
        time.sleep(RATE_MSG_DELAY)

    return "done", count
