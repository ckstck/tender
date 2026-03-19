import pytest
from unittest.mock import Mock, patch, MagicMock
from src.documents.storage import MinIOStorage

class TestMinIOStorage:
    """Test MinIO storage client"""
    
    @patch('src.documents.storage.Minio')
    def test_init_success(self, mock_minio):
        """Test successful MinIO initialization"""
        mock_client = Mock()
        mock_client.bucket_exists.return_value = True
        mock_minio.return_value = mock_client
        
        storage = MinIOStorage()
        
        assert storage.enabled is True
        assert storage.client is not None
        mock_minio.assert_called_once()
    
    @patch('src.documents.storage.Minio')
    def test_init_creates_bucket(self, mock_minio):
        """Test bucket creation if it doesn't exist"""
        mock_client = Mock()
        mock_client.bucket_exists.return_value = False
        mock_minio.return_value = mock_client
        
        storage = MinIOStorage()
        
        mock_client.make_bucket.assert_called_once()
    
    @patch('src.documents.storage.Minio')
    def test_upload_document_success(self, mock_minio):
        """Test successful document upload"""
        mock_client = Mock()
        mock_client.bucket_exists.return_value = True
        mock_minio.return_value = mock_client
        
        storage = MinIOStorage()
        test_data = b"test document content"
        
        result = storage.upload_document("TENDER-001", "test.pdf", test_data)
        
        assert result is not None
        assert "minio://" in result
        assert "TENDER-001/test.pdf" in result
        mock_client.put_object.assert_called_once()
    
    @patch('src.documents.storage.Minio')
    def test_upload_document_disabled(self, mock_minio):
        """Test upload when MinIO is disabled"""
        mock_minio.side_effect = Exception("Connection failed")
        
        storage = MinIOStorage()
        result = storage.upload_document("TENDER-001", "test.pdf", b"data")
        
        assert result is None
    
    @patch('src.documents.storage.Minio')
    def test_download_document_success(self, mock_minio):
        """Test successful document download"""
        mock_client = Mock()
        mock_client.bucket_exists.return_value = True
        mock_response = Mock()
        mock_response.read.return_value = b"test content"
        mock_client.get_object.return_value = mock_response
        mock_minio.return_value = mock_client
        
        storage = MinIOStorage()
        result = storage.download_document("TENDER-001/test.pdf")
        
        assert result == b"test content"
        mock_client.get_object.assert_called_once()
    
    @patch('src.documents.storage.Minio')
    def test_get_content_type(self, mock_minio):
        """Test content type detection"""
        mock_client = Mock()
        mock_client.bucket_exists.return_value = True
        mock_minio.return_value = mock_client
        
        storage = MinIOStorage()
        
        assert storage._get_content_type("test.pdf") == "application/pdf"
        assert storage._get_content_type("test.docx") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert storage._get_content_type("test.unknown") == "application/octet-stream"
