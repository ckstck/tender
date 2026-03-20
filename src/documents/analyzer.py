import csv
import logging
from collections import Counter
from typing import Dict, Optional
from urllib.parse import urlparse

from src.database.connection import get_db
from src.database.models import Tender

logger = logging.getLogger(__name__)

class DocumentPortalAnalyzer:
    def analyze(self, output_file: str = 'portal_analysis.csv') -> Dict[str, int]:
        """Analyze document portal distribution and output to CSV"""
        logger.info("Analyzing document portals (source-aware fallback)")
        
        with get_db() as db:
            tenders = db.query(Tender).all()

            portal_counts = Counter()

            total_tenders_processed = 0
            tenders_with_real_document_urls = 0
            tenders_using_fallback = 0

            ANAC_UI_BASE_DOMAIN = "pubblicitalegale.anticorruzione.it"
            MEPA_BASE_DOMAIN = "acquistinretepa.it"

            def normalize_domain(url_or_base: Optional[str]) -> str:
                if not url_or_base or not isinstance(url_or_base, str):
                    return ""
                candidate = url_or_base.strip()
                if not candidate:
                    return ""
                if not (candidate.startswith("http://") or candidate.startswith("https://")):
                    candidate = "https://" + candidate
                parsed = urlparse(candidate)
                host = (parsed.netloc or "").strip().lower()
                if host.startswith("www."):
                    host = host[len("www.") :]
                return host

            for tender in tenders:
                total_tenders_processed += 1
                doc_url = (tender.document_portal_url or "").strip() if tender.document_portal_url else ""

                if doc_url:
                    domain = normalize_domain(doc_url)
                    if domain:
                        tenders_with_real_document_urls += 1
                    else:
                        # Document URL exists but can't be normalized; fall back.
                        tenders_using_fallback += 1
                        doc_url = ""

                if not doc_url:
                    # Fallback 1: tender_url domain.
                    tender_url = (tender.tender_url or "").strip() if tender.tender_url else ""
                    domain = normalize_domain(tender_url) if tender_url else ""
                    if domain:
                        tenders_using_fallback += 1
                    else:
                        # Fallback 2 (last resort): platform-aware base domain.
                        tenders_using_fallback += 1
                        sp = getattr(tender, "source_platform", None)  # tolerate older schemas
                        domain = MEPA_BASE_DOMAIN if sp == "MEPA" else ANAC_UI_BASE_DOMAIN

                if not domain:
                    # Last-resort guarantee: if somehow domain extraction fails, use ANAC UI.
                    domain = ANAC_UI_BASE_DOMAIN

                portal_counts[domain] += 1

            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['portal_domain', 'tender_count'])
                
                for domain, count in portal_counts.most_common():
                    writer.writerow([domain, count])

            logger.info(f"Portal analysis saved to {output_file}")
            logger.info(
                "Portal analysis stats: total_tenders=%s real_doc_urls=%s fallback=%s unique_domains=%s",
                total_tenders_processed,
                tenders_with_real_document_urls,
                tenders_using_fallback,
                len(portal_counts),
            )
            
            return dict(portal_counts.most_common())
