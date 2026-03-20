import os
import re
import subprocess
import threading
import time
import uuid
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sqlalchemy import text, func

from src.database.connection import get_db
from src.config import Config, ENV_PATH, describe_database_url
from src.database.models import (
    Issuer,
    JobRun,
    Organization,
    ScheduledJob,
    SearchQuery,
    Tender,
    TenderParticipant,
    TenderDocument,
)
from src.ingestion.pipeline import IngestionPipeline
from src.search.hybrid import HybridSearch
from src.scheduler.job_runner import acquire_job_run, start_job_run_async

logger = logging.getLogger(__name__)

print(
    "Web server startup: ENV_PATH=%s DATABASE_URL=%s (%s)"
    % (ENV_PATH, Config.DATABASE_URL, describe_database_url(Config.DATABASE_URL)),
    flush=True,
)

BASE_DIR = Path(__file__).resolve().parents[2]  # tender/
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python"

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


class JobState:
    def __init__(self, job_id: str, action: str, payload: Dict[str, Any]):
        self.job_id = job_id
        self.action = action
        self.payload = payload
        self.status = "pending"  # pending | running | stopping | stopped | completed | failed
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.exit_code: Optional[int] = None
        self.result: Dict[str, Any] = {}
        self.logs: Deque[str] = deque(maxlen=500)
        self.cancel_requested = False
        self.cancel_requested_at: Optional[float] = None
        self.process: Optional[subprocess.Popen] = None

    def add_log(self, line: str) -> None:
        self.logs.append(line)

    def to_api(self, include_logs: bool = True) -> Dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "action": self.action,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "result": self.result,
            "cancel_requested": self.cancel_requested,
        }
        if include_logs:
            data["logs"] = list(self.logs)
        return data


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobState] = {}

    def create(self, action: str, payload: Dict[str, Any]) -> JobState:
        job_id = str(uuid.uuid4())
        job = JobState(job_id=job_id, action=action, payload=payload)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[JobState]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )[:limit]
            return [j.to_api(include_logs=False) for j in jobs]

    def request_stop(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)

            # Idempotent stop requests.
            if job.status in ("completed", "failed", "stopped"):
                return {"ok": True, "status": job.status, "message": "job already finished"}

            if not job.cancel_requested:
                job.cancel_requested = True
                job.cancel_requested_at = time.time()
                job.add_log(f"Stop requested: {job_id}")
                logger.info("Stop requested: %s", job_id)

            if job.status == "running":
                job.status = "stopping"

            # For subprocess-backed jobs, request process termination.
            proc = job.process
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

            return {"ok": True, "status": job.status, "message": "stop requested"}


JOB_STORE = JobStore()


def _extract_ingestion_stats_from_logs(logs: Deque[str]) -> Dict[str, Any]:
    """
    Best-effort parsing of the final ingestion summary line(s).

    Keeps UI observability without needing to change the CLI contract.
    """
    try:
        import re as _re

        tail = "\n".join(list(logs)[-120:])
        out: Dict[str, Any] = {}

        dur_m = _re.search(r"duration_s=([0-9.]+)", tail)
        if dur_m:
            out["duration_s"] = float(dur_m.group(1))

        fetched_m = _re.search(r"fetched=([0-9]+)", tail)
        if fetched_m:
            out["fetched"] = int(fetched_m.group(1))

        inserted_m = _re.search(r"inserted=([0-9]+)", tail)
        ingested_m = _re.search(r"ingested=([0-9]+)", tail)
        if inserted_m:
            out["inserted"] = int(inserted_m.group(1))
        elif ingested_m:
            # pipeline uses "ingested" when fetched=0/empty.
            out["inserted"] = int(ingested_m.group(1))

        skipped_m = _re.search(r"skipped=([0-9]+)", tail)
        if skipped_m:
            out["skipped"] = int(skipped_m.group(1))

        errors_m = _re.search(r"errors=([0-9]+)", tail)
        if errors_m:
            out["errors"] = int(errors_m.group(1))

        return out
    except Exception:
        return {}


def _safe_filename(filename: str) -> str:
    # Prevent directory traversal. We only allow a conservative character set.
    name = Path(filename).name
    if not re.fullmatch(r"[A-Za-z0-9._ -]{1,200}", name):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


def _validate_date_range(start_date: Optional[str], end_date: Optional[str]) -> tuple[str, str]:
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="start_date/end_date must be YYYY-MM-DD") from exc
    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    return start_date, end_date


def _spawn_cli_job(job: JobState, cli_args: List[str], result_metadata: Optional[Dict[str, Any]] = None) -> None:
    logger.info("Job started: %s", job.job_id)
    if job.cancel_requested:
        job.status = "stopped"
        job.started_at = time.time()
        job.finished_at = job.started_at
        job.exit_code = 0
        job.result = dict(result_metadata or {})
        job.result["stopped"] = True
        job.add_log(f"Job stopped: {job.job_id}")
        logger.info("Job stopped: %s", job.job_id)
        return
    job.status = "running"
    job.started_at = time.time()

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    if not VENV_PYTHON.exists():
        job.status = "failed"
        job.finished_at = time.time()
        job.exit_code = -1
        job.result = dict(result_metadata or {})
        job.result["error"] = "venv python not found"
        if job.started_at and job.finished_at:
            job.result["duration_s"] = job.finished_at - job.started_at
        job.add_log("ERROR: venv python not found: %s" % str(VENV_PYTHON))
        return

    full_cmd = [str(VENV_PYTHON)] + cli_args
    job.add_log("Running: %s" % " ".join(full_cmd))

    try:
        proc = subprocess.Popen(
            full_cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        job.process = proc

        assert proc.stdout is not None
        for line in proc.stdout:
            job.add_log(line.rstrip("\n"))

        proc.wait()
        job.process = None
        job.exit_code = proc.returncode
        job.finished_at = time.time()

        duration_s = None
        if job.started_at and job.finished_at:
            duration_s = job.finished_at - job.started_at

        job.result = dict(result_metadata or {})
        job.result["exit_code"] = proc.returncode
        if duration_s is not None:
            job.result["duration_s"] = duration_s

        # For ingestion pipelines, parse final stats from logs.
        if job.action == "ingest":
            job.result.update(_extract_ingestion_stats_from_logs(job.logs))

        if job.cancel_requested:
            job.status = "stopped"
            job.result.setdefault("stopped", True)
            logger.info("Job stopped: %s", job.job_id)
        elif proc.returncode == 0:
            job.status = "completed"
            logger.info("Job completed: %s", job.job_id)
        else:
            job.status = "failed"
            job.result.setdefault("error", "non-zero exit code")
    except Exception as e:
        job.status = "failed"
        job.finished_at = time.time()
        job.exit_code = -1
        job.result = dict(result_metadata or {})
        job.result["error"] = str(e)
        if job.started_at and job.finished_at:
            job.result["duration_s"] = job.finished_at - job.started_at
        job.add_log("ERROR: %s" % str(e))
        logger.info("Job failed: %s", job.job_id)


def _spawn_ingest_job(job: JobState, start_date: str, end_date: str) -> None:
    logger.info("Job started: %s", job.job_id)
    if job.cancel_requested:
        job.status = "stopped"
        job.started_at = time.time()
        job.finished_at = job.started_at
        job.exit_code = 0
        job.result = {"stopped": True, "start_date": start_date, "end_date": end_date, "duration_s": 0.0}
        job.add_log(f"Job stopped: {job.job_id}")
        logger.info("Job stopped: %s", job.job_id)
        return
    job.status = "running"
    job.started_at = time.time()
    job.add_log(f"Job started: {job.job_id}")
    job.add_log(f"Starting ingestion from {start_date} to {end_date}")

    try:
        pipeline = IngestionPipeline()
        result = pipeline.run(
            start_date=start_date,
            end_date=end_date,
            should_stop=lambda: job.cancel_requested,
            job_id=job.job_id,
        )
        job.finished_at = time.time()
        if job.started_at and job.finished_at:
            result["duration_s"] = result.get("duration_s", job.finished_at - job.started_at)
        result["start_date"] = start_date
        result["end_date"] = end_date
        job.result = result
        job.exit_code = 0

        if result.get("stopped") or job.cancel_requested:
            job.status = "stopped"
            job.result["status"] = "stopped"
            job.result["stopped"] = True
            job.add_log(f"Job stopped: {job.job_id}")
            logger.info("Job stopped: %s", job.job_id)
        else:
            job.status = "completed"
            job.add_log(f"Job completed: {job.job_id}")
            logger.info("Job completed: %s", job.job_id)
    except Exception as e:
        job.status = "failed"
        job.finished_at = time.time()
        job.exit_code = -1
        job.result = {"error": str(e), "start_date": start_date, "end_date": end_date}
        if job.started_at and job.finished_at:
            job.result["duration_s"] = job.finished_at - job.started_at
        job.add_log(f"ERROR: {e}")
        logger.info("Job failed: %s", job.job_id)


def _start_job_thread(action: str, payload: Dict[str, Any], cli_args: List[str], result_metadata: Optional[Dict[str, Any]] = None) -> str:
    job = JOB_STORE.create(action=action, payload=payload)
    thread = threading.Thread(
        target=_spawn_cli_job,
        args=(job, cli_args, result_metadata),
        daemon=True,
    )
    thread.start()
    return job.job_id


def _start_ingest_thread(start_date: str, end_date: str) -> str:
    job = JOB_STORE.create(action="ingest", payload={"start_date": start_date, "end_date": end_date})
    thread = threading.Thread(
        target=_spawn_ingest_job,
        args=(job, start_date, end_date),
        daemon=True,
    )
    thread.start()
    return job.job_id


app = FastAPI(title="Italian Tender Intelligence - UI", version="0.1")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def ensure_scheduled_jobs() -> None:
    # Ensure we have production-friendly default job config rows.
    default_job_name = "daily_ingestion"
    default_download_job_name = "document_download"
    with get_db() as db:
        existing = (
            db.query(ScheduledJob).filter(ScheduledJob.job_name == default_job_name).first()
        )
        if not existing:
            db.add(
                ScheduledJob(
                    job_name=default_job_name,
                    enabled=False,
                    schedule_time="09:00",
                    last_status=None,
                    last_run_at=None,
                )
            )

        existing_download = (
            db.query(ScheduledJob)
            .filter(ScheduledJob.job_name == default_download_job_name)
            .first()
        )
        if not existing_download:
            db.add(
                ScheduledJob(
                    job_name=default_download_job_name,
                    enabled=False,
                    schedule_time="09:10",
                    last_status=None,
                    last_run_at=None,
                )
            )

        # Backwards compatibility: older builds may have used this job name.
        # Keep it disabled so only `document_download` runs going forward.
        legacy_download = (
            db.query(ScheduledJob)
            .filter(ScheduledJob.job_name == "daily_download_documents")
            .first()
        )
        if legacy_download and legacy_download.enabled:
            legacy_download.enabled = False


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    if not INDEX_HTML.exists():
        return HTMLResponse("<h1>UI not built</h1>", status_code=404)
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/api/status")
def api_status(response: Response) -> Dict[str, Any]:
    # Ensure the UI never relies on cached stats.
    response.headers["Cache-Control"] = "no-store"
    print(
        "api_status DATABASE_URL=%s (%s)"
        % (Config.DATABASE_URL, describe_database_url(Config.DATABASE_URL)),
        flush=True,
    )

    with get_db() as db:
        tender_count = db.query(Tender).count()
        org_count = db.query(Organization).count()
        issuer_count = db.query(Issuer).count()
        documents_count = db.query(TenderDocument).count()
        search_count = db.query(SearchQuery).count()

    print(
        "api_status counts tenders=%s organizations=%s issuers=%s documents=%s search_queries=%s"
        % (tender_count, org_count, issuer_count, documents_count, search_count),
        flush=True,
    )

    return {
        "tenders": tender_count,
        "organizations": org_count,
        "issuers": issuer_count,
        "documents": documents_count,
        # Backward compatible field name:
        "search_queries": search_count,
        # Spec-aligned field name:
        "search_queries_count": search_count,
        "job_queue_size": len(JOB_STORE._jobs),
    }


@app.get("/api/debug/org-count")
def api_debug_org_count(response: Response) -> Dict[str, Any]:
    # Forced no-cache + raw SQL COUNT for debugging UI vs backend.
    response.headers["Cache-Control"] = "no-store"
    print(
        "api_debug_org_count DATABASE_URL=%s (%s)"
        % (Config.DATABASE_URL, describe_database_url(Config.DATABASE_URL)),
        flush=True,
    )

    with get_db() as db:
        row = db.execute(text("SELECT COUNT(*) FROM organizations")).fetchone()
        count = int(row[0]) if row and row[0] is not None else 0

    return {
        "organizations": count,
        "database": describe_database_url(Config.DATABASE_URL),
    }


class ScheduledJobUpsert(BaseModel):
    job_name: str
    enabled: bool = True
    # Format: HH:MM (24h)
    schedule_time: str


class ScheduledJobToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/jobs")
def api_scheduled_jobs() -> Dict[str, Any]:
    """
    UI-managed scheduled ingestion jobs.
    Includes last run + running run tail for observability.
    """
    with get_db() as db:
        jobs = db.query(ScheduledJob).order_by(ScheduledJob.job_name).all()

        out_jobs: List[Dict[str, Any]] = []
        for j in jobs:
            current_run = (
                db.query(JobRun)
                .filter(JobRun.job_name == j.job_name, JobRun.status == "running")
                .order_by(JobRun.started_at.desc())
                .first()
            )

            last_completed = (
                db.query(JobRun)
                .filter(
                    JobRun.job_name == j.job_name,
                    JobRun.status.in_(("success", "failed")),
                )
                .order_by(JobRun.finished_at.desc())
                .first()
            )

            out_jobs.append(
                {
                    "job_name": j.job_name,
                    "enabled": bool(j.enabled),
                    "schedule_time": j.schedule_time,
                    "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
                    "last_status": j.last_status,
                    "current_run": (
                        {
                            "run_id": current_run.id,
                            "status": current_run.status,
                            "started_at": current_run.started_at.isoformat() if current_run.started_at else None,
                            "log_tail": current_run.log_tail or "",
                        }
                        if current_run
                        else None
                    ),
                    "last_run": (
                        {
                            "run_id": last_completed.id,
                            "status": last_completed.status,
                            "finished_at": last_completed.finished_at.isoformat() if last_completed.finished_at else None,
                            "exit_code": last_completed.exit_code,
                            "log_tail": last_completed.log_tail or "",
                        }
                        if last_completed
                        else None
                    ),
                }
            )

        return {"jobs": out_jobs}


@app.post("/api/jobs")
def api_upsert_scheduled_job(req: ScheduledJobUpsert) -> Dict[str, Any]:
    # Validate schedule_time = HH:MM
    if not re.fullmatch(r"\d{2}:\d{2}", req.schedule_time.strip()):
        raise HTTPException(status_code=400, detail="schedule_time must be HH:MM")

    with get_db() as db:
        job = db.query(ScheduledJob).filter(ScheduledJob.job_name == req.job_name).first()
        if not job:
            job = ScheduledJob(
                job_name=req.job_name,
                enabled=req.enabled,
                schedule_time=req.schedule_time.strip(),
                last_status=None,
                last_run_at=None,
            )
            db.add(job)
        else:
            job.enabled = bool(req.enabled)
            job.schedule_time = req.schedule_time.strip()

    return {"ok": True}


@app.post("/api/jobs/{job_name}/toggle")
def api_toggle_scheduled_job(job_name: str, req: ScheduledJobToggleRequest) -> Dict[str, Any]:
    with get_db() as db:
        job = db.query(ScheduledJob).filter(ScheduledJob.job_name == job_name).first()
        if not job:
            raise HTTPException(status_code=404, detail="Scheduled job not found")
        job.enabled = bool(req.enabled)

    return {"ok": True}


@app.post("/api/jobs/{job_name}/run")
def api_run_scheduled_job(job_name: str) -> Dict[str, Any]:
    scheduled_for = datetime.utcnow().replace(second=0, microsecond=0)

    run_id = acquire_job_run(job_name=job_name, scheduled_for=scheduled_for)
    if run_id is None:
        raise HTTPException(status_code=409, detail="Job is disabled or already running")

    start_job_run_async(run_id)
    return {"ok": True, "run_id": run_id}


@app.get("/api/job-queue")
def api_job_queue(limit: int = 20) -> Dict[str, Any]:
    return {"jobs": JOB_STORE.list_recent(limit=limit)}


@app.get("/api/job-queue/{job_id}")
def api_job(job_id: str) -> Dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_api(include_logs=True)


@app.get("/api/job-queue/{job_id}/status")
def api_job_status(job_id: str) -> Dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "cancel_requested": job.cancel_requested,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@app.post("/api/job-queue/{job_id}/stop")
def api_job_stop(job_id: str) -> Dict[str, Any]:
    try:
        return JOB_STORE.request_stop(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")


@app.post("/jobs/{job_id}/stop")
def job_stop_alias(job_id: str) -> Dict[str, Any]:
    return api_job_stop(job_id)


@app.get("/api/orgs")
def api_orgs(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    with get_db() as db:
        rows = (
            db.query(Organization)
            .order_by(Organization.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
    return {
        "items": [
            {
                "id": o.id,
                "tax_id": o.tax_id,
                "name": o.name,
                "city": o.city,
                "region": o.region,
            }
            for o in rows
        ]
    }


@app.get("/api/organizations")
def api_organizations(limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """
    DB-only organizations browser for the UI.
    Returns participation counts via JOIN with `tender_participants`.
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    with get_db() as db:
        total = db.query(Organization).count()

        rows = (
            db.query(
                Organization.id,
                Organization.tax_id,
                Organization.name,
                func.count(TenderParticipant.tender_id).label("participation_count"),
            )
            .outerjoin(TenderParticipant, TenderParticipant.organization_id == Organization.id)
            .group_by(Organization.id, Organization.tax_id, Organization.name)
            .order_by(Organization.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

    return {
        "total": total,
        "items": [
            {
                "id": r[0],
                "tax_id": r[1],
                "name": r[2],
                "participation_count": int(r[3] or 0),
            }
            for r in rows
        ],
    }


@app.get("/api/organizations/{org_id}")
def api_organization_details(
    org_id: int,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    DB-only organization detail endpoint including related tenders.
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    with get_db() as db:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        participation_count = (
            db.query(func.count(TenderParticipant.tender_id))
            .filter(TenderParticipant.organization_id == org_id)
            .scalar()
        )
        participation_count = int(participation_count or 0)

        # Related tenders via join with tender_participants.
        tender_rows = (
            db.query(Tender)
            .join(TenderParticipant, TenderParticipant.tender_id == Tender.id)
            .filter(TenderParticipant.organization_id == org_id)
            .order_by(Tender.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Build a plain dict while the SQLAlchemy session is alive.
        return {
            "id": org.id,
            "tax_id": org.tax_id,
            "name": org.name,
            "normalized_name": org.normalized_name,
            "source_count": org.source_count,
            "country": org.country,
            "city": org.city,
            "region": org.region,
            "industry": org.industry,
            "size": org.size,
            "participation_count": participation_count,
            "related_tenders": [
                {
                    "tender_id": t.tender_id,
                    "title": t.title,
                    "estimated_value": str(t.estimated_value) if t.estimated_value is not None else None,
                    "publication_date": t.publication_date.isoformat() if t.publication_date is not None else None,
                    "contract_type": t.contract_type,
                    "currency": t.currency,
                }
                for t in tender_rows
            ],
        }


@app.get("/api/tenders")
def api_tenders(limit: int = 20, offset: int = 0, include_embedding: bool = False) -> Dict[str, Any]:
    """
    Paginated tender browser for the web UI.

    By default we exclude `embedding` because it is large; set include_embedding=true if you really need it.
    """
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    with get_db() as db:
        total = db.query(Tender).count()

        # Join issuer name for UI convenience.
        rows = (
            db.query(Tender, Issuer.name)
            .outerjoin(Issuer, Tender.issuer_id == Issuer.id)
            .order_by(Tender.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

        items: list[Dict[str, Any]] = []
        for tender, issuer_name in rows:
            out: Dict[str, Any] = {
                "id": tender.id,
                "tender_id": tender.tender_id,
                "title": tender.title,
                "issuer": issuer_name,
                "estimated_value": str(tender.estimated_value) if tender.estimated_value is not None else None,
                "currency": tender.currency,
                "publication_date": tender.publication_date.isoformat() if tender.publication_date is not None else None,
                "submission_deadline": tender.submission_deadline.isoformat() if tender.submission_deadline is not None else None,
                "execution_start_date": tender.execution_start_date.isoformat() if tender.execution_start_date is not None else None,
                "execution_end_date": tender.execution_end_date.isoformat() if tender.execution_end_date is not None else None,
                "execution_location": tender.execution_location,
                "nuts_codes": tender.nuts_codes,
                "cpv_codes": tender.cpv_codes,
                "contract_type": tender.contract_type,
                "eu_funded": tender.eu_funded,
                "renewable": tender.renewable,
                "has_lots": tender.has_lots,
                "lots_data": tender.lots_data,
                "tender_url": tender.tender_url,
                "document_portal_url": tender.document_portal_url,
                "summary": tender.summary,
                "created_at": tender.created_at.isoformat() if tender.created_at is not None else None,
                "updated_at": tender.updated_at.isoformat() if tender.updated_at is not None else None,
            }

            if include_embedding:
                # pgvector vector type can be large; return only if explicitly requested.
                out["embedding"] = tender.embedding

            items.append(out)

    return {"total": total, "items": items}


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    cpv: Optional[str] = None
    nuts: Optional[str] = None
    contract_type: Optional[str] = None
    eu_funded: Optional[bool] = None
    limit: int = 10


@app.post("/api/search")
def api_search(req: SearchRequest) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    if req.min_value is not None:
        filters["min_value"] = req.min_value
    if req.max_value is not None:
        filters["max_value"] = req.max_value
    if req.cpv:
        filters["cpv_codes"] = [req.cpv]
    if req.nuts:
        filters["nuts_codes"] = [req.nuts]
    if req.contract_type:
        filters["contract_type"] = req.contract_type
    if req.eu_funded is not None:
        filters["eu_funded"] = req.eu_funded

    # Persist every search attempt (even if results are empty).
    # This is intentionally committed immediately to keep tracking reliable.
    with get_db() as db:
        row = SearchQuery(
            query_text=req.query,
            filters=filters if filters else {},
            results=None,
        )
        db.add(row)
        db.commit()

    searcher = HybridSearch()
    results = searcher.search(
        req.query,
        filters=filters if filters else None,
        limit=req.limit,
    )
    return {"results": results}


class ActionRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    org_id: Optional[int] = None
    output: Optional[str] = None
    portal: Optional[str] = None
    limit: Optional[int] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


@app.post("/api/actions/ingest")
def api_ingest(req: ActionRequest) -> Dict[str, Any]:
    start_date, end_date = _validate_date_range(req.start_date, req.end_date)
    job_id = _start_ingest_thread(start_date=start_date, end_date=end_date)
    return {"job_id": job_id}


@app.post("/api/actions/extract-orgs")
def api_extract_orgs(req: ActionRequest) -> Dict[str, Any]:
    start_date, end_date = _validate_date_range(req.start_date, req.end_date)
    job_id = _start_job_thread(
        action="extract-orgs",
        payload={"start_date": start_date, "end_date": end_date},
        cli_args=[
            "-m",
            "src.cli.main",
            "extract-orgs",
            "--start-date",
            start_date,
            "--end-date",
            end_date,
        ],
        result_metadata={"start_date": start_date, "end_date": end_date},
    )
    return {"job_id": job_id}


@app.post("/api/actions/demo-search")
def api_demo_search(req: ActionRequest) -> Dict[str, Any]:
    org_id = req.org_id
    if org_id is None:
        raise HTTPException(status_code=400, detail="org_id is required")
    job_id = _start_job_thread(
        action="demo-search",
        payload={"org_id": org_id},
        cli_args=["-m", "src.cli.main", "demo-search", "--org-id", str(org_id)],
        result_metadata={"org_id": org_id},
    )
    return {"job_id": job_id}


@app.post("/api/actions/analyze-portals")
def api_analyze_portals(req: ActionRequest) -> Dict[str, Any]:
    output = req.output or "portal_analysis.csv"
    output = _safe_filename(output)
    job_id = _start_job_thread(
        action="analyze-portals",
        payload={"output": output},
        cli_args=["-m", "src.cli.main", "analyze-portals", "--output", output],
        result_metadata={"output": output},
    )
    return {"job_id": job_id}


@app.post("/api/actions/download-docs")
def api_download_docs(req: ActionRequest) -> Dict[str, Any]:
    portal = req.portal
    if not portal:
        raise HTTPException(status_code=400, detail="portal is required")
    limit = req.limit or 10
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")
    job_id = _start_job_thread(
        action="download-docs",
        payload={"portal": portal, "limit": limit},
        cli_args=[
            "-m",
            "src.cli.main",
            "download-docs",
            "--portal",
            portal,
            "--limit",
            str(limit),
        ],
        result_metadata={"portal": portal, "limit": limit},
    )
    return {"job_id": job_id}


@app.get("/api/artifacts/{filename}")
def api_artifact(filename: str) -> HTMLResponse:
    # For UI convenience. We render CSV content directly (browser will display it).
    safe_name = _safe_filename(filename)
    path = BASE_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Return HTML with preformatted CSV so it looks nice in the browser.
    text = path.read_text(encoding="utf-8", errors="replace")
    return HTMLResponse(
        "<html><body style='font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas; white-space: pre-wrap; margin: 16px;'>"
        + text
        + "</body></html>"
    )


if __name__ == "__main__":
    import uvicorn

    # Default local dev port; adjust as needed for your environment.
    uvicorn.run("src.web.server:app", host="0.0.0.0", port=8000, log_level="info")

