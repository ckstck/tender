import logging
from datetime import datetime, timedelta

from src.database.connection import SessionLocal
from src.database.models import JobRun, ScheduledJob
from src.scheduler.job_runner import acquire_job_run, execute_job_run

logger = logging.getLogger(__name__)


def run_jobs() -> None:
    """
    Cron entrypoint.

    cron:
      */5 * * * * python -m src.scheduler.run_jobs

    It matches jobs where:
      now HH:MM == scheduled_jobs.schedule_time
    """
    now = datetime.utcnow()
    minute_key = now.strftime("%H:%M")

    db = SessionLocal()
    try:
        due_jobs = db.query(ScheduledJob).filter(
            ScheduledJob.enabled.is_(True),
            ScheduledJob.schedule_time == minute_key,
        ).all()
    finally:
        db.close()

    if not due_jobs:
        return

    for job in due_jobs:
        scheduled_for = datetime(
            now.year,
            now.month,
            now.day,
            hour=int(minute_key.split(":")[0]),
            minute=int(minute_key.split(":")[1]),
        )

        # Avoid redundant duplicate triggers.
        # (UniqueConstraint handles most cases; this is just a fast pre-check.)
        run_id = acquire_job_run(job.job_name, scheduled_for=scheduled_for)
        if run_id is None:
            continue

        logger.info("Running scheduled job %s (run_id=%s)", job.job_name, run_id)
        execute_job_run(run_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_jobs()

