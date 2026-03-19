import logging
from collections import Counter
from typing import Dict
import csv
from src.database.connection import get_db
from src.database.models import Tender
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class DocumentPortalAnalyzer:
    def analyze(self, output_file: str = 'portal_analysis.csv') -> Dict[str, int]:
        """Analyze document portal distribution and output to CSV"""
        logger.info("Analyzing document portals")
        
        with get_db() as db:
            tenders = db.query(Tender).filter(Tender.document_portal_url.isnot(None)).all()
            
            portal_counts = Counter()
            
            for tender in tenders:
                if tender.document_portal_url:
                    domain = urlparse(tender.document_portal_url).netloc
                    portal_counts[domain] += 1
            
            total = sum(portal_counts.values())
            
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Portal Domain', 'Tender Count', 'Percentage'])
                
                for domain, count in portal_counts.most_common():
                    percentage = (count / total * 100) if total > 0 else 0
                    writer.writerow([domain, count, f"{percentage:.2f}%"])
            
            logger.info(f"Portal analysis saved to {output_file}")
            logger.info(f"Total portals: {len(portal_counts)}, Total tenders: {total}")
            
            return dict(portal_counts.most_common())
