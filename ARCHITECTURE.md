# System Architecture - Italian Tender Intelligence MVP

## Overview

Clean, minimal MVP for ingesting Italian public tenders, enriching with AI, and enabling intelligent search/matching.

## Architecture Layers

### 1. Ingestion Layer

**Components:**
- `ANACClient`: Fetches tenders from ANAC API (falls back to mock data)
- `TenderEnrichment`: Generates summaries and embeddings via OpenAI
- `IngestionPipeline`: Orchestrates fetch → enrich → store

**Flow:**
```
ANAC API → Client → Parser → Enrichment (OpenAI) → Database
   ↓ (fallback)
Mock Data
```

**Key Decisions:**
- API-first with graceful fallback to mock data
- Single-pass enrichment (no re-processing)
- Batch processing (no streaming for MVP)

### 2. Storage Layer

**Database:** PostgreSQL 14+ with pgvector extension

**Tables:**
- `issuers`: Contracting authorities
- `tenders`: Main entity with embeddings
- `organizations`: Bidders (deduplicated by tax_id)
- `tender_participants`: Many-to-many relationship
- `documents`: Document metadata and storage URLs
- `search_queries`: Demo search tracking

**Indexes:**
- Vector index (ivfflat) on `tenders.embedding`
- GIN indexes on array fields (cpv_codes, nuts_codes)
- BTree indexes on dates and foreign keys

### 3. Search Layer

**Components:**
- `TenderFilter`: Structured SQL filtering (price, date, CPV, NUTS, etc.)
- `SemanticSearch`: Pure vector similarity search
- `HybridSearch`: Combines filters + vector ranking

**Search Strategy:**
```
Query Text → Generate Embedding
     ↓
Apply Filters (SQL WHERE) → Candidate Set
     ↓
Rank by Vector Similarity (cosine distance)
     ↓
Return Top N Results
```

**Simplifications:**
- No BM25 keyword scoring
- No learned ranking models
- Simple weighted combination (filters narrow, vectors rank)

### 4. Organization Matching

**Approach:** Integrated into search (not separate scoring)

Organizations search for relevant tenders using:
- Natural language queries
- Structured filters (location, value, type)
- Vector similarity for relevance

**Demo:** 5 pre-defined queries per organization showcase matching capability

### 5. Document Pipeline

**Components:**
- `DocumentPortalAnalyzer`: Counts tenders per portal → CSV
- `DocumentDownloader`: Downloads from ONE portal, stubs for others

**Implementation:**
- Analyze all portals (domain extraction from URLs)
- Implement download for top portal only
- Create stub records for other portals

## Data Flow

### Ingestion Flow
```
1. Cron triggers daily (2 AM)
2. ANACClient.fetch_tenders(days_back=1)
   → Try API
   → Fallback to mock if unavailable
3. For each tender:
   a. Check if exists (skip if yes)
   b. Get or create issuer
   c. Generate summary (OpenAI gpt-4o-mini)
   d. Build searchable_text
   e. Generate embedding (OpenAI text-embedding-3-small)
   f. Insert tender
4. OrganizationExtractor.extract_from_tenders()
   → Deduplicate by tax_id
   → Create tender_participants records
```

### Search Flow
```
1. User submits query + optional filters
2. HybridSearch.search(query, filters)
   a. Generate query embedding
   b. Build SQL query with filters
   c. Add vector similarity ranking
   d. Execute and return top N
3. Format results with scores
```

### Document Flow
```
1. Analyze portals
   → Group by domain
   → Count tenders per portal
   → Output CSV
2. Download from top portal
   → Fetch tender list for portal
   → Download documents (or create stubs)
   → Store in S3/local
   → Record in documents table
```

## Technology Stack

**Backend:**
- Python 3.9+
- SQLAlchemy (ORM)
- psycopg2 (PostgreSQL driver)
- pgvector (vector extension)

**AI/ML:**
- OpenAI API (gpt-4o-mini for summaries)
- OpenAI Embeddings (text-embedding-3-small, 1536-dim)

**CLI:**
- Click (command-line interface)

**Storage:**
- PostgreSQL 14+ (primary database)
- S3/MinIO (document storage, optional)

## Key Design Decisions

### 1. Single Database (Postgres + pgvector)
**Why:** Simpler operations, fewer moving parts, good enough for MVP scale
**Trade-off:** Slightly slower than dedicated vector DB, but easier to manage

### 2. OpenAI API (not local models)
**Why:** Better quality, minimal setup, low cost (~$0.07/1K tenders)
**Trade-off:** External dependency, but cost is negligible

### 3. Mock Data Fallback
**Why:** API may be blocked, demonstrates architecture without live data
**Trade-off:** Demo uses fake data, but system is production-ready

### 4. Hybrid Search (filters + vectors, no BM25)
**Why:** Simpler, vectors handle semantic matching well
**Trade-off:** No keyword boosting, but sufficient for MVP

### 5. One Portal Download
**Why:** Each portal has different structure, custom scrapers needed
**Trade-off:** Incomplete coverage, but demonstrates approach

### 6. CLI-First
**Why:** Faster to build, easier to test, scriptable
**Trade-off:** Less user-friendly, but meets requirements

### 7. Synchronous Processing
**Why:** Daily cron is sufficient, no need for async complexity
**Trade-off:** Not real-time, but acceptable for MVP

## Scalability Considerations

**Current Scale:** Handles ~1000 tenders comfortably

**Future Scaling:**
- Partition tenders by publication_date
- Add Redis caching for search results
- Implement async processing with Celery
- Use batch OpenAI API for >100 tenders
- Separate read/write databases
- Add CDN for document delivery

## Cost Analysis

**Per 1000 Tenders:**
- Summaries: $0.05 (gpt-4o-mini, ~100 tokens avg)
- Embeddings: $0.02 (text-embedding-3-small)
- **Total: $0.07**

**Monthly (assuming 500 new tenders):**
- AI costs: ~$0.35/month
- Database: Free (self-hosted) or ~$10/month (managed)
- Storage: Negligible for metadata only

## Security Considerations (Future)

**MVP has no authentication** - intentional simplification

**Production would need:**
- User authentication (JWT tokens)
- API rate limiting
- SQL injection protection (SQLAlchemy handles this)
- Input validation and sanitization
- HTTPS for all endpoints
- Encrypted API keys in environment

## Monitoring & Observability (Future)

**MVP has basic logging only**

**Production would add:**
- Prometheus metrics (ingestion rate, search latency)
- Grafana dashboards
- Error tracking (Sentry)
- Query performance monitoring
- Vector index health checks

## Testing Strategy (Future)

**MVP has no automated tests** - manual CLI testing only

**Production would include:**
- Unit tests (pytest)
- Integration tests (database operations)
- End-to-end tests (full pipeline)
- Load tests (search performance)
- Mock API responses for CI/CD

## Deployment

**MVP:** Single server deployment
- PostgreSQL on same server
- Python app runs via cron
- CLI for manual operations

**Production:** Container-based deployment
- Docker containers for app
- Managed PostgreSQL (AWS RDS, etc.)
- Kubernetes for orchestration
- CI/CD pipeline (GitHub Actions)

## Explicit Simplifications

What's **NOT** implemented (intentionally):

1. ❌ UI scraping for missing fields
2. ❌ Retry logic for API failures
3. ❌ Pagination/streaming ingestion
4. ❌ Document content parsing
5. ❌ Multi-portal downloads
6. ❌ User authentication
7. ❌ Rate limiting
8. ❌ Monitoring/alerting
9. ❌ Automated testing
10. ❌ Async processing
11. ❌ Result caching
12. ❌ BM25 keyword scoring
13. ❌ Query expansion
14. ❌ Fuzzy duplicate detection
15. ❌ Error recovery/rollback

What's **INCLUDED** (MVP core):

1. ✅ Database schema with pgvector
2. ✅ ANAC API client with mock fallback
3. ✅ OpenAI enrichment (summary + embedding)
4. ✅ Organization extraction and deduplication
5. ✅ Hybrid search (filters + vectors)
6. ✅ Portal analysis (CSV output)
7. ✅ Document download (one portal)
8. ✅ Complete CLI interface
9. ✅ Cron job setup
10. ✅ Comprehensive documentation

## File Organization

```
tender/
├── src/                          # Source code
│   ├── config.py                 # Configuration
│   ├── database/                 # Data layer
│   ├── ingestion/                # Ingestion pipeline
│   ├── organizations/            # Organization extraction
│   ├── search/                   # Search system
│   ├── documents/                # Document pipeline
│   └── cli/                      # CLI interface
├── scripts/                      # Utility scripts
├── data/                         # Mock data
├── requirements.txt              # Dependencies
├── .env.example                  # Environment template
├── README.md                     # User guide
└── ARCHITECTURE.md               # This file
```

## Development Workflow

1. **Setup:** Install deps, configure .env, init database
2. **Develop:** Edit source files, test via CLI
3. **Test:** Run commands manually, verify output
4. **Deploy:** Set up cron, monitor logs

## Future Enhancements

**Phase 2 (Production-Ready):**
- Real-time API integration with retries
- Web UI dashboard
- User authentication
- Async processing
- Comprehensive testing

**Phase 3 (Advanced Features):**
- Multi-portal scraping
- Document content RAG
- ML-based recommendations
- Email notifications
- Advanced analytics

**Phase 4 (Scale):**
- Multi-tenant support
- Horizontal scaling
- Global CDN
- Advanced monitoring
- SLA guarantees
