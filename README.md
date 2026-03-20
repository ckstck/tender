# Italian Tender Intelligence System

End-to-end system for ingesting, enriching, and searching Italian public procurement tenders, with optional document storage.

This repository implements a production-style pipeline aligned with challenge Parts 1-5. The demo flow focuses on CLI commands, with a lightweight web UI for observability and scheduled job control.

## Project Overview

Core capabilities:

- Ingest tenders from ANAC OCDS (with fallback behavior)
- Extract and deduplicate participating organizations
- Perform hybrid search (structured filters + vector similarity)
- Analyze tender document portal distribution and export `portal_analysis.csv`
- Download tender-related documents from the selected portal and store them in S3-compatible object storage (MinIO)

## Quick Start

### 0) Clone repository
```bash
# Replace with your repo URL (or just `cd` if you've already cloned)
git clone <REPO_URL>
cd <REPO_ROOT>/tender
```

### Prerequisites

- Python 3.9+
- PostgreSQL with the `pgvector` extension
- (Optional) OpenAI API key for enrichment and embedding generation
- (Required for document download) S3-compatible storage (MinIO recommended)

Optional (recommended for a first run): Docker Compose
- `docker compose up -d` (starts Postgres + pgvector)
- Postgres will be available at `localhost:5432` with DB name `tender_db`
Run `docker compose up -d` before `init-db`.

### 1) Create environment

From the `tender/` directory:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment

Create `tender/.env` from `tender/.env.example`:

```bash
cp .env.example .env
```

Ensure `DATABASE_URL` points to your PostgreSQL instance and that the S3 values are correct if you plan to run document downloads.

### 3) Initialize database schema

```bash
./venv/bin/python -m src.cli.main init-db
```

This script is idempotent: you can re-run it safely.

Expected log snippets:
- `Initializing database...`
- `Enabling pgvector extension...`
- `Creating tables...`
- `✓ Database initialized successfully`

## Environment Variables (from `.env.example`)

The CLI and web server load `.env` automatically from the `tender/` folder, so the working directory does not matter.

### Required for the app to run
- `DATABASE_URL`: PostgreSQL connection string.

### OpenAI (optional)
- `OPENAI_API_KEY`: if unset, the app still runs:
  - summaries fall back to truncated titles
  - embeddings use a deterministic pseudo-embedding fallback (so `search` still returns results)
- `OPENAI_MODEL`: chat model used for summaries (default: `gpt-5.4-mini`).

### ANAC (optional)
- `ANAC_API_KEY`: optional token for ANAC Open Data. Leaving it blank can still work but may hit stricter WAF/rate limits.

### Object storage (optional)
- `S3_ENDPOINT`: S3/MinIO endpoint URL.
- `S3_ACCESS_KEY`: access key for S3/MinIO.
- `S3_SECRET_KEY`: secret key for S3/MinIO.
- `S3_BUCKET`: bucket name (default: `tenders`).

If S3 env vars are not set, document download falls back to local storage under `.tmp_storage/`.

### Demo / tuning (optional)
- `INGESTION_DAYS_BACK`: default ingestion window size.
- `EXTRACT_ORGS_COMMIT_EVERY`: commit frequency during `extract-orgs`.
- `DOWNLOAD_DOCS_LIMIT`: default per-portal limit during document download.
- `PORTAL_ANALYSIS_DEFAULT_FILE`: default name for `portal_analysis.csv`.
- `INGESTION_MAX_TENDERS`: safety valve for `ingest` (set to e.g. `50` for a quick demo).

## CLI Usage

All commands are implemented in `src/cli/main.py` (Click-based). They print progress and summary counts for demo readiness.

Activate your virtual environment first:

```bash
source venv/bin/activate
```

### Initialize database

Run once (idempotent) to create tables and enable `pgvector`:

```bash
python -m src.cli.main init-db
```

### Ingest tenders

Fetch tenders in a date range and write to the `tenders` table.

```bash
python -m src.cli.main ingest --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

For a network-free demo, append `--demo-data` (loads deterministic data from `db_dumps/`).

Example:

```bash
python -m src.cli.main ingest --start-date 2025-08-01 --end-date 2025-08-05
```

### Extract organizations

Parse tender participants and write to `organizations` and `tender_participants`.

```bash
python -m src.cli.main extract-orgs --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

Example:

```bash
python -m src.cli.main extract-orgs --start-date 2025-08-01 --end-date 2025-08-05
```

### Search

Run hybrid search with optional structured filters. Each CLI search is persisted to `search_queries` for demo tracking (even if results are empty).

```bash
python -m src.cli.main search --query "TEXT" --limit N
```

Optional filters:

- `--min-value FLOAT`
- `--max-value FLOAT`
- `--cpv STRING`
- `--nuts STRING`
- `--contract-type services|supplies|works`
- `--eu-funded true|false`

Examples:

```bash
python -m src.cli.main search --query "road maintenance and infrastructure services" --contract-type services --limit 5
python -m src.cli.main search --query "IT equipment" --min-value 100000 --limit 5
python -m src.cli.main search --query "digital transformation" --eu-funded true --limit 5
```

### Analyze document portals

Reads portal URLs from stored tenders and outputs `portal_analysis.csv`.

```bash
python -m src.cli.main analyze-portals --output portal_analysis.csv
```

It always writes the CSV header; if there are no usable portal domains yet, the file may contain only the header.

### Download documents

Two variants exist:

- `download-docs`: download from a specified portal domain (`--portal`)
- `download-documents`: download from the top portal found in `portal_analysis.csv`

Download from top portal:

```bash
python -m src.cli.main download-documents --portal-analysis-file portal_analysis.csv --limit N
```

If `portal_analysis.csv` is empty (header-only), the command skips download gracefully.

Download from a specific portal:

```bash
python -m src.cli.main download-docs --portal "example.portal.it" --limit N
```

### System status

Shows current DB counts (including the correct `tender_documents` count).

```bash
python -m src.cli.main status
```

## UI (Web Dashboard)

The repository also ships a lightweight web UI for running the same demo steps with quick visibility into counts, logs, and scheduled jobs.

### Start the server

```bash
./venv/bin/python -m src.web.server
```

Then open:

- http://localhost:8000

### What you can manage from the UI

Expected UI behavior during the demo:
- After `init-db`, the counts start at `0`.
- After `ingest`, the UI status strip shows non-zero `tenders` and `issuers`.
- After each `search`, the UI status strip’s `search_queries` count increments (even if no results are found).
- After `analyze-portals`, `portal_analysis.csv` should be generated on disk (and the UI can use it for download).
- After `download-documents`, the UI status strip’s `documents` count increases when document URLs are available and downloads succeed.

1. **Status strip**
   - Shows live DB-backed metrics (tenders, organizations, issuers, stored documents, search query count).
   - Use the **Refresh** button to force a fresh `/api/status` read.

2. **Search**
   - Open the **Search** modal from the sidebar.
   - Searches are executed via `POST /api/search` and are persisted into `search_queries` for demo tracking.

3. **Primary Actions**
   - **Ingest Tenders**: runs `ingest` (background job)
   - **Extract Organizations**: runs `extract-orgs` (background job)

4. **Secondary Actions**
   - **Analyze Portals**: runs `analyze-portals` and produces `portal_analysis.csv`
   - **Download Documents**: runs `download-docs` (portal domain MVP) from the UI input

5. **Job Monitor + Scheduled Jobs**
   - The scheduled jobs panel is dynamic: it fetches configured jobs from `GET /api/jobs`.
   - For each job you can:
     - Toggle **Enabled/Disabled** (persisted to `scheduled_jobs`)
     - Adjust **Schedule Time** (HH:MM)
     - Click **Run Now** (calls `POST /api/jobs/{job_name}/run`)
   - Recent logs are shown from `job_runs.log_tail`.

## Demo Walkthrough

This walkthrough is designed so each step can run independently and leaves visible traces:

- Step 1 writes tenders into PostgreSQL
- Step 2 writes organizations into PostgreSQL
- Step 3 persists search queries into `search_queries`
- Step 4 writes `portal_analysis.csv`
- Step 5 inserts into `tender_documents` and uploads to S3/MinIO

### Step 1: Ingest tenders

```bash
YESTERDAY="$(date -u -d 'yesterday' +%F)"
python -m src.cli.main ingest --start-date "$YESTERDAY" --end-date "$YESTERDAY" --demo-data
```

Verify:

```bash
python -m src.cli.main status
```

Expected:
- `Tenders:` and `Issuers:` are > 0
- `Documents:` is typically 0 before downloading
- The command prints `Loading deterministic demo dataset...` and a `✓ Demo load completed: ...` summary

### Step 2: Extract organizations

```bash
YESTERDAY="$(date -u -d 'yesterday' +%F)"
python -m src.cli.main extract-orgs --start-date "$YESTERDAY" --end-date "$YESTERDAY"
```

Verify organizations increased:

```bash
python -m src.cli.main status
```

Expected:
- In demo mode, organizations may already exist (created alongside the offline tenders load).
- If ANAC is blocked, you may see `Organizations:` unchanged without a crash (known limitation)

### Step 3: Run search queries

Run one or more searches:

```bash
python -m src.cli.main search --query "road maintenance and infrastructure services" --contract-type services --limit 5
python -m src.cli.main search --query "IT equipment" --min-value 100000 --limit 5
```

Verify persistence:

```sql
SELECT COUNT(*) FROM search_queries;
```

### Step 4: Analyze document portals

```bash
python -m src.cli.main analyze-portals --output portal_analysis.csv
```

The output CSV is always created and has header:

```text
portal_domain,tender_count
```

### Step 5: Download documents

```bash
python -m src.cli.main download-documents --portal-analysis-file portal_analysis.csv --limit 10
```

Verify:

```sql
SELECT COUNT(*) FROM tender_documents;
```

Expected:
- If document links exist (in demo mode we backfill a stable portal URL) and downloads succeed: `tender_documents` count increases
- Logs include `Document download summary for <portal>:` with `uploaded=<n>` and `failures=<m>`
- If S3 env vars are not configured: downloaded files are stored locally under `.tmp_storage/tenders/`
- If downloads fail or document URLs are missing: the command still completes, but `tender_documents` may remain 0

## Example Search Queries

Use these as realistic demo queries:

```bash
python -m src.cli.main search --query "road maintenance and infrastructure services" --contract-type services --limit 5
python -m src.cli.main search --query "IT equipment computers and technology supplies" --min-value 100000 --limit 5
python -m src.cli.main search --query "building renovation construction works" --contract-type works --limit 5
python -m src.cli.main search --query "digital transformation consulting services" --eu-funded true --limit 5
python -m src.cli.main search --query "renewable energy solar panels installation" --limit 5
```

## System Architecture

High-level components:

- Ingestion layer (`src/ingestion`)
  - ANAC OCDS client -> enrich -> store in `tenders`
  - backfills missing portal info for downstream portal analysis
- Organization extraction (`src/organizations`)
  - reads tender participants -> normalizes tax IDs -> stores `organizations` + `tender_participants`
- Search layer (`src/search`)
  - hybrid search: structured filtering + vector similarity via pgvector
- Document portal analysis (`src/documents/analyzer.py`)
  - normalizes `tenders.document_portal_url` domains and exports `portal_analysis.csv`
- Document storage and download (`src/documents/downloader.py`)
  - downloads content (PDF/HTML)
  - uploads to MinIO/S3 bucket (`tenders`)
  - inserts metadata into `tender_documents`
- Scheduled jobs + UI
  - `src/scheduler` + `src/web/server.py` manage job definitions and UI control

## Data Model

Main tables:

- `tenders`
  - Identifies each tender (`tender_id`)
  - Stores embeddings and portal URL fields
- `organizations`
  - Deduplicated by normalized tax ID / identity
- `tender_participants`
  - Many-to-many relationship between tenders and organizations
- `tender_documents`
  - One row per tender document downloaded
  - Tracks `storage_path`, `source_url`, `file_type`, and `file_name`
- `search_queries`
  - One row per search request made from the API or CLI (persisted even for empty results)

## Portal Analysis (CSV)

`analyze-portals` produces `portal_analysis.csv`:

- Column 1: `portal_domain` (normalized: lowercase, `www.` stripped)
- Column 2: `tender_count`

The analyzer always writes the CSV header, even when there are no tenders with valid portal URLs.

## Document Storage (MinIO / S3)

Documents are downloaded from each tender’s `document_portal_url`:

- If the URL is missing, the downloader falls back to the tender URL
- File type detection:
  - detects PDF via response content-type or `%PDF` magic bytes
  - otherwise stores the HTML page
- Upload:
  - uploads to the configured S3 bucket
  - if S3 env vars are missing, stores locally under `.tmp_storage/`
  - `storage_path` format is `tenders/<file_name>`

## Known Limitations

- External ingestion may be affected by WAF/rate limiting on ANAC endpoints
- For a reliable offline demo, use `ingest --demo-data` (loads deterministic CSV dumps and backfills portal URLs so document download can still run)
- Some tenders may contain incomplete OCDS payloads; the system uses fallback logic to ensure portal analysis remains possible
- Document download currently targets one top portal domain per run (as selected from `portal_analysis.csv`)
- HTML-to-PDF conversion is not performed (HTML is stored when PDF is not available)
- Scheduled jobs are best-effort and designed to avoid concurrent runs per job name
- If `OPENAI_API_KEY` is not set, the system still runs using:
  - title-based summaries
  - deterministic pseudo-embeddings (search works, but relevance may be lower)
- If S3/MinIO variables are not set, downloaded documents are stored locally under `.tmp_storage/` instead of being uploaded

## Design Decisions

- Single PostgreSQL database with pgvector for simplicity and operational ease
- Idempotency:
  - ingestion skips existing `tender_id`s
  - document download skips already present `tender_id` rows in `tender_documents`
- Fallback URL logic:
  - ensures `document_portal_url` and portal domain analysis remain robust even with missing OCDS fields
- Demo-first observability:
  - CLI and API persist searches (`search_queries`) and expose accurate dashboard metrics

## License

MIT

