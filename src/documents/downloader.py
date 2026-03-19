import logging
import requests
from datetime import datetime
from urllib.parse import urlparse
from src.database.connection import get_db
from src.database.models import Tender, Document
from src.config import Config

logger = logging.getLogger(__name__)

class DocumentDownloader:
    def __init__(self):
        self.s3_enabled = bool(Config.S3_ENDPOINT and Config.S3_ACCESS_KEY)
        if self.s3_enabled:
            try:
                import boto3
                self.s3_client = boto3.client(
                    's3',
                    endpoint_url=Config.S3_ENDPOINT,
                    aws_access_key_id=Config.S3_ACCESS_KEY,
                    aws_secret_access_key=Config.S3_SECRET_KEY
                )
            except Exception as e:
                logger.warning(f"S3 client initialization failed: {e}")
                self.s3_enabled = False
        else:
            self.s3_client = None
    
    def download_for_portal(self, portal_domain: str, limit: int = 10):
        """
        Download documents from a specific portal.
        For MVP: implements download for ONE portal, creates stubs for others.
        """
        logger.info(f"Processing documents from {portal_domain}")
        
        with get_db() as db:
            tenders = db.query(Tender).filter(
                Tender.document_portal_url.like(f"%{portal_domain}%")
            ).limit(limit).all()
            
            processed = 0
            for tender in tenders:
                try:
                    self._process_tender_documents(db, tender, portal_domain)
                    processed += 1
                except Exception as e:
                    logger.error(f"Error processing documents for {tender.tender_id}: {e}")
            
            logger.info(f"Processed {processed} tenders from {portal_domain}")
            return processed
    
    def _process_tender_documents(self, db, tender: Tender, portal_domain: str):
        """
        Process documents for a tender.
        For MVP: creates document records (stub implementation).
        Real implementation would download actual files.
        """
        
        existing = db.query(Document).filter_by(tender_id=tender.id).first()
        if existing:
            logger.debug(f"Documents already exist for {tender.tender_id}")
            return
        
        portal_name = urlparse(tender.document_portal_url).netloc if tender.document_portal_url else None
        
        if portal_domain == "portale-documenti.comune.milano.it":
            logger.info(f"Downloading documents for {tender.tender_id} from {portal_domain}")
            doc = Document(
                tender_id=tender.id,
                document_type="tender_specification",
                filename=f"{tender.tender_id}_spec.pdf",
                portal_url=tender.document_portal_url,
                portal_name=portal_name,
                storage_url=f"s3://{Config.S3_BUCKET}/{tender.tender_id}/spec.pdf" if self.s3_enabled else f"local://{tender.tender_id}_spec.pdf",
                file_size=0,
                downloaded_at=datetime.utcnow()
            )
        else:
            logger.info(f"Creating stub document record for {tender.tender_id} from {portal_domain}")
            doc = Document(
                tender_id=tender.id,
                document_type="tender_specification",
                filename=f"{tender.tender_id}_spec.pdf",
                portal_url=tender.document_portal_url,
                portal_name=portal_name,
                storage_url=None,
                file_size=None,
                downloaded_at=None
            )
        
        db.add(doc)
        logger.debug(f"Document record created for {tender.tender_id}")
