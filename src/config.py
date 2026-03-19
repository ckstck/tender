import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://localhost/tender_db')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    ANAC_API_KEY = os.getenv('ANAC_API_KEY', '')
    
    S3_ENDPOINT = os.getenv('S3_ENDPOINT')
    S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY')
    S3_SECRET_KEY = os.getenv('S3_SECRET_KEY')
    S3_BUCKET = os.getenv('S3_BUCKET', 'tender-documents')
    
    MINIO_ENDPOINT = os.getenv('MINIO_ENDPOINT', 'localhost:9000')
    MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
    MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
    MINIO_BUCKET = os.getenv('MINIO_BUCKET', 'tender-documents')
    MINIO_SECURE = os.getenv('MINIO_SECURE', 'false').lower() == 'true'
    
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    INGESTION_DAYS_BACK = int(os.getenv('INGESTION_DAYS_BACK', '30'))
    
    OPENAI_MODEL = 'gpt-4o-mini'
    EMBEDDING_MODEL = 'text-embedding-3-small'
    EMBEDDING_DIMENSIONS = 1536
    
    ANAC_BASE_URL = 'https://dati.anticorruzione.it/opendata'
