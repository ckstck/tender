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

-- Search queries (for demo tracking)
CREATE TABLE IF NOT EXISTS search_queries (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    query_text TEXT,
    filters JSONB,
    results JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
