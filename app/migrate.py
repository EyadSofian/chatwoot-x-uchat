"""UChat -> Chatwoot migration helpers.

Refactored from the original main.py. Tokens are ENV-ONLY (no hardcoded
defaults) so nothing sensitive ever lands in the repo.
"""
import os
import json
import re
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
CHATWOOT_AGENT_MAP_RAW = os.environ.get("CHATWOOT_AGENT_MAP", "").strip()
UCHAT_INCLUDE_BOT = int(os.environ.get("UCHAT_INCLUDE_BOT", "1"))
UCHAT_INCLUDE_NOTE = int(os.environ.get("UCHAT_INCLUDE_NOTE", "1"))
UCHAT_INCLUDE_SYSTEM = int(os.environ.get("UCHAT_INCLUDE_SYSTEM", "0"))
UCHAT_MSG_LIMIT = int(os.environ.get("UCHAT_MSG_LIMIT", "100"))

_CW_HEADERS = {"api_access_token": CHATWOOT_API_TOKEN, "Content-Type": "application/json"}
_AGENTS_CACHE: list[dict] | None = None


def _norm_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^\w@.+-]+", " ", str(value).lower())).strip()


def _compact_name(value: str | None) -> str:
    return _norm_name(value).replace(" ", "").replace("-", "")


def _load_agent_map() -> dict[str, int]:
    """Optional override: CHATWOOT_AGENT_MAP='{"Salesperson Name": 12}'."""
    if not CHATWOOT_AGENT_MAP_RAW:
        return {}
    try:
        parsed = json.loads(CHATWOOT_AGENT_MAP_RAW)
    except json.JSONDecodeError:
        parsed = {}
        for part in CHATWOOT_AGENT_MAP_RAW.split(","):
            if "=" not in part:
                continue
            name, agent_id = part.split("=", 1)
            try:
                parsed[name.strip()] = int(agent_id.strip())
            except ValueError:
                continue
    if not isinstance(parsed, dict):
        parsed = {}
    agent_map: dict[str, int] = {}
    for name, agent_id in parsed.items():
        try:
            agent_map[_norm_name(name)] = int(agent_id)
        except (TypeError, ValueError):
            continue
    return agent_map


_STATIC_AGENT_MAP = _load_agent_map()


class UChatFetchError(RuntimeError):
    """UChat returned an API error, not a genuinely empty conversation."""


def fetch_uchat_messages(phone: str, user_ns: str, retries: int = 3) -> list:
    url = "https://www.uchat.com.au/api/subscriber/chat-messages"
    headers = {"Authorization": f"Bearer {UCHAT_API_TOKEN}", "Accept": "application/json"}
    base_params = {
        "include_bot": UCHAT_INCLUDE_BOT,
        "include_note": UCHAT_INCLUDE_NOTE,
        "include_system": UCHAT_INCLUDE_SYSTEM,
        "limit": UCHAT_MSG_LIMIT,
    }
    lookup_params = []
    if user_ns:
        lookup_params.append({"user_ns": user_ns})
    lookup_params.append({"user_id": phone.replace("+", "")})

    last_error = None
    saw_empty_ok = False
    for lookup in lookup_params:
        params = {**base_params, **lookup}
        for _ in range(retries):
            try:
                r = requests.get(url, headers=headers, params=params, timeout=30)
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    if isinstance(data, dict):
                        data = data.get("messages") or data.get("items") or data.get("data") or []
                    if data:
                        return data
                    last_error = f"empty via {lookup}"
                    saw_empty_ok = True
                    break
                last_error = f"http_{r.status_code}: {r.text[:180]}"
                if r.status_code in (400, 401, 403, 404):
                    break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_error = f"network: {e}"
                time.sleep(3)
            except Exception as e:
                last_error = f"unexpected: {e}"
                break
    if last_error:
        print(f"UChat returned no messages for {phone}: {last_error}", flush=True)
    if last_error and not last_error.startswith("empty via") and not saw_empty_ok:
        raise UChatFetchError(last_error)
    return []


def fetch_chatwoot_agents(retries: int = 3) -> list[dict]:
    global _AGENTS_CACHE
    if _AGENTS_CACHE is not None:
        return _AGENTS_CACHE
    for _ in range(retries):
        try:
            r = requests.get(
                f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/agents",
                headers=_CW_HEADERS,
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    data = data.get("payload") or data.get("data") or []
                _AGENTS_CACHE = data if isinstance(data, list) else []
                return _AGENTS_CACHE
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(3)
        except Exception:
            break
    _AGENTS_CACHE = []
    return _AGENTS_CACHE


def resolve_assignee_id(salesperson: str | None, explicit_assignee_id: int | None = None) -> int | None:
    if explicit_assignee_id:
        return int(explicit_assignee_id)
    if not salesperson:
        return None

    normalized = _norm_name(salesperson)
    compact = _compact_name(salesperson)
    if normalized in _STATIC_AGENT_MAP:
        return _STATIC_AGENT_MAP[normalized]

    for agent in fetch_chatwoot_agents():
        candidates = [
            agent.get("name"),
            agent.get("available_name"),
            agent.get("email"),
        ]
        for candidate in candidates:
            if _norm_name(candidate) == normalized or _compact_name(candidate) == compact:
                return int(agent["id"])
    return None


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


def set_conversation_assignee(conv_id: int, assignee_id: int | None, retries: int = 3) -> bool:
    assignment_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/assignments"
    payload = {"assignee_id": assignee_id}
    for _ in range(retries):
        try:
            r = requests.post(assignment_url, headers=_CW_HEADERS, json=payload, timeout=30)
            if r.status_code in (200, 204):
                return True
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(3)
        except Exception:
            return False
    if assignee_id is None:
        try:
            patch_url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}"
            r = requests.patch(patch_url, headers=_CW_HEADERS, json=payload, timeout=30)
            return r.status_code in (200, 204)
        except Exception:
            return False
    return False


def send_note(conv_id: int, content: str, retries: int = 3) -> bool:
    url = f"{CHATWOOT_BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/conversations/{conv_id}/messages"
    payload = {"content": content, "message_type": "outgoing", "private": True}
    for _ in range(retries):
        try:
            r = requests.post(url, headers=_CW_HEADERS, json=payload, timeout=30)
            return r.status_code in (200, 201)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            time.sleep(3)
        except Exception:
            return False
    return False


def apply_assignment_policy(
    conv_id: int,
    salesperson: str | None = None,
    assignee_id: int | None = None,
) -> str | None:
    """Assign matched contacts; otherwise keep imported conversations unassigned."""
    resolved_assignee_id = resolve_assignee_id(salesperson, assignee_id)
    if resolved_assignee_id:
        ok = set_conversation_assignee(conv_id, resolved_assignee_id)
        return None if ok else f"assignment_failed:{salesperson or resolved_assignee_id}"

    ok = set_conversation_assignee(conv_id, None)
    if salesperson:
        return f"agent_not_found:{salesperson}" if ok else f"unassign_failed_agent_not_found:{salesperson}"
    return None if ok else "unassign_failed"


def migrate_user(
    phone: str,
    user_ns: str,
    name: str,
    assignment_salesperson: str | None = None,
    assignment_assignee_id: int | None = None,
) -> tuple[str, int, str | None]:
    """Returns (status, messages_count, assignment_error).

    status: 'done' | 'empty' | 'failed'
      - 'failed' means Chatwoot was unreachable -> caller should requeue & wait.
    """
    messages = fetch_uchat_messages(phone, user_ns)
    if not messages:
        return "empty", 0, None

    messages.reverse()  # oldest first

    contact_id = get_or_create_contact(phone, name)
    if not contact_id:
        return "failed", 0, None

    conv_id = create_conversation(contact_id)
    if not conv_id:
        return "failed", 0, None

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

    assignment_error = apply_assignment_policy(
        conv_id,
        salesperson=assignment_salesperson,
        assignee_id=assignment_assignee_id,
    )
    return "done", count, assignment_error
