import logging
from typing import List, Dict, Optional
from src.database.connection import get_db
from src.database.models import Tender
from src.ingestion.enrichment import TenderEnrichment
from src.search.filters import TenderFilter

logger = logging.getLogger(__name__)

class HybridSearch:
    def __init__(self):
        self.enrichment = TenderEnrichment()
    
    def search(self, query_text: str, filters: Optional[dict] = None, limit: int = 10) -> List[Dict]:
        """
        Hybrid search: apply structured filters first, then rank by vector similarity.
        Simple approach: filters narrow the candidate set, vectors provide ranking.
        """
        
        query_embedding = self.enrichment.generate_embedding(query_text)
        
        with get_db() as db:
            base_query = db.query(Tender).filter(Tender.embedding.isnot(None))
            
            if filters:
                base_query = TenderFilter.apply_filters(base_query, **filters)
            
            results = base_query.add_columns(
                Tender.embedding.cosine_distance(query_embedding).label('distance')
            ).order_by(
                'distance'
            ).limit(limit).all()
            
            return [self._format_result(result[0], 1 - result[1]) for result in results]
    
    def _format_result(self, tender: Tender, similarity: float) -> Dict:
        """Format search result"""
        return {
            "tender_id": tender.tender_id,
            "title": tender.title,
            "summary": tender.summary,
            "estimated_value": float(tender.estimated_value) if tender.estimated_value else None,
            "publication_date": tender.publication_date.isoformat() if tender.publication_date else None,
            "submission_deadline": tender.submission_deadline.isoformat() if tender.submission_deadline else None,
            "cpv_codes": tender.cpv_codes,
            "nuts_codes": tender.nuts_codes,
            "contract_type": tender.contract_type,
            "eu_funded": tender.eu_funded,
            "tender_url": tender.tender_url,
            "similarity_score": float(similarity)
        }
