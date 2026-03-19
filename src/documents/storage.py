import logging
import io
from typing import Optional
from minio import Minio
from minio.error import S3Error
from src.config import Config

logger = logging.getLogger(__name__)

class MinIOStorage:
    """MinIO object storage client for tender documents"""
    
    def __init__(self):
        self.endpoint = Config.MINIO_ENDPOINT
        self.bucket = Config.MINIO_BUCKET
        self.secure = Config.MINIO_SECURE
        
        try:
            self.client = Minio(
                self.endpoint,
                access_key=Config.MINIO_ACCESS_KEY,
                secret_key=Config.MINIO_SECRET_KEY,
                secure=self.secure
            )
            self._ensure_bucket()
            self.enabled = True
            logger.info(f"MinIO client initialized: {self.endpoint}/{self.bucket}")
        except Exception as e:
            logger.warning(f"MinIO initialization failed: {e}. Storage disabled.")
            self.enabled = False
            self.client = None
    
    def _ensure_bucket(self):
        """Create bucket if it doesn't exist"""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"Created MinIO bucket: {self.bucket}")
        except S3Error as e:
            logger.error(f"Error ensuring bucket exists: {e}")
            raise
    
    def upload_document(self, tender_id: str, filename: str, data: bytes) -> Optional[str]:
        """
        Upload document to MinIO storage
        
        Args:
            tender_id: Tender identifier
            filename: Document filename
            data: Document bytes
            
        Returns:
            Storage URL or None if upload failed
        """
        if not self.enabled:
            logger.warning("MinIO not enabled, skipping upload")
            return None
        
        try:
            object_name = f"{tender_id}/{filename}"
            
            self.client.put_object(
                self.bucket,
                object_name,
                io.BytesIO(data),
                length=len(data),
                content_type=self._get_content_type(filename)
            )
            
            storage_url = f"minio://{self.bucket}/{object_name}"
            logger.info(f"Uploaded document: {storage_url} ({len(data)} bytes)")
            return storage_url
            
        except S3Error as e:
            logger.error(f"MinIO upload failed for {tender_id}/{filename}: {e}")
            return None
    
    def download_document(self, object_name: str) -> Optional[bytes]:
        """
        Download document from MinIO storage
        
        Args:
            object_name: Object path in bucket
            
        Returns:
            Document bytes or None if download failed
        """
        if not self.enabled:
            logger.warning("MinIO not enabled, cannot download")
            return None
        
        try:
            response = self.client.get_object(self.bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            
            logger.info(f"Downloaded document: {object_name} ({len(data)} bytes)")
            return data
            
        except S3Error as e:
            logger.error(f"MinIO download failed for {object_name}: {e}")
            return None
    
    def get_presigned_url(self, object_name: str, expires_seconds: int = 3600) -> Optional[str]:
        """
        Get presigned URL for temporary access to document
        
        Args:
            object_name: Object path in bucket
            expires_seconds: URL expiration time in seconds
            
        Returns:
            Presigned URL or None if failed
        """
        if not self.enabled:
            return None
        
        try:
            from datetime import timedelta
            url = self.client.presigned_get_object(
                self.bucket,
                object_name,
                expires=timedelta(seconds=expires_seconds)
            )
            return url
        except S3Error as e:
            logger.error(f"Failed to generate presigned URL for {object_name}: {e}")
            return None
    
    def _get_content_type(self, filename: str) -> str:
        """Determine content type from filename"""
        ext = filename.lower().split('.')[-1]
        content_types = {
            'pdf': 'application/pdf',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'zip': 'application/zip',
            'xml': 'application/xml',
            'json': 'application/json'
        }
        return content_types.get(ext, 'application/octet-stream')
