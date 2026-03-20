-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Issuers (contracting authorities)
CREATE TABLE IF NOT EXISTS issuers (
    id SERIAL PRIMARY KEY,
    issuer_id VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(500) NOT NULL,
    contact_email VARCHAR(255),
    contact_phone VARCHAR(50),
    address TEXT,
    city VARCHAR(255),
    region VARCHAR(255),
    nuts_code VARCHAR(10),
    organization_type VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tenders
CREATE TABLE IF NOT EXISTS tenders (
    id SERIAL PRIMARY KEY,
    tender_id VARCHAR(255) UNIQUE NOT NULL,
    title TEXT NOT NULL,
    issuer_id INTEGER REFERENCES issuers(id),
    
    estimated_value DECIMAL(15, 2),
    currency VARCHAR(3) DEFAULT 'EUR',
    award_criteria JSONB,
    
    publication_date DATE,
    submission_deadline TIMESTAMP,
    execution_start_date DATE,
    execution_end_date DATE,
    
    execution_location TEXT,
    nuts_codes VARCHAR(20)[],
    
    cpv_codes VARCHAR(20)[],
    contract_type VARCHAR(50),
    eu_funded BOOLEAN,
    renewable BOOLEAN,
    
    has_lots BOOLEAN DEFAULT FALSE,
    lots_data JSONB,
    
    tender_url TEXT,
    document_portal_url TEXT,
    source_platform VARCHAR(20),
    
    summary VARCHAR(240),
    searchable_text TEXT,
    embedding vector(1536),
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Organizations (bidders/participants)
CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    tax_id VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(500) NOT NULL,
    normalized_name TEXT,
    source_count INTEGER DEFAULT 0,
    country VARCHAR(2),
    city VARCHAR(255),
    region VARCHAR(255),
    industry VARCHAR(255),
    size VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tender participants (many-to-many)
CREATE TABLE IF NOT EXISTS tender_participants (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
    role VARCHAR(50),
    awarded BOOLEAN DEFAULT FALSE,
    award_value DECIMAL(15, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tender_id, organization_id, role)
);

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
    document_type VARCHAR(100),
    filename VARCHAR(500),
    file_size BIGINT,
    storage_url TEXT,
    portal_url TEXT,
    portal_name VARCHAR(255),
    downloaded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Stored documents for tenders (production-safe storage metadata)
-- Intended for `download-documents` pipeline (MinIO/S3 upload).
CREATE TABLE IF NOT EXISTS tender_documents (
    id SERIAL PRIMARY KEY,
    tender_id INTEGER REFERENCES tenders(id) ON DELETE CASCADE,
    file_name TEXT,
    storage_path TEXT NOT NULL,
    source_url TEXT NOT NULL,
    file_type VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tender_id)
);

-- Search queries (for demo tracking)
CREATE TABLE IF NOT EXISTS search_queries (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    query_text TEXT,
    filters JSONB,
    results JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scheduled jobs configuration (UI-managed)
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id SERIAL PRIMARY KEY,
    job_name VARCHAR(100) UNIQUE NOT NULL,
    enabled BOOLEAN DEFAULT TRUE NOT NULL,
    schedule_time VARCHAR(5) NOT NULL,
    last_run_at TIMESTAMP,
    last_status VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Job runs (for UI observability/log tail)
CREATE TABLE IF NOT EXISTS job_runs (
    id SERIAL PRIMARY KEY,
    job_name VARCHAR(100) NOT NULL,
    scheduled_for TIMESTAMP,
    status VARCHAR(20) NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    exit_code INTEGER,
    log_tail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(job_name, scheduled_for)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tenders_publication_date ON tenders(publication_date);
CREATE INDEX IF NOT EXISTS idx_tenders_submission_deadline ON tenders(submission_deadline);
CREATE INDEX IF NOT EXISTS idx_tenders_cpv_codes ON tenders USING GIN(cpv_codes);
CREATE INDEX IF NOT EXISTS idx_tenders_nuts_codes ON tenders USING GIN(nuts_codes);
CREATE INDEX IF NOT EXISTS idx_tenders_embedding ON tenders USING ivfflat(embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_organizations_tax_id ON organizations(tax_id);
CREATE INDEX IF NOT EXISTS idx_tender_participants_tender ON tender_participants(tender_id);
CREATE INDEX IF NOT EXISTS idx_tender_participants_org ON tender_participants(organization_id);
