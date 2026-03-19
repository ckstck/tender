#!/bin/bash
# Daily ingestion cron job
# Add to crontab: 0 2 * * * /path/to/tender/scripts/cron_ingestion.sh

cd "$(dirname "$0")/.."
source venv/bin/activate

python -m src.cli.main ingest --days 1

# Optional: extract new organizations
python -m src.cli.main extract-orgs --days 1
