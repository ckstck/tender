import logging
from src.database.connection import get_db
from src.database.models import Organization, TenderParticipant, Tender
from src.ingestion.client import ANACClient

logger = logging.getLogger(__name__)

class OrganizationExtractor:
    def extract_from_tenders(self, days_back: int = 30):
        """Extract organizations from tender participants"""
        logger.info("Starting organization extraction")
        
        client = ANACClient()
        raw_tenders = client.fetch_tenders(days_back)
        
        org_count = 0
        participation_count = 0
        
        with get_db() as db:
            for tender_data in raw_tenders:
                tender = db.query(Tender).filter_by(tender_id=tender_data['tender_id']).first()
                if not tender:
                    logger.warning(f"Tender {tender_data['tender_id']} not found in database, skipping")
                    continue
                
                for participant_data in tender_data.get('participants', []):
                    org, is_new = self._get_or_create_organization(db, participant_data)
                    if is_new:
                        org_count += 1
                    
                    existing_participation = db.query(TenderParticipant).filter_by(
                        tender_id=tender.id,
                        organization_id=org.id,
                        role=participant_data.get('role', 'bidder')
                    ).first()
                    
                    if not existing_participation:
                        participation = TenderParticipant(
                            tender_id=tender.id,
                            organization_id=org.id,
                            role=participant_data.get('role', 'bidder'),
                            awarded=participant_data.get('awarded', False),
                            award_value=participant_data.get('award_value')
                        )
                        db.add(participation)
                        participation_count += 1
        
        logger.info(f"Organization extraction completed: {org_count} new organizations, {participation_count} new participations")
        return {
            'new_organizations': org_count,
            'new_participations': participation_count
        }
    
    def _get_or_create_organization(self, db, org_data: dict) -> tuple[Organization, bool]:
        """Get or create organization with deduplication. Returns (org, is_new)"""
        org = db.query(Organization).filter_by(tax_id=org_data['tax_id']).first()
        
        if org:
            return org, False
        
        org = Organization(
            tax_id=org_data['tax_id'],
            name=org_data['name'],
            country=org_data.get('country', 'IT'),
            city=org_data.get('city'),
            region=org_data.get('region'),
            industry=org_data.get('industry'),
            size=org_data.get('size')
        )
        db.add(org)
        db.flush()
        logger.info(f"Created organization: {org_data['name']} ({org_data['tax_id']})")
        
        return org, True
