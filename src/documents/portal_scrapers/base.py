from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class PortalScraper(ABC):
    """Abstract base class for document portal scrapers"""
    
    def __init__(self):
        self.session = None
    
    @abstractmethod
    async def fetch_document_list(self, tender_url: str) -> List[Dict]:
        """
        Fetch list of documents for a tender
        
        Args:
            tender_url: URL to the tender page
            
        Returns:
            List of document dictionaries with keys:
                - filename: Document filename
                - url: Download URL
                - type: Document type (e.g., 'specification', 'attachment')
                - size: File size in bytes (if available)
        """
        pass
    
    @abstractmethod
    async def download_document(self, doc_url: str) -> Optional[bytes]:
        """
        Download document content
        
        Args:
            doc_url: URL to download document from
            
        Returns:
            Document bytes or None if download failed
        """
        pass
    
    async def close(self):
        """Close any open connections"""
        if self.session:
            await self.session.close()
