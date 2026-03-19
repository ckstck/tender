#!/usr/bin/env python3
"""
Database initialization script.
Creates all tables and enables pgvector extension.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import engine, Base
from src.database.models import Issuer, Tender, Organization, TenderParticipant, Document, SearchQuery
from sqlalchemy import text

def init_database():
    """Initialize database schema and extensions"""
    print("Initializing database...")
    
    try:
        with engine.connect() as conn:
            print("Enabling pgvector extension...")
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        
        print("Creating tables...")
        Base.metadata.create_all(bind=engine)
        
        print("\n✓ Database initialized successfully")
        print("\nTables created:")
        print("  - issuers")
        print("  - tenders")
        print("  - organizations")
        print("  - tender_participants")
        print("  - documents")
        print("  - search_queries")
        
    except Exception as e:
        print(f"\n❌ Error initializing database: {e}")
        sys.exit(1)

if __name__ == '__main__':
    init_database()
