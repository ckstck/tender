import os
from pathlib import Path
from dotenv import load_dotenv

# Always load the same .env file regardless of the working directory.
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"  # tender/.env
load_dotenv(dotenv_path=str(ENV_PATH), override=False)


def redact_database_url(url: str) -> str:
    """
    Redact credentials from a DATABASE_URL for logs.
    Keeps host/db info but removes password/token if present.
    """
    if not url:
        return url

    # postgres://user:pass@host/db or similar
    if "@" in url and "://" in url:
        try:
            prefix, rest = url.split("://", 1)
            creds, tail = rest.split("@", 1)
            if ":" in creds:
                user, _pwd = creds.split(":", 1)
                creds = f"{user}:***"
            else:
                creds = "***"
            return f"{prefix}://{creds}@{tail}"
        except Exception:
            return url

    return url


def describe_database_url(url: str) -> str:
    """
    Provide a short human-readable identifier for the DB target.
    Especially useful for debugging sqlite vs postgres mismatches.
    """
    if not url:
        return "unknown"

    if url.startswith("sqlite:///"):
        return f"sqlite:{url.removeprefix('sqlite:///')}"
    if url.startswith("sqlite:"):
        return f"sqlite:{url.removeprefix('sqlite:')}"

    # Keep it short: postgres://host:port/db
    # Example: postgresql://postgres:***@localhost:5432/tender_db
    redacted = redact_database_url(url)
    return redacted

class Config:
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://localhost/tender_db')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    ANAC_API_KEY = os.getenv('ANAC_API_KEY', '')
    
    S3_ENDPOINT = os.getenv('S3_ENDPOINT')
    S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY')
    S3_SECRET_KEY = os.getenv('S3_SECRET_KEY')
    S3_BUCKET = os.getenv('S3_BUCKET', 'tenders')
    
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    INGESTION_DAYS_BACK = int(os.getenv('INGESTION_DAYS_BACK', '30'))
    # How many records to request per page from ANAC endpoints.
    # Also used as an ingestion batch size when streaming tenders.
    INGESTION_BATCH_SIZE = int(os.getenv('INGESTION_BATCH_SIZE', '100'))
    # Optional safety valve for backfills/debugging: stop after N yielded tenders.
    # When unset/empty, ingest processes the full date window.
    _max_tenders_raw = os.getenv('INGESTION_MAX_TENDERS')
    INGESTION_MAX_TENDERS = int(_max_tenders_raw) if _max_tenders_raw else None
    
    # Commit frequency for organization extraction.
    # Smaller values reduce data loss risk when a job is stopped/interrupted.
    EXTRACT_ORGS_COMMIT_EVERY = int(os.getenv("EXTRACT_ORGS_COMMIT_EVERY", "100"))

    # Document download pipeline
    DOWNLOAD_DOCS_LIMIT = int(os.getenv("DOWNLOAD_DOCS_LIMIT", "10"))
    PORTAL_ANALYSIS_DEFAULT_FILE = os.getenv("PORTAL_ANALYSIS_DEFAULT_FILE", "portal_analysis.csv")
    
    # LLM model used for tender summaries.
    # You can override at runtime via OPENAI_MODEL env var.
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    EMBEDDING_MODEL = 'text-embedding-3-small'
    EMBEDDING_DIMENSIONS = 1536
    
    ANAC_BASE_URL = 'https://dati.anticorruzione.it/opendata'
