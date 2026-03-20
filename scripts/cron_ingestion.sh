#!/bin/bash
# Daily ingestion cron job
# Add to crontab: 0 2 * * * /path/to/tender/scripts/cron_ingestion.sh

cd "$(dirname "$0")/.."
source venv/bin/activate

# Run ingestion for the previous day (UTC).
YESTERDAY="$(date -u -d 'yesterday' +%F)"
python -m src.cli.main ingest --start-date "$YESTERDAY" --end-date "$YESTERDAY"

# Optional: extract new organizations
python -m src.cli.main extract-orgs --start-date "$YESTERDAY" --end-date "$YESTERDAY"
