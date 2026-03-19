#!/usr/bin/env python3
"""
Requirements validation script.
Checks that all challenge requirements are met.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import get_db, engine
from src.database.models import Tender, Issuer, Organization, Document, SearchQuery
from sqlalchemy import inspect, text

def check_database_schema():
    """Verify all required tables and columns exist"""
    print("🔍 Checking database schema...")
    
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    required_tables = ['issuers', 'tenders', 'organizations', 'tender_participants', 'documents', 'search_queries']
    
    for table in required_tables:
        if table in tables:
            print(f"  ✅ Table '{table}' exists")
        else:
            print(f"  ❌ Table '{table}' missing")
            return False
    
    # Check tender columns
    tender_columns = [col['name'] for col in inspector.get_columns('tenders')]
    required_tender_fields = [
        'tender_id', 'title', 'cpv_codes', 'estimated_value', 'award_criteria',
        'publication_date', 'submission_deadline', 'execution_start_date', 'execution_end_date',
        'execution_location', 'nuts_codes', 'contract_type', 'eu_funded', 'renewable',
        'has_lots', 'lots_data', 'tender_url', 'document_portal_url',
        'summary', 'searchable_text', 'embedding'
    ]
    
    for field in required_tender_fields:
        if field in tender_columns:
            print(f"  ✅ Tender field '{field}' exists")
        else:
            print(f"  ❌ Tender field '{field}' missing")
            return False
    
    return True

def check_pgvector_extension():
    """Verify pgvector extension is installed"""
    print("\n🔍 Checking pgvector extension...")
    
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM pg_extension WHERE extname = 'vector'"))
        if result.fetchone():
            print("  ✅ pgvector extension installed")
            return True
        else:
            print("  ❌ pgvector extension not installed")
            return False

def check_data_exists():
    """Check if system has data"""
    print("\n🔍 Checking data...")
    
    with get_db() as db:
        tender_count = db.query(Tender).count()
        org_count = db.query(Organization).count()
        issuer_count = db.query(Issuer).count()
        doc_count = db.query(Document).count()
        search_count = db.query(SearchQuery).count()
        
        print(f"  📊 Tenders: {tender_count}")
        print(f"  📊 Organizations: {org_count}")
        print(f"  📊 Issuers: {issuer_count}")
        print(f"  📊 Documents: {doc_count}")
        print(f"  📊 Search Queries: {search_count}")
        
        if tender_count > 0:
            print("  ✅ System has data")
            return True
        else:
            print("  ⚠️  No tenders in database (run ingestion)")
            return False

def check_embeddings():
    """Check if tenders have embeddings"""
    print("\n🔍 Checking AI enrichment...")
    
    with get_db() as db:
        total = db.query(Tender).count()
        with_embeddings = db.query(Tender).filter(Tender.embedding.isnot(None)).count()
        with_summary = db.query(Tender).filter(Tender.summary.isnot(None)).count()
        
        print(f"  📊 Tenders with embeddings: {with_embeddings}/{total}")
        print(f"  📊 Tenders with summaries: {with_summary}/{total}")
        
        if total > 0 and with_embeddings == total:
            print("  ✅ All tenders have embeddings")
            return True
        elif total > 0:
            print(f"  ⚠️  {total - with_embeddings} tenders missing embeddings")
            return False
        else:
            return True

def check_files_exist():
    """Check if required files exist"""
    print("\n🔍 Checking required files...")
    
    project_root = Path(__file__).parent.parent
    
    required_files = [
        'requirements.txt',
        'README.md',
        'ARCHITECTURE.md',
        'REQUIREMENTS_VALIDATION.md',
        'src/config.py',
        'src/database/models.py',
        'src/database/connection.py',
        'src/ingestion/pipeline.py',
        'src/ingestion/enrichment.py',
        'src/organizations/extractor.py',
        'src/search/hybrid.py',
        'src/documents/downloader.py',
        'src/documents/storage.py',
        'src/documents/portal_scrapers/anac_scraper.py',
        'src/cli/main.py',
        'scripts/init_db.py',
        'scripts/cron_ingestion.sh',
        'scripts/cron_documents.sh',
        'data/mock_tenders.json',
        'docker-compose.minio.yml'
    ]
    
    all_exist = True
    for file_path in required_files:
        full_path = project_root / file_path
        if full_path.exists():
            print(f"  ✅ {file_path}")
        else:
            print(f"  ❌ {file_path} missing")
            all_exist = False
    
    return all_exist

def main():
    """Run all validation checks"""
    print("=" * 70)
    print("REQUIREMENTS VALIDATION")
    print("=" * 70)
    
    checks = [
        ("Database Schema", check_database_schema),
        ("pgvector Extension", check_pgvector_extension),
        ("Required Files", check_files_exist),
        ("Data Exists", check_data_exists),
        ("AI Enrichment", check_embeddings),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ Error in {name}: {e}")
            results.append((name, False))
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n🎉 ALL REQUIREMENTS VALIDATED")
        return 0
    else:
        print("\n⚠️  SOME REQUIREMENTS NOT MET")
        print("\nNext steps:")
        print("  1. Run: python scripts/init_db.py")
        print("  2. Run: python -m src.cli.main ingest --days 30")
        print("  3. Run: python -m src.cli.main extract-orgs")
        return 1

if __name__ == '__main__':
    sys.exit(main())
