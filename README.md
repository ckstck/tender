# Italian Tender Intelligence System

End-to-end MVP system for ingesting, enriching, and matching Italian public procurement tenders.

## Features

- **Part 1**: Automated tender ingestion from ANAC with AI enrichment (summaries + embeddings)
- **Part 2**: Organization extraction and deduplication from tender participants
- **Part 3**: Hybrid search (structured filters + semantic vector search)
- **Part 4**: Document portal analysis and download pipeline
- **Part 5**: Complete CLI interface

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        INGESTION LAYER                          │
│  ┌──────────────┐      ┌─────────────┐      ┌──────────────┐  │
│  │  ANAC API    │─────▶│  Extractor  │─────▶│   OpenAI     │  │
│  │  (OCDS JSON) │      │  + Parser   │      │  Enrichment  │  │
│  └──────────────┘      └─────────────┘      └──────────────┘  │
└───────────────────────────────────────────────────────────────┬─┘
                                │                                 │
                                ▼                                 │
┌─────────────────────────────────────────────────────────────────┘
│                        STORAGE LAYER                            │
│              PostgreSQL + pgvector                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐       │
│  │ Tenders  │  │  Orgs    │  │ Issuers  │  │  Docs   │       │
│  │ +vectors │  │          │  │          │  │         │       │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘       │
└───────────────────────────────────────────────────────────────┬─┘
                                │                                 │
                                ▼                                 │
┌─────────────────────────────────────────────────────────────────┘
│                        SEARCH LAYER                             │
│  ┌──────────────┐      ┌─────────────┐      ┌──────────────┐  │
│  │  Structured  │      │   Vector    │      │    Hybrid    │  │
│  │   Filters    │─────▶│   Search    │─────▶│   Ranking    │  │
│  │  (SQL WHERE) │      │  (pgvector) │      │  (weighted)  │  │
│  └──────────────┘      └─────────────┘      └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Production-Ready Prerequisites
- Python 3.9+
- PostgreSQL 14+ with the `pgvector` extension available (extension type is `vector`)
- An OpenAI API key (optional but recommended for good summaries and search ranking)

### 1) Create the environment
From the `tender/` directory (this folder):
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment (do not commit secrets)
Create a file named `.env` in `tender/`:
```bash
# Database connection (recommended: local unix socket)
DATABASE_URL=postgresql:///tender_db

# Required for best search quality. If omitted, the system still runs but
# summaries/embeddings fall back (search similarity will be lower quality).
OPENAI_API_KEY=sk-your-key-here

# Optional: ANAC API key (if your ANAC access needs it)
ANAC_API_KEY=

# Optional: logging
LOG_LEVEL=INFO

# Optional: document storage (only needed if you use the S3 download pipeline)
S3_ENDPOINT=https://s3.amazonaws.com
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET=tender-documents
```

### 3) Database setup (idempotent)
The `scripts/init_db.py` script:
- enables the `vector` extension
- creates all tables (safe to run multiple times)

```bash
# 1) Install PostgreSQL server + client + build tooling (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib postgresql-server-dev-14

# 2) Install pgvector:
#    Option A (if available as a package on your distro)
sudo apt-get install -y postgresql-14-pgvector

#    Option B (always works): build from source
#    git clone https://github.com/pgvector/pgvector.git
#    cd pgvector && make && sudo make install

# 3) Create database (run as postgres user; safe if it already exists)
sudo -u postgres createdb tender_db 2>/dev/null || echo "Database already exists"

# 4) Initialize schema (run inside venv; loads .env automatically)
./venv/bin/python scripts/init_db.py
```

### 4) First run
```bash
# Ingest tenders (uses mock data if ANAC API fetch fails)
./venv/bin/python -m src.cli.main ingest --days 30

# Extract organizations from tender participants
./venv/bin/python -m src.cli.main extract-orgs --days 30

# Check database statistics
./venv/bin/python -m src.cli.main status

# Try a search
./venv/bin/python -m src.cli.main search --query "road maintenance services" --limit 5
```

### Re-running safely (important for production)
- `ingest` is idempotent by `tender_id` (existing tenders are skipped).
- If you change prompts/models or want to regenerate embeddings/summaries, you must explicitly reprocess data (this repo currently does not expose a “force re-embed” flag).

## Usage

All commands assume your virtual environment is active (`source venv/bin/activate`). If you prefer, replace `python` with `./venv/bin/python`.

### CLI Commands

#### Ingestion

```bash
# Ingest tenders from last 30 days
python -m src.cli.main ingest --days 30

# Extract organizations
python -m src.cli.main extract-orgs --days 30
```

#### Search

```bash
# Basic search
python -m src.cli.main search --query "road maintenance services"

# Search with filters
python -m src.cli.main search \
  --query "IT equipment" \
  --min-value 100000 \
  --max-value 500000 \
  --contract-type supplies

# Search by location
python -m src.cli.main search \
  --query "construction works" \
  --nuts ITC4C

# Search EU-funded tenders
python -m src.cli.main search \
  --query "digital transformation" \
  --eu-funded true
```

#### Demo Searches

```bash
# List organizations
python -m src.cli.main list-orgs

# Run 5 demo searches for an organization
python -m src.cli.main demo-search --org-id 1
```

#### Document Analysis

```bash
# Analyze portal distribution
python -m src.cli.main analyze-portals --output portal_analysis.csv

# Download documents from specific portal
python -m src.cli.main download-docs \
  --portal portale-documenti.comune.milano.it \
  --limit 10
```

#### System Status

```bash
# Show database statistics
python -m src.cli.main status
```

### Web UI (Modern Dashboard)
This repo ships a lightweight web dashboard that wraps the existing CLI actions.

1. Start the server:
```bash
./venv/bin/python -m src.web.server
```

2. Open in your browser:
- http://localhost:8000

3. Actions you can run from the UI:
- `ingest`
- `extract-orgs`
- `demo-search`
- `analyze-portals`
- `download-docs`
- quick `search` with filters
- job monitor + logs

### Cron Setup

For daily automated ingestion:

```bash
# Ensure the cron script is executable
chmod +x scripts/cron_ingestion.sh

# Add to crontab (runs daily at 2 AM)
crontab -e

# Use an absolute path to the repo (script activates the venv and runs the CLI)
0 2 * * * /full/path/to/tender/scripts/cron_ingestion.sh
```

#### Production logging
- The CLI writes structured logs to stdout/stderr.
- In cron, redirect output to a log file (example):
  - `0 2 * * * /full/path/to/tender/scripts/cron_ingestion.sh >> /var/log/tender/cron.log 2>&1`

#### Basic health check
After each scheduled run:
```bash
./venv/bin/python -m src.cli.main status
./venv/bin/python -m src.cli.main search --query "digital transformation consulting services" --limit 3
```

## Data Model

### Core Tables

- **issuers**: Contracting authorities (municipalities, regions, etc.)
- **tenders**: Public tenders with AI-generated summaries and embeddings
- **organizations**: Bidders/participants (deduplicated by tax_id)
- **tender_participants**: Many-to-many relationship between tenders and organizations
- **documents**: Downloaded tender documents with storage URLs
- **search_queries**: Saved searches for demo tracking

### Key Fields

- `embedding` (vector 1536): OpenAI embedding for semantic search
- `searchable_text` (text): Rich text combining all tender metadata
- `summary` (varchar 240): AI-generated concise summary
- `cpv_codes`, `nuts_codes` (arrays): Classification codes for filtering

## Design Decisions

### 1. Postgres + pgvector (not separate vector DB)
- **Why**: Single database, simpler operations, sufficient for MVP scale
- **Trade-off**: Slightly slower than dedicated vector DB, but easier to manage

### 2. OpenAI API (not local models)
- **Why**: Better quality, minimal setup, low cost (~$0.07 per 1000 tenders)
- **Trade-off**: External dependency, but cost is negligible

### 3. Mock data fallback (not robust scraping)
- **Why**: API may be blocked, scraping is fragile and out of scope
- **Trade-off**: Demo uses realistic mock data, but architecture is production-ready

### 4. Hybrid search = filters + vectors (not BM25)
- **Why**: Simpler implementation, vectors handle semantic matching well
- **Trade-off**: No keyword boosting, but sufficient for MVP

### 5. One portal download (not all portals)
- **Why**: Each portal has different structure, would require custom scrapers
- **Trade-off**: Incomplete document coverage, but demonstrates approach

### 6. CLI-first (not web UI)
- **Why**: Faster to build, easier to test, scriptable
- **Trade-off**: Less user-friendly, but meets MVP requirements

## Cost Estimates

Per 1000 tenders:
- **Summaries**: ~$0.05 (gpt-4o-mini, 100 tokens avg)
- **Embeddings**: ~$0.02 (text-embedding-3-small, 1536 dims)
- **Total**: ~$0.07 per 1000 tenders

For 30 days of tenders (~100-500 tenders): **< $0.50/month**

## Project Structure

```
tender/
├── src/
│   ├── config.py                    # Environment configuration
│   ├── database/
│   │   ├── connection.py            # SQLAlchemy engine + session
│   │   ├── models.py                # ORM models
│   │   └── schema.sql               # Raw SQL schema
│   ├── ingestion/
│   │   ├── client.py                # ANAC API client (with mock fallback)
│   │   ├── enrichment.py            # OpenAI: summary + embedding
│   │   └── pipeline.py              # Orchestration
│   ├── organizations/
│   │   └── extractor.py             # Extract orgs from participants
│   ├── search/
│   │   ├── filters.py               # Structured SQL filtering
│   │   ├── semantic.py              # Vector similarity search
│   │   └── hybrid.py                # Combine filters + vectors
│   ├── documents/
│   │   ├── analyzer.py              # Portal analysis → CSV
│   │   └── downloader.py            # Download from ONE portal
│   └── cli/
│       └── main.py                  # Click-based CLI
├── scripts/
│   ├── init_db.py                   # Database initialization
│   └── cron_ingestion.sh            # Daily cron wrapper
├── data/
│   └── mock_tenders.json            # Mock OCDS data
├── requirements.txt
├── .env                       # Local environment config (do not commit)
└── README.md
```

## Limitations (MVP)

This is an MVP with intentional simplifications:

1. **Mock data**: Uses realistic mock data if ANAC API is unavailable
2. **No scraping**: Missing fields from API are stored as NULL
3. **Placeholder downloads**: Document download implemented for one portal only
4. **No authentication**: Public system, no user management
5. **Synchronous**: No async processing (sufficient for daily cron)
6. **Basic error handling**: Fail fast, log errors, continue processing
7. **No caching**: No Redis or result caching
8. **No testing**: Manual CLI testing only

## Future Enhancements

- Real-time API integration with retry logic
- Multi-portal scraping with custom extractors
- Advanced RAG with document content parsing
- Web UI dashboard with React/Next.js
- Email notifications for matching tenders
- ML-based tender recommendations
- Multi-tenant authentication
- Async processing with Celery
- Comprehensive test suite
- Monitoring and alerting

## Troubleshooting

### Database connection errors
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Verify database exists
psql -l | grep tender_db

# Test connection
sudo -u postgres psql -d tender_db -c "SELECT 1;"
```

### pgvector extension not found
```bash
# Install pgvector
sudo apt-get install postgresql-14-pgvector

# Or build from source
git clone https://github.com/pgvector/pgvector.git
cd pgvector
make
sudo make install
```

### OpenAI API errors
```bash
# Verify API key is set
grep OPENAI_API_KEY .env

# Test API key
python -c "import openai; openai.api_key='your-key'; print(openai.models.list())"
```

### No results from search
```bash
# Check if tenders have embeddings
python -m src.cli.main status

# Re-run ingestion if needed
python -m src.cli.main ingest --days 30
```

## License

MIT

## Support

For issues or questions, please check the troubleshooting section or review the code comments in the source files.
