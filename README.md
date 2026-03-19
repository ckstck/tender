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

### Prerequisites

- Python 3.9+
- PostgreSQL 14+ with pgvector extension
- OpenAI API key

### Installation

```bash
# Clone repository
cd /home/loki/projects/tender

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials:
#   - DATABASE_URL
#   - OPENAI_API_KEY
```

### Database Setup

```bash
# Install PostgreSQL and pgvector (Ubuntu/Debian)
sudo apt-get install postgresql-14 postgresql-14-pgvector

# Create database
sudo -u postgres createdb tender_db

# Initialize schema
python scripts/init_db.py
```

### MinIO Setup (for Document Storage)

```bash
# Start MinIO with Docker Compose
docker-compose -f docker-compose.minio.yml up -d

# MinIO will be available at:
# - API: http://localhost:9000
# - Console: http://localhost:9001
# - Default credentials: minioadmin / minioadmin

# Configure MinIO in .env
echo "MINIO_ENDPOINT=localhost:9000" >> .env
echo "MINIO_ACCESS_KEY=minioadmin" >> .env
echo "MINIO_SECRET_KEY=minioadmin" >> .env
echo "MINIO_BUCKET=tender-documents" >> .env
```

### First Run

```bash
# Ingest tenders (uses mock data if API unavailable)
python -m src.cli.main ingest --days 30

# Extract organizations from participants
python -m src.cli.main extract-orgs --days 30

# Check status
python -m src.cli.main status
```

## Usage

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

### Cron Setup

For daily automated ingestion and document downloads:

```bash
# Make scripts executable
chmod +x scripts/cron_ingestion.sh scripts/cron_documents.sh

# Add to crontab
crontab -e
# Add lines:
0 2 * * * /home/loki/projects/tender/scripts/cron_ingestion.sh
0 3 * * * /home/loki/projects/tender/scripts/cron_documents.sh
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
├── .env.example
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
psql postgresql://localhost/tender_db
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
