from typing import Optional, List
from datetime import datetime
from sqlalchemy import and_, or_
from src.database.models import Tender, Issuer

class TenderFilter:
    @staticmethod
    def apply_filters(query, **filters):
        """Apply structured filters to tender query"""
        
        if filters.get('min_value'):
            query = query.filter(Tender.estimated_value >= filters['min_value'])
        
        if filters.get('max_value'):
            query = query.filter(Tender.estimated_value <= filters['max_value'])
        
        if filters.get('start_date'):
            start = datetime.fromisoformat(filters['start_date']).date() if isinstance(filters['start_date'], str) else filters['start_date']
            query = query.filter(Tender.publication_date >= start)
        
        if filters.get('end_date'):
            end = datetime.fromisoformat(filters['end_date']).date() if isinstance(filters['end_date'], str) else filters['end_date']
            query = query.filter(Tender.publication_date <= end)
        
        if filters.get('nuts_codes'):
            nuts_list = filters['nuts_codes'] if isinstance(filters['nuts_codes'], list) else [filters['nuts_codes']]
            query = query.filter(Tender.nuts_codes.overlap(nuts_list))
        
        if filters.get('cpv_codes'):
            cpv_list = filters['cpv_codes'] if isinstance(filters['cpv_codes'], list) else [filters['cpv_codes']]
            query = query.filter(Tender.cpv_codes.overlap(cpv_list))
        
        if filters.get('contract_type'):
            query = query.filter(Tender.contract_type == filters['contract_type'])
        
        if filters.get('eu_funded') is not None:
            query = query.filter(Tender.eu_funded == filters['eu_funded'])
        
        if filters.get('issuer_name'):
            query = query.join(Issuer).filter(Issuer.name.ilike(f"%{filters['issuer_name']}%"))
        
        if filters.get('keyword'):
            keyword = f"%{filters['keyword']}%"
            query = query.filter(
                or_(
                    Tender.title.ilike(keyword),
                    Tender.searchable_text.ilike(keyword)
                )
            )
        
        return query
