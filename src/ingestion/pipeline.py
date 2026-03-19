import logging
from datetime import datetime
from typing import List, Dict
from src.database.connection import get_db
from src.database.models import Tender, Issuer
from src.ingestion.client import ANACClient
from src.ingestion.enrichment import TenderEnrichment
from src.config import Config

logger = logging.getLogger(__name__)

class IngestionPipeline:
    def __init__(self):
        self.client = ANACClient()
        self.enrichment = TenderEnrichment()
    
    def run(self, days_back: int = None):
        """Run the complete ingestion pipeline"""
        if days_back is None:
            days_back = Config.INGESTION_DAYS_BACK
        
        logger.info(f"Starting ingestion for last {days_back} days")
        
        raw_tenders = self.client.fetch_tenders(days_back)
        logger.info(f"Fetched {len(raw_tenders)} tenders")
        
        ingested_count = 0
        skipped_count = 0
        error_count = 0
        
        with get_db() as db:
            for tender_data in raw_tenders:
                try:
                    if self._process_tender(db, tender_data):
                        ingested_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.error(f"Error processing tender {tender_data.get('tender_id')}: {e}")
                    error_count += 1
                    continue
        
        logger.info(f"Ingestion completed: {ingested_count} ingested, {skipped_count} skipped, {error_count} errors")
        return {
            'ingested': ingested_count,
            'skipped': skipped_count,
            'errors': error_count
        }
    
    def _process_tender(self, db, tender_data: Dict) -> bool:
        """Process a single tender. Returns True if ingested, False if skipped."""
        existing = db.query(Tender).filter_by(tender_id=tender_data['tender_id']).first()
        if existing:
            logger.debug(f"Tender {tender_data['tender_id']} already exists, skipping")
            return False
        
        issuer = self._get_or_create_issuer(db, tender_data['issuer'])
        
        summary = self.enrichment.generate_summary(tender_data)
        searchable_text = self.enrichment.generate_searchable_text(tender_data)
        embedding = self.enrichment.generate_embedding(searchable_text)
        
        tender = Tender(
            tender_id=tender_data['tender_id'],
            title=tender_data['title'],
            issuer_id=issuer.id,
            estimated_value=tender_data.get('estimated_value'),
            award_criteria=tender_data.get('award_criteria'),
            publication_date=datetime.fromisoformat(tender_data['publication_date']).date() if tender_data.get('publication_date') else None,
            submission_deadline=datetime.fromisoformat(tender_data['submission_deadline']) if tender_data.get('submission_deadline') else None,
            execution_location=tender_data.get('execution_location'),
            nuts_codes=tender_data.get('nuts_codes', []),
            cpv_codes=tender_data.get('cpv_codes', []),
            contract_type=tender_data.get('contract_type'),
            eu_funded=tender_data.get('eu_funded'),
            renewable=tender_data.get('renewable'),
            has_lots=tender_data.get('has_lots', False),
            lots_data=tender_data.get('lots_data'),
            tender_url=tender_data.get('tender_url'),
            document_portal_url=tender_data.get('document_portal_url'),
            summary=summary,
            searchable_text=searchable_text,
            embedding=embedding
        )
        
        db.add(tender)
        db.flush()
        
        logger.info(f"Ingested tender: {tender_data['tender_id']}")
        return True
    
    def _get_or_create_issuer(self, db, issuer_data: Dict) -> Issuer:
        """Get or create issuer"""
        issuer = db.query(Issuer).filter_by(issuer_id=issuer_data['issuer_id']).first()
        
        if not issuer:
            issuer = Issuer(
                issuer_id=issuer_data['issuer_id'],
                name=issuer_data['name'],
                contact_email=issuer_data.get('contact_email'),
                city=issuer_data.get('city'),
                region=issuer_data.get('region'),
                nuts_code=issuer_data.get('nuts_code')
            )
            db.add(issuer)
            db.flush()
            logger.debug(f"Created issuer: {issuer_data['name']}")
        
        return issuer
