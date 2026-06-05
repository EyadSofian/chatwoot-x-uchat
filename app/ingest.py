"""Parse uploaded CSV/XLSX into normalized contact rows.

Handles the UChat export schema (user_ns, phone, name, first_name,
last_interaction, ...) but is tolerant of column-name variations.
"""
import io
import os

import pandas as pd

# Optional filter: only migrate contacts whose last_interaction >= this ISO date.
# e.g. MIGRATE_SINCE=2025-11-01  (leave empty to migrate everyone)
MIGRATE_SINCE = os.environ.get("MIGRATE_SINCE", "").strip()
_SINCE = pd.to_datetime(MIGRATE_SINCE, utc=True, errors="coerce") if MIGRATE_SINCE else None

_BAD_PHONES = {"", "n.a", "na", "n/a", "none", "nan"}


def _norm_phone(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(" ", "")
    if s.lower() in _BAD_PHONES or len(s) < 7:
        return None
    return s if s.startswith("+") else f"+{s}"


def _pick(cols: dict, *names: str):
    """Return the actual column name matching any candidate (case-insensitive)."""
    for n in names:
        if n in cols:
            return cols[n]
    return None


def parse_file(filename: str, content: bytes) -> list[dict]:
    ext = filename.lower().rsplit(".", 1)[-1]
    buf = io.BytesIO(content)
    if ext in ("xlsx", "xls"):
        df = pd.read_excel(buf, dtype=str)
    else:
        df = pd.read_csv(buf, dtype=str, encoding="utf-8-sig")

    df.columns = [str(c).strip() for c in df.columns]
    lc = {c.lower(): c for c in df.columns}

    phone_col = _pick(lc, "phone", "phone_number", "msisdn", "facebook phone number")
    if not phone_col:
        raise ValueError("No phone column found in file.")
    ns_col = _pick(lc, "user_ns", "subscriber_id", "ns")
    name_col = _pick(lc, "name", "full_name")
    first_col = _pick(lc, "first_name", "firstname")
    li_col = _pick(lc, "last_interaction", "last_message_at", "last_seen")

    li_series = pd.to_datetime(df[li_col], utc=True, errors="coerce") if li_col else None

    rows: list[dict] = []
    seen: set[str] = set()  # in-file dedup (cross-file dedup is at the DB layer)

    for i, rec in df.iterrows():
        phone = _norm_phone(rec.get(phone_col))
        if not phone or phone in seen:
            continue
        seen.add(phone)

        name = (rec.get(name_col) if name_col else None) \
            or (rec.get(first_col) if first_col else None) \
            or "عميل مستورد"
        name = str(name).strip() or "عميل مستورد"

        li = li_series.iloc[i] if li_series is not None else None
        li_val = None if (li is None or pd.isna(li)) else li.to_pydatetime()

        status = "pending"
        if _SINCE is not None and (li is None or pd.isna(li) or li < _SINCE):
            status = "skipped"

        rows.append({
            "user_ns": (str(rec.get(ns_col)).strip() if ns_col and rec.get(ns_col) else None),
            "phone": phone,
            "name": name,
            "last_interaction": li_val,
            "status": status,
        })

    return rows


def parse_assignment_file(filename: str, content: bytes) -> list[dict]:
    """Parse phone -> salesperson assignment sheets.

    Expected columns are tolerant, but the common shape is:
    Contact Name | Phone | Salesperson

    If a sheet includes assignee_id / agent_id / chatwoot_agent_id, that ID wins
    over name matching in the Chatwoot worker.
    """
    ext = filename.lower().rsplit(".", 1)[-1]
    buf = io.BytesIO(content)
    if ext in ("xlsx", "xls"):
        df = pd.read_excel(buf, dtype=str)
    else:
        df = pd.read_csv(buf, dtype=str, encoding="utf-8-sig")

    df.columns = [str(c).strip() for c in df.columns]
    lc = {c.lower(): c for c in df.columns}

    phone_col = _pick(lc, "phone", "phone_number", "msisdn", "mobile", "facebook phone number")
    if not phone_col:
        raise ValueError("No phone column found in assignment file.")

    salesperson_col = _pick(
        lc,
        "salesperson",
        "sales person",
        "sales_person",
        "agent",
        "assignee",
        "assigned_to",
        "owner",
    )
    fallback_salesperson = filename.rsplit(".", 1)[0].replace(" Daata", "").replace(" Dataa", "").strip()
    if not salesperson_col and not fallback_salesperson:
        raise ValueError("No Salesperson/agent column found in assignment file.")

    assignee_col = _pick(lc, "assignee_id", "agent_id", "chatwoot_agent_id", "chatwoot_user_id")

    by_phone: dict[str, dict] = {}
    for _, rec in df.iterrows():
        phone = _norm_phone(rec.get(phone_col))
        if not phone:
            continue

        salesperson = (rec.get(salesperson_col) if salesperson_col else fallback_salesperson) or ""
        salesperson = str(salesperson).strip()
        if not salesperson:
            continue

        assignee_id = None
        if assignee_col and rec.get(assignee_col):
            try:
                assignee_id = int(float(str(rec.get(assignee_col)).strip()))
            except (TypeError, ValueError):
                assignee_id = None

        by_phone[phone] = {
            "phone": phone,
            "salesperson": salesperson,
            "assignee_id": assignee_id,
            "source_file": filename,
        }

    return list(by_phone.values())
