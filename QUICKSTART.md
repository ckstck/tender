# Quick Start Guide

Get the Italian Tender Intelligence System running in 5 minutes.

## Prerequisites Check

```bash
# Check Python version (need 3.9+)
python3 --version

# Check PostgreSQL (need 14+)
psql --version
```

## Step 1: Environment Setup

```bash
cd /root/bluestar/tender

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Step 2: Database Setup

```bash
# Create database
sudo -u postgres createdb tender_db

# Or if you have postgres user password:
createdb tender_db

# Initialize schema and pgvector
./venv/bin/python scripts/init_db.py
```

Expected output:
```
Initializing database...
Enabling pgvector extension...
Creating tables...

✓ Database initialized successfully
```

## Step 3: Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit configuration
nano .env
```

**Minimum required:**
```env
DATABASE_URL=postgresql://localhost/tender_db
OPENAI_API_KEY=sk-your-key-here
```

**Optional (for document storage):**
```env
S3_ENDPOINT=https://s3.amazonaws.com
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_BUCKET=tenders
```

## Step 4: Run First Ingestion

```bash
# Ingest tenders (uses mock data if API unavailable)
python -m src.cli.main ingest --start-date 2025-08-01 --end-date 2025-08-30

# Extract organizations
python -m src.cli.main extract-orgs --start-date 2025-08-01 --end-date 2025-08-30

# Check status
python -m src.cli.main status
```

Expected output:
```
📈 System Status

  Tenders: 5
  Organizations: 9
  Issuers: 5
  Documents: 0
  Search Queries: 0
```

## Step 5: Try Searching

```bash
# Basic search
python -m src.cli.main search --query "road maintenance"

# Search with filters
python -m src.cli.main search \
  --query "IT equipment" \
  --min-value 100000 \
  --contract-type supplies

# List organizations
python -m src.cli.main list-orgs

# Run demo searches (use org ID from list)
python -m src.cli.main demo-search --org-id 1
```

## Step 6: Document Analysis

```bash
# Analyze portals
python -m src.cli.main analyze-portals --output portal_analysis.csv

# Download documents from the top portal found in portal_analysis.csv
python -m src.cli.main download-documents \
  --portal-analysis-file portal_analysis.csv \
  --limit 5
```

## Troubleshooting

### "No module named 'src'"
```bash
# Make sure you're in the project root
cd /root/bluestar/tender

# Activate virtual environment
source venv/bin/activate
```

### "could not connect to server"
```bash
# Start PostgreSQL
sudo systemctl start postgresql

# Check it's running
sudo systemctl status postgresql
```

### "pgvector extension not found"
```bash
# Install pgvector
sudo apt-get install postgresql-14-pgvector

# Or build from source
git clone https://github.com/pgvector/pgvector.git
cd pgvector
make
sudo make install
```

### "OpenAI API error"
```bash
# Verify API key is set
grep OPENAI_API_KEY .env

# System will fall back to zero vectors if no key
# Search will still work but with lower quality
```

## Next Steps

1. **Set up daily cron:**
   ```bash
   chmod +x scripts/cron_ingestion.sh
   crontab -e
   # Add: 0 2 * * * /root/bluestar/tender/scripts/cron_ingestion.sh
   ```

2. **Explore the data:**
   ```bash
   psql tender_db
   SELECT tender_id, title, estimated_value FROM tenders LIMIT 5;
   ```

3. **Read the docs:**
   - `README.md` - Full documentation
   - `ARCHITECTURE.md` - System design details

## Common Commands

```bash
# Daily operations (example: previous day, UTC)
YESTERDAY="$(date -u -d 'yesterday' +%F)"
python -m src.cli.main ingest --start-date "$YESTERDAY" --end-date "$YESTERDAY"
python -m src.cli.main extract-orgs --start-date "$YESTERDAY" --end-date "$YESTERDAY"
python -m src.cli.main status

# Search examples
python -m src.cli.main search --query "construction works" --nuts ITC11
python -m src.cli.main search --query "digital services" --eu-funded true

# Analysis
python -m src.cli.main analyze-portals
python -m src.cli.main list-orgs
```

## Success Criteria

You should now have:
- ✅ Database initialized with pgvector
- ✅ 5 mock tenders ingested
- ✅ 9 organizations extracted
- ✅ Search working with semantic matching
- ✅ Portal analysis generating CSV

## Getting Help

- Check `README.md` for detailed documentation
- Review `ARCHITECTURE.md` for system design
- Examine source code comments for implementation details
