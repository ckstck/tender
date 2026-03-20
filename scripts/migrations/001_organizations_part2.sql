-- Part 2: Organizations — schema alignment, backfill, unique tax_id
-- Prefer: ./venv/bin/python scripts/migrate_organizations_part2.py

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS normalized_name TEXT,
  ADD COLUMN IF NOT EXISTS source_count INTEGER DEFAULT 0;

UPDATE organizations
SET normalized_name = UPPER(TRIM(name))
WHERE normalized_name IS NULL;

UPDATE organizations
SET source_count = 0
WHERE source_count IS NULL;

ALTER TABLE organizations
  ALTER COLUMN source_count SET DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_org_tax_id ON organizations (tax_id);
