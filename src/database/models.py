from sqlalchemy import Column, Integer, String, Text, DECIMAL, Boolean, TIMESTAMP, Date, ForeignKey
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from src.database.connection import Base
from pgvector.sqlalchemy import Vector

class Issuer(Base):
    __tablename__ = 'issuers'
    
    id = Column(Integer, primary_key=True)
    issuer_id = Column(String(255), unique=True, nullable=False)
    name = Column(String(500), nullable=False)
    contact_email = Column(String(255))
    contact_phone = Column(String(50))
    address = Column(Text)
    city = Column(String(255))
    region = Column(String(255))
    nuts_code = Column(String(10))
    organization_type = Column(String(100))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    tenders = relationship('Tender', back_populates='issuer')

class Tender(Base):
    __tablename__ = 'tenders'
    
    id = Column(Integer, primary_key=True)
    tender_id = Column(String(255), unique=True, nullable=False)
    title = Column(Text, nullable=False)
    issuer_id = Column(Integer, ForeignKey('issuers.id'))
    
    estimated_value = Column(DECIMAL(15, 2))
    currency = Column(String(3), default='EUR')
    award_criteria = Column(JSONB)
    
    publication_date = Column(Date)
    submission_deadline = Column(TIMESTAMP)
    execution_start_date = Column(Date)
    execution_end_date = Column(Date)
    
    execution_location = Column(Text)
    nuts_codes = Column(ARRAY(String(20)))
    
    cpv_codes = Column(ARRAY(String(20)))
    contract_type = Column(String(50))
    eu_funded = Column(Boolean)
    renewable = Column(Boolean)
    
    has_lots = Column(Boolean, default=False)
    lots_data = Column(JSONB)
    
    tender_url = Column(Text)
    document_portal_url = Column(Text)
    
    summary = Column(String(240))
    searchable_text = Column(Text)
    embedding = Column(Vector(1536))
    
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    issuer = relationship('Issuer', back_populates='tenders')
    participants = relationship('TenderParticipant', back_populates='tender')
    documents = relationship('Document', back_populates='tender')

class Organization(Base):
    __tablename__ = 'organizations'
    
    id = Column(Integer, primary_key=True)
    tax_id = Column(String(50), unique=True, nullable=False)
    name = Column(String(500), nullable=False)
    country = Column(String(2))
    city = Column(String(255))
    region = Column(String(255))
    industry = Column(String(255))
    size = Column(String(50))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    participations = relationship('TenderParticipant', back_populates='organization')

class TenderParticipant(Base):
    __tablename__ = 'tender_participants'
    
    id = Column(Integer, primary_key=True)
    tender_id = Column(Integer, ForeignKey('tenders.id', ondelete='CASCADE'))
    organization_id = Column(Integer, ForeignKey('organizations.id', ondelete='CASCADE'))
    role = Column(String(50))
    awarded = Column(Boolean, default=False)
    award_value = Column(DECIMAL(15, 2))
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    tender = relationship('Tender', back_populates='participants')
    organization = relationship('Organization', back_populates='participations')

class Document(Base):
    __tablename__ = 'documents'
    
    id = Column(Integer, primary_key=True)
    tender_id = Column(Integer, ForeignKey('tenders.id', ondelete='CASCADE'))
    document_type = Column(String(100))
    filename = Column(String(500))
    file_size = Column(Integer)
    storage_url = Column(Text)
    portal_url = Column(Text)
    portal_name = Column(String(255))
    downloaded_at = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    
    tender = relationship('Tender', back_populates='documents')

class SearchQuery(Base):
    __tablename__ = 'search_queries'
    
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey('organizations.id'))
    query_text = Column(Text)
    filters = Column(JSONB)
    results = Column(JSONB)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
