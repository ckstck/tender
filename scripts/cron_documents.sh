#!/bin/bash
# Daily document download cron job
# Add to crontab: 0 3 * * * /home/loki/projects/tender/scripts/cron_documents.sh

# Change to project directory
cd "$(dirname "$0")/.."

# Activate virtual environment
source venv/bin/activate

# Create logs directory if it doesn't exist
mkdir -p logs

# Run document download with auto-detect
echo "$(date): Starting document download" >> logs/document_downloads.log

python -m src.cli.main download-docs --auto-detect --limit 50 >> logs/document_downloads.log 2>&1

# Log completion
echo "$(date): Document download completed" >> logs/document_downloads.log
echo "---" >> logs/document_downloads.log
