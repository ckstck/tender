import logging
import aiohttp
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .base import PortalScraper

logger = logging.getLogger(__name__)

class ANACPortalScraper(PortalScraper):
    """
    Scraper for Italian ANAC procurement portal documents
    
    Supports:
    - pubblicitalegale.anticorruzione.it
    - portale-documenti.comune.*.it
    - Other Italian procurement portals
    """
    
    def __init__(self):
        super().__init__()
        self.timeout = aiohttp.ClientTimeout(total=30)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=self.headers
            )
        return self.session
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError))
    )
    async def fetch_document_list(self, tender_url: str) -> List[Dict]:
        """
        Fetch document list from ANAC portal tender page
        
        This implementation handles common Italian procurement portal patterns:
        1. Direct PDF links in the tender page
        2. Document sections with download links
        3. Attachment tables
        """
        logger.info(f"Fetching documents from: {tender_url}")
        
        session = await self._get_session()
        
        try:
            async with session.get(tender_url) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch tender page: {response.status}")
                    return []
                
                html = await response.text()
                soup = BeautifulSoup(html, 'lxml')
                
                documents = []
                
                # Strategy 1: Find all PDF links
                pdf_links = soup.find_all('a', href=lambda x: x and x.endswith('.pdf'))
                for link in pdf_links:
                    doc_url = urljoin(tender_url, link.get('href'))
                    filename = link.get_text(strip=True) or urlparse(doc_url).path.split('/')[-1]
                    
                    documents.append({
                        'filename': filename if filename.endswith('.pdf') else f"{filename}.pdf",
                        'url': doc_url,
                        'type': self._classify_document(filename),
                        'size': None
                    })
                
                # Strategy 2: Find document sections/tables
                doc_sections = soup.find_all(['div', 'section'], class_=lambda x: x and ('document' in x.lower() or 'allegat' in x.lower()))
                for section in doc_sections:
                    links = section.find_all('a', href=True)
                    for link in links:
                        href = link.get('href')
                        if href and (href.endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip'))):
                            doc_url = urljoin(tender_url, href)
                            filename = link.get_text(strip=True) or urlparse(doc_url).path.split('/')[-1]
                            
                            # Check if already added
                            if not any(d['url'] == doc_url for d in documents):
                                documents.append({
                                    'filename': filename,
                                    'url': doc_url,
                                    'type': self._classify_document(filename),
                                    'size': None
                                })
                
                # Strategy 3: Look for common Italian portal patterns
                # "Scarica" (Download), "Allegati" (Attachments), "Documenti" (Documents)
                download_keywords = ['scarica', 'download', 'allegat', 'document']
                for keyword in download_keywords:
                    elements = soup.find_all(text=lambda t: t and keyword in t.lower())
                    for elem in elements:
                        parent = elem.find_parent('a', href=True)
                        if parent:
                            href = parent.get('href')
                            if href and not href.startswith('#'):
                                doc_url = urljoin(tender_url, href)
                                filename = elem.strip() or urlparse(doc_url).path.split('/')[-1]
                                
                                if not any(d['url'] == doc_url for d in documents):
                                    documents.append({
                                        'filename': filename,
                                        'url': doc_url,
                                        'type': self._classify_document(filename),
                                        'size': None
                                    })
                
                logger.info(f"Found {len(documents)} documents for {tender_url}")
                return documents
                
        except Exception as e:
            logger.error(f"Error fetching document list from {tender_url}: {e}")
            return []
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError))
    )
    async def download_document(self, doc_url: str) -> Optional[bytes]:
        """
        Download document from URL
        
        Args:
            doc_url: Document download URL
            
        Returns:
            Document bytes or None if failed
        """
        logger.info(f"Downloading document: {doc_url}")
        
        session = await self._get_session()
        
        try:
            async with session.get(doc_url) as response:
                if response.status != 200:
                    logger.warning(f"Failed to download document: {response.status}")
                    return None
                
                data = await response.read()
                logger.info(f"Downloaded {len(data)} bytes from {doc_url}")
                return data
                
        except Exception as e:
            logger.error(f"Error downloading document from {doc_url}: {e}")
            return None
    
    def _classify_document(self, filename: str) -> str:
        """
        Classify document type based on filename
        
        Common Italian tender document types:
        - Capitolato: Specification
        - Disciplinare: Tender rules
        - Allegato: Attachment
        - Modulo: Form
        - Offerta: Offer template
        """
        filename_lower = filename.lower()
        
        if any(word in filename_lower for word in ['capitolato', 'specification', 'specifiche']):
            return 'specification'
        elif any(word in filename_lower for word in ['disciplinare', 'rules', 'regole']):
            return 'tender_rules'
        elif any(word in filename_lower for word in ['allegato', 'attachment', 'annex']):
            return 'attachment'
        elif any(word in filename_lower for word in ['modulo', 'form', 'formulario']):
            return 'form'
        elif any(word in filename_lower for word in ['offerta', 'offer', 'bid']):
            return 'offer_template'
        elif any(word in filename_lower for word in ['contratto', 'contract']):
            return 'contract'
        else:
            return 'other'
