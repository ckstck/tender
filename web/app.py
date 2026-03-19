from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import sys
from pathlib import Path
import math
from datetime import datetime
import io

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.connection import get_db
from src.database.models import Tender, Organization, Issuer, Document
from src.search.hybrid import HybridSearch
from src.ingestion.pipeline import IngestionPipeline
from src.organizations.extractor import OrganizationExtractor
from src.documents.storage import MinIOStorage
from src.documents.analyzer import DocumentPortalAnalyzer

app = FastAPI(title="Italian Tender Intelligence System")

class SearchRequest(BaseModel):
    query: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    contract_type: Optional[str] = None
    nuts_codes: Optional[str] = None
    cpv_codes: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    issuer_name: Optional[str] = None
    keyword: Optional[str] = None
    page: int = 1
    page_size: int = 20

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/api/status")
async def get_status():
    with get_db() as db:
        return {
            "tenders": db.query(Tender).count(),
            "organizations": db.query(Organization).count(),
            "issuers": db.query(Issuer).count(),
            "documents": db.query(Document).count()
        }

@app.get("/api/tenders")
async def get_tenders(page: int = 1, page_size: int = 20):
    with get_db() as db:
        offset = (page - 1) * page_size
        total = db.query(Tender).count()
        tenders = db.query(Tender).offset(offset).limit(page_size).all()
        
        return {
            "tenders": [{
            "tender_id": t.tender_id,
            "title": t.title,
            "estimated_value": float(t.estimated_value) if t.estimated_value else None,
            "publication_date": t.publication_date.isoformat() if t.publication_date else None,
            "submission_deadline": t.submission_deadline.isoformat() if t.submission_deadline else None,
            "contract_type": t.contract_type,
            "summary": t.summary,
            "tender_url": t.tender_url,
            "execution_location": t.execution_location,
            "nuts_codes": t.nuts_codes,
            "cpv_codes": t.cpv_codes,
            "has_lots": t.has_lots,
            "lots_data": t.lots_data,
            "eu_funded": t.eu_funded,
            "renewable": t.renewable
        } for t in tenders],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size)
        }

@app.get("/api/organizations")
async def get_organizations(limit: int = 20):
    with get_db() as db:
        orgs = db.query(Organization).limit(limit).all()
        return [{
            "id": o.id,
            "name": o.name,
            "tax_id": o.tax_id,
            "region": o.region
        } for o in orgs]

@app.post("/api/search")
async def search_tenders(request: SearchRequest):
    searcher = HybridSearch()
    
    filters = {}
    if request.min_value:
        filters['min_value'] = request.min_value
    if request.max_value:
        filters['max_value'] = request.max_value
    if request.contract_type:
        filters['contract_type'] = request.contract_type
    if request.nuts_codes:
        filters['nuts_codes'] = [request.nuts_codes]
    if request.cpv_codes:
        filters['cpv_codes'] = [request.cpv_codes]
    if request.start_date:
        filters['start_date'] = request.start_date
    if request.end_date:
        filters['end_date'] = request.end_date
    if request.issuer_name:
        filters['issuer_name'] = request.issuer_name
    if request.keyword:
        filters['keyword'] = request.keyword
    
    # Get total count for pagination
    with get_db() as db:
        from src.search.filters import TenderFilter
        query = db.query(Tender).filter(Tender.embedding.isnot(None))
        if filters:
            query = TenderFilter.apply_filters(query, **filters)
        total = query.count()
    
    # Calculate pagination
    offset = (request.page - 1) * request.page_size
    
    # Get paginated results
    all_results = searcher.search(request.query, filters=filters, limit=total if total < 1000 else 1000)
    results = all_results[offset:offset + request.page_size]
    
    # Fix NaN values in similarity scores
    for result in results:
        if 'similarity_score' in result and (result['similarity_score'] is None or math.isnan(result['similarity_score'])):
            result['similarity_score'] = 0.0
    
    return {
        "results": results,
        "total": total,
        "page": request.page,
        "page_size": request.page_size,
        "total_pages": math.ceil(total / request.page_size) if total > 0 else 0
    }

@app.post("/api/ingest")
async def run_ingestion(days: int = 30):
    try:
        pipeline = IngestionPipeline()
        result = pipeline.run(days_back=days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/extract-orgs")
async def extract_organizations(days: int = 30):
    try:
        extractor = OrganizationExtractor()
        result = extractor.extract_from_tenders(days_back=days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/issuers")
async def get_issuers(page: int = 1, page_size: int = 20):
    with get_db() as db:
        offset = (page - 1) * page_size
        total = db.query(Issuer).count()
        issuers = db.query(Issuer).offset(offset).limit(page_size).all()
        
        result = []
        for issuer in issuers:
            tender_count = db.query(Tender).filter(Tender.issuer_id == issuer.id).count()
            result.append({
                "id": issuer.id,
                "issuer_id": issuer.issuer_id,
                "name": issuer.name,
                "contact_email": issuer.contact_email,
                "contact_phone": issuer.contact_phone,
                "city": issuer.city,
                "region": issuer.region,
                "nuts_code": issuer.nuts_code,
                "organization_type": issuer.organization_type,
                "tender_count": tender_count
            })
        
        return {
            "issuers": result,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size)
        }

@app.get("/api/documents")
async def get_documents(page: int = 1, page_size: int = 20):
    with get_db() as db:
        offset = (page - 1) * page_size
        total = db.query(Document).count()
        documents = db.query(Document).join(Tender).offset(offset).limit(page_size).all()
        
        result = []
        for doc in documents:
            result.append({
                "id": doc.id,
                "filename": doc.filename,
                "document_type": doc.document_type,
                "file_size": doc.file_size,
                "storage_url": doc.storage_url,
                "download_date": doc.downloaded_at.isoformat() if doc.downloaded_at else None,
                "tender_id": doc.tender.tender_id if doc.tender else None,
                "tender_title": doc.tender.title if doc.tender else None
            })
        
        return {
            "documents": result,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size)
        }

@app.get("/api/documents/download/{document_id}")
async def download_document(document_id: int):
    with get_db() as db:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        
        if not doc.storage_url:
            raise HTTPException(status_code=404, detail="Document not yet downloaded")
        
        try:
            storage = MinIOStorage()
            # Extract object name from storage URL (format: minio://bucket/path)
            object_name = doc.storage_url.replace(f"minio://{storage.bucket}/", "")
            file_data = storage.download_document(object_name)
            
            if not file_data:
                raise HTTPException(status_code=404, detail="Document not found in storage")
            
            return StreamingResponse(
                io.BytesIO(file_data),
                media_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={doc.filename}"}
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.get("/api/portal-analysis")
async def get_portal_analysis():
    try:
        analyzer = DocumentPortalAnalyzer()
        portal_counts = analyzer.analyze(output_file='portal_analysis.csv')
        
        with get_db() as db:
            total_tenders = db.query(Tender).filter(Tender.document_portal_url.isnot(None)).count()
        
        result = []
        for portal, count in portal_counts.items():
            percentage = (count / total_tenders * 100) if total_tenders > 0 else 0
            result.append({
                "portal": portal,
                "count": count,
                "percentage": round(percentage, 2)
            })
        
        return {
            "portals": result,
            "total_tenders": total_tenders
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/jobs/status")
async def get_job_status():
    import os
    from pathlib import Path
    
    logs_dir = Path(__file__).parent.parent / 'logs'
    
    def read_last_lines(file_path, n=10):
        if not file_path.exists():
            return []
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
                return [line.strip() for line in lines[-n:]]
        except:
            return []
    
    def get_file_mtime(file_path):
        if file_path.exists():
            return datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
        return None
    
    ingestion_log = logs_dir / 'ingestion.log'
    documents_log = logs_dir / 'document_downloads.log'
    
    return {
        "ingestion": {
            "last_run": get_file_mtime(ingestion_log),
            "recent_logs": read_last_lines(ingestion_log, 10)
        },
        "documents": {
            "last_run": get_file_mtime(documents_log),
            "recent_logs": read_last_lines(documents_log, 10)
        },
        "cron_schedule": {
            "ingestion": "Daily at 2:00 AM",
            "documents": "Daily at 3:00 AM"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
