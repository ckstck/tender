"""
Scheduled job runner for periodic ingestion.

This package is intentionally lightweight: cron triggers `python -m src.scheduler.run_jobs`,
and all business logic lives in `job_runner`.
"""

