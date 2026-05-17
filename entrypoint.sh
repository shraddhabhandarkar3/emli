#!/usr/bin/env bash
# entrypoint.sh — runs inside the pipeline Docker container
# Applies DB migrations then executes the full pipeline once.
set -e

echo "── Applying DB migrations ─────────────────────────────"
python -m alembic upgrade head

echo "── Step 1/3: Fetching & classifying emails ────────────"
python -m services.ingestion.run_fetch

echo "── Step 2/3: Running ETL ──────────────────────────────"
python -m services.etl.run_etl

echo "── Step 3/3: Syncing to Notion ────────────────────────"
python -m services.notion_sync.run_sync

echo "✓ Pipeline complete!"
