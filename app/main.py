"""Web service: upload CSV/XLSX files and check progress.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

from . import db
from .ingest import parse_assignment_file, parse_file
from .ui import UPLOAD_PAGE

app = FastAPI(title="UChat → Chatwoot Relay")


@app.on_event("startup")
def _startup():
    db.init_pool()
    db.init_schema()


@app.get("/", response_class=HTMLResponse)
def home():
    """Browser upload UI — drag & drop files, no command line needed."""
    return UPLOAD_PAGE


@app.get("/health")
def health():
    return {"ok": True, "service": "uchat-chatwoot-relay"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Upload a .csv or .xlsx file")
    content = await file.read()
    try:
        rows = parse_file(file.filename, content)
    except Exception as e:
        raise HTTPException(400, f"Parse error: {e}")
    if not rows:
        raise HTTPException(400, "No valid rows (need a phone column).")

    job_id = db.create_job(file.filename)
    inserted, dup = db.bulk_insert_contacts(job_id, rows)
    skipped = sum(1 for r in rows if r["status"] == "skipped")
    return {
        "job_id": job_id,
        "file": file.filename,
        "rows_in_file": len(rows),
        "queued_new": inserted,
        "duplicates_ignored": dup,
        "filtered_out_by_date": skipped,
    }


@app.post("/upload-assignments")
async def upload_assignments(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Upload a .csv or .xlsx assignment file")
    content = await file.read()
    try:
        rows = parse_assignment_file(file.filename, content)
    except Exception as e:
        raise HTTPException(400, f"Parse error: {e}")
    if not rows:
        raise HTTPException(400, "No valid assignment rows (need phone + Salesperson).")

    upserted = db.bulk_upsert_assignments(rows)
    by_salesperson: dict[str, int] = {}
    direct_ids = 0
    for row in rows:
        by_salesperson[row["salesperson"]] = by_salesperson.get(row["salesperson"], 0) + 1
        direct_ids += 1 if row.get("assignee_id") else 0
    return {
        "file": file.filename,
        "rows_in_file": len(rows),
        "assignment_rules_upserted": upserted,
        "direct_assignee_ids": direct_ids,
        "by_salesperson": by_salesperson,
    }


@app.get("/status")
def status():
    return db.stats()
