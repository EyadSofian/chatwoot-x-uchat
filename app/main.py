"""Web service: upload CSV/XLSX files and check progress.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from fastapi import FastAPI, UploadFile, File, HTTPException

from . import db
from .ingest import parse_file

app = FastAPI(title="UChat → Chatwoot Relay")


@app.on_event("startup")
def _startup():
    db.init_pool()
    db.init_schema()


@app.get("/")
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


@app.get("/status")
def status():
    return db.stats()
