#!/usr/bin/env python3
"""
Database initialization script.
Creates all tables and enables pgvector extension.
"""
import sys
from pathlib import Path
import re
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import engine, Base
from src.config import Config
from src.database.models import (
    Issuer,
    Tender,
    Organization,
    TenderParticipant,
    Document,
    SearchQuery,
    TenderDocument,
    ScheduledJob,
    JobRun,
)
from sqlalchemy import text, create_engine
from sqlalchemy.exc import OperationalError


def _safe_pg_identifier(name: str) -> str:
    # We only accept simple identifiers coming from the DATABASE_URL path.
    # This is used to build `CREATE DATABASE ...` safely.
    if not re.match(r"^[A-Za-z0-9_]+$", name or ""):
        raise ValueError(f"Unsafe Postgres identifier: {name!r}")
    return name


def _ensure_postgres_database_exists() -> None:
    """
    Create the target Postgres DB if it is missing.

    This keeps `init-db` beginner-friendly on fresh machines: users can run
    init without manually executing `CREATE DATABASE ...`.
    """
    database_url = Config.DATABASE_URL or ""
    if not database_url.startswith("postgres"):
        return

    parsed = urlparse(database_url)
    dbname = (parsed.path or "").lstrip("/")
    if not dbname:
        return

    dbname_safe = _safe_pg_identifier(dbname)

    base_db = "postgres"
    base_parsed = parsed._replace(path=f"/{base_db}")
    base_url = urlunparse(base_parsed)

    base_engine = create_engine(base_url)
    with base_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
            {"dbname": dbname_safe},
        ).fetchone()
        if exists:
            return

        conn.execute(text(f"CREATE DATABASE {dbname_safe}"))

def init_database():
    """Initialize database schema and extensions"""
    print("Initializing database...")
    
    try:
        try:
            with engine.connect() as conn:
                print("Enabling pgvector extension...")
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
        except OperationalError:
            # Most common case on a fresh machine: the target DB doesn't exist.
            # We attempt to create it, then retry initialization.
            _ensure_postgres_database_exists()
            with engine.connect() as conn:
                print("Enabling pgvector extension...")
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
        
        print("Creating tables...")
        Base.metadata.create_all(bind=engine)

        # Organizations Part 2 columns + backfill + unique index (idempotent)
        org_migrate = [
            "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS normalized_name TEXT",
            "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS source_count INTEGER DEFAULT 0",
            """UPDATE organizations SET normalized_name = UPPER(TRIM(name)) WHERE normalized_name IS NULL""",
            "UPDATE organizations SET source_count = 0 WHERE source_count IS NULL",
            "ALTER TABLE organizations ALTER COLUMN source_count SET DEFAULT 0",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_org_tax_id ON organizations (tax_id)",
        ]
        
        # Tenders columns required by newer pipeline versions.
        tender_migrate = [
            "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS source_platform VARCHAR(20)",
        ]
        tender_documents_migrate = [
            # Backwards compatible: this column may be missing in older databases.
            "ALTER TABLE tender_documents ADD COLUMN IF NOT EXISTS file_name TEXT",
        ]
        search_queries_migrate = [
            # Ensure required tracking columns exist on older databases.
            "ALTER TABLE search_queries ADD COLUMN IF NOT EXISTS query_text TEXT",
            "ALTER TABLE search_queries ADD COLUMN IF NOT EXISTS filters JSONB",
            "ALTER TABLE search_queries ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]
        with engine.connect() as conn:
            for stmt in org_migrate:
                conn.execute(text(stmt))
            for stmt in tender_migrate:
                conn.execute(text(stmt))
            for stmt in tender_documents_migrate:
                conn.execute(text(stmt))
            for stmt in search_queries_migrate:
                conn.execute(text(stmt))
            conn.commit()
        
        print("\n✓ Database initialized successfully")
        print("\nTables created:")
        print("  - issuers")
        print("  - tenders")
        print("  - organizations")
        print("  - tender_participants")
        print("  - documents")
        print("  - search_queries")
        print("  - tender_documents")
        print("  - scheduled_jobs")
        print("  - job_runs")
        
    except Exception as e:
        print(f"\n❌ Error initializing database: {e}")
        sys.exit(1)

if __name__ == '__main__':
    init_database()
