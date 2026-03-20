import logging
import subprocess
import threading
from datetime import datetime, time, timedelta
from typing import Optional, Tuple

from sqlalchemy import text

from src.config import Config
from src.database.connection import SessionLocal
from src.database.models import JobRun, ScheduledJob

logger = logging.getLogger(__name__)

# Used to run CLI commands under the project's venv.
BASE_DIR = __file__
try:
    from pathlib import Path

    BASE_DIR = Path(__file__).resolve().parents[2]  # tender/
except Exception:
    BASE_DIR = None

VENV_PYTHON = None
if BASE_DIR is not None:
    VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python"


def _utc_now() -> datetime:
    # Keep DB timestamps consistent; the UI just renders them.
    return datetime.utcnow()


def _compute_daily_ingestion_range() -> Tuple[str, str]:
    """
    Daily ingestion range (simple + deterministic):
    - start_date = yesterday
    - end_date   = yesterday
    """
    d = datetime.utcnow().date() - timedelta(days=1)
    iso = d.isoformat()
    return iso, iso


def _tail_log_tail(lines: list[str], max_chars: int = 12000) -> str:
    if not lines:
        return ""
    text = "\n".join(lines[-500:])
    if len(text) > max_chars:
        return text[-max_chars:]
    return text


def acquire_job_run(
    job_name: str,
    scheduled_for: Optional[datetime] = None,
) -> Optional[int]:
    """
    Create a `job_runs` row and mark the scheduled job as running.
    Returns run_id if acquired, else None.
    """
    if VENV_PYTHON is None:
        raise RuntimeError("VENV_PYTHON could not be resolved")

    now = _utc_now()

    db = SessionLocal()
    try:
        # Lock the scheduled_jobs row to prevent concurrent runs.
        job = (
            db.query(ScheduledJob)
            .filter(ScheduledJob.job_name == job_name)
            .with_for_update(nowait=True)
            .first()
        )
        if not job or not job.enabled:
            return None
        if (job.last_status or "").lower() == "running":
            return None

        # If scheduled_for is set, rely on the unique constraint to dedupe.
        run = JobRun(
            job_name=job_name,
            scheduled_for=scheduled_for,
            status="running",
            started_at=now,
            log_tail="",
        )
        db.add(run)

        job.last_status = "running"
        db.commit()
        return run.id
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()


def execute_job_run(run_id: int) -> None:
    """
    Execute ingestion + extract-orgs for the job run.
    Updates job_runs and scheduled_jobs with status + log tail.
    """
    if VENV_PYTHON is None:
        raise RuntimeError("VENV_PYTHON could not be resolved")

    db = SessionLocal()
    run: Optional[JobRun] = None
    try:
        run = db.query(JobRun).filter(JobRun.id == run_id).first()
        if not run:
            return

        job_name = run.job_name

        # Live log accumulation; update DB periodically.
        log_lines: list[str] = []
        last_update_at = _utc_now()

        def flush_log_tail() -> None:
            nonlocal last_update_at
            tail = _tail_log_tail(log_lines)
            db.query(JobRun).filter(JobRun.id == run_id).update({"log_tail": tail})
            db.commit()
            last_update_at = _utc_now()

        status = "success"
        exit_code: Optional[int] = 0

        if job_name == "daily_ingestion":
            start_date, end_date = _compute_daily_ingestion_range()
            cmds = [
                [
                    str(VENV_PYTHON),
                    "-m",
                    "src.cli.main",
                    "ingest",
                    "--start-date",
                    start_date,
                    "--end-date",
                    end_date,
                ],
                [
                    str(VENV_PYTHON),
                    "-m",
                    "src.cli.main",
                    "extract-orgs",
                    "--start-date",
                    start_date,
                    "--end-date",
                    end_date,
                ],
            ]
        elif job_name == "document_download":
            # Ensure we have portal distribution CSV for top-domain selection.
            cmds = [
                [
                    str(VENV_PYTHON),
                    "-m",
                    "src.cli.main",
                    "analyze-portals",
                    "--output",
                    Config.PORTAL_ANALYSIS_DEFAULT_FILE,
                ],
                [
                    str(VENV_PYTHON),
                    "-m",
                    "src.cli.main",
                    "download-documents",
                    "--portal-analysis-file",
                    Config.PORTAL_ANALYSIS_DEFAULT_FILE,
                    "--limit",
                    str(Config.DOWNLOAD_DOCS_LIMIT),
                ],
            ]
        else:
            logger.warning("Unknown scheduled job_name=%s; marking failed", job_name)
            status = "failed"
            exit_code = -1
            cmds = []

        for cmd in cmds:
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=None,
            )

            assert proc.stdout is not None
            for line in proc.stdout:
                line = (line or "").rstrip("\n")
                log_lines.append(line)
                if len(log_lines) % 25 == 0:
                    # Keep DB updates infrequent (production-safe).
                    if (_utc_now() - last_update_at).total_seconds() >= 1.0:
                        flush_log_tail()

            proc.wait()
            exit_code = proc.returncode
            if exit_code != 0:
                status = "failed"
                break

        # Final log flush + status update.
        flush_log_tail()

        finished_at = _utc_now()
        db.query(JobRun).filter(JobRun.id == run_id).update(
            {
                "status": status,
                "finished_at": finished_at,
                "exit_code": exit_code,
            }
        )

        db.query(ScheduledJob).filter(ScheduledJob.job_name == job_name).update(
            {
                "last_run_at": finished_at,
                "last_status": status,
            }
        )
        db.commit()
    except Exception as e:
        logger.exception("execute_job_run failed run_id=%s: %s", run_id, e)
        # Best-effort failure marking.
        try:
            db.query(JobRun).filter(JobRun.id == run_id).update(
                {"status": "failed", "finished_at": _utc_now(), "exit_code": -1}
            )
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


def start_job_run_async(run_id: int) -> None:
    t = threading.Thread(target=execute_job_run, args=(run_id,), daemon=True)
    t.start()

