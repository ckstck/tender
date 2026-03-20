#!/usr/bin/env python3
"""
Apply organizations Part 2 migration (normalized_name, source_count, unique index).
Uses DATABASE_URL from .env via Config.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.database.connection import engine


STATEMENTS = [
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS normalized_name TEXT",
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS source_count INTEGER DEFAULT 0",
    """UPDATE organizations
SET normalized_name = UPPER(TRIM(name))
WHERE normalized_name IS NULL""",
    "UPDATE organizations SET source_count = 0 WHERE source_count IS NULL",
    "ALTER TABLE organizations ALTER COLUMN source_count SET DEFAULT 0",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_org_tax_id ON organizations (tax_id)",
]


def main() -> None:
    print("Running organizations Part 2 migration...")
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
    print("✓ Migration completed.")


if __name__ == "__main__":
    main()
