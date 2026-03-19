import logging
import asyncio
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional
from src.database.connection import get_db
from src.database.models import Tender, Document
from src.config import Config
from src.documents.storage import MinIOStorage
from src.documents.portal_scrapers import ANACPortalScraper

logger = logging.getLogger(__name__)

class DocumentDownloader:
    """
    Real document downloader with portal scraping and MinIO storage.
    Downloads actual documents from Italian procurement portals.
    """
    
    def __init__(self):
        self.storage = MinIOStorage()
        self.scraper = ANACPortalScraper()
    
    def download_for_portal(self, portal_domain: str, limit: int = 10, auto_detect: bool = False):
        """
        Download documents from a specific portal with real implementation.
        
        Args:
            portal_domain: Portal domain to download from (or None for auto-detect)
            limit: Maximum number of tenders to process
            auto_detect: Auto-detect top portal from database
        """
        if auto_detect:
            portal_domain = self._detect_top_portal()
            if not portal_domain:
                logger.warning("No portals found in database")
                return 0
        
        logger.info(f"Processing documents from {portal_domain}")
        
        with get_db() as db:
            tenders = db.query(Tender).filter(
                Tender.document_portal_url.like(f"%{portal_domain}%")
            ).limit(limit).all()
            
            if not tenders:
                logger.warning(f"No tenders found for portal: {portal_domain}")
                return 0
            
            processed = 0
            for tender in tenders:
                try:
                    result = asyncio.run(self._process_tender_documents_async(db, tender))
                    if result:
                        processed += 1
                except Exception as e:
                    logger.error(f"Error processing documents for {tender.tender_id}: {e}")
            
            db.commit()
            logger.info(f"Processed {processed}/{len(tenders)} tenders from {portal_domain}")
            return processed
    
    async def _process_tender_documents_async(self, db, tender: Tender) -> bool:
        """
        Process documents for a tender with real download and storage.
        
        Returns:
            True if documents were processed successfully
        """
        existing = db.query(Document).filter_by(tender_id=tender.id).first()
        if existing:
            logger.debug(f"Documents already exist for {tender.tender_id}")
            return False
        
        if not tender.document_portal_url:
            logger.warning(f"No document portal URL for {tender.tender_id}")
            return False
        
        portal_name = urlparse(tender.document_portal_url).netloc
        
        try:
            doc_list = await self.scraper.fetch_document_list(tender.document_portal_url)
            
            if not doc_list:
                logger.warning(f"No documents found for {tender.tender_id}")
                doc = Document(
                    tender_id=tender.id,
                    document_type="not_found",
                    filename=f"{tender.tender_id}_not_found.txt",
                    portal_url=tender.document_portal_url,
                    portal_name=portal_name,
                    storage_url=None,
                    file_size=None,
                    downloaded_at=None
                )
                db.add(doc)
                return False
            
            logger.info(f"Found {len(doc_list)} documents for {tender.tender_id}")
            
            for doc_info in doc_list[:5]:
                try:
                    doc_data = await self.scraper.download_document(doc_info['url'])
                    
                    if doc_data:
                        storage_url = self.storage.upload_document(
                            tender.tender_id,
                            doc_info['filename'],
                            doc_data
                        )
                        
                        doc = Document(
                            tender_id=tender.id,
                            document_type=doc_info['type'],
                            filename=doc_info['filename'],
                            portal_url=doc_info['url'],
                            portal_name=portal_name,
                            storage_url=storage_url,
                            file_size=len(doc_data),
                            downloaded_at=datetime.utcnow()
                        )
                        db.add(doc)
                        logger.info(f"Downloaded and stored: {doc_info['filename']} ({len(doc_data)} bytes)")
                    else:
                        doc = Document(
                            tender_id=tender.id,
                            document_type=doc_info['type'],
                            filename=doc_info['filename'],
                            portal_url=doc_info['url'],
                            portal_name=portal_name,
                            storage_url=None,
                            file_size=None,
                            downloaded_at=None
                        )
                        db.add(doc)
                        logger.warning(f"Failed to download: {doc_info['filename']}")
                        
                except Exception as e:
                    logger.error(f"Error downloading {doc_info.get('filename', 'unknown')}: {e}")
                    continue
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing documents for {tender.tender_id}: {e}")
            return False
    
    def _detect_top_portal(self) -> Optional[str]:
        """Detect the most common portal domain from database"""
        from collections import Counter
        
        with get_db() as db:
            tenders = db.query(Tender).filter(Tender.document_portal_url.isnot(None)).all()
            
            if not tenders:
                return None
            
            domains = [urlparse(t.document_portal_url).netloc for t in tenders if t.document_portal_url]
            if not domains:
                return None
            
            counter = Counter(domains)
            top_portal = counter.most_common(1)[0][0]
            logger.info(f"Auto-detected top portal: {top_portal} ({counter[top_portal]} tenders)")
            return top_portal
    
    async def close(self):
        """Close scraper connections"""
        await self.scraper.close()
