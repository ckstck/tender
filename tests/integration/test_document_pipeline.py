import pytest
from unittest.mock import Mock, patch, AsyncMock
from src.documents.downloader import DocumentDownloader
from src.database.models import Tender, Document

class TestDocumentPipeline:
    """Integration tests for document download pipeline"""
    
    @patch('src.documents.downloader.MinIOStorage')
    @patch('src.documents.downloader.ANACPortalScraper')
    def test_download_for_portal_auto_detect(self, mock_scraper, mock_storage):
        """Test auto-detect portal functionality"""
        mock_storage_instance = Mock()
        mock_storage_instance.enabled = True
        mock_storage.return_value = mock_storage_instance
        
        mock_scraper_instance = Mock()
        mock_scraper.return_value = mock_scraper_instance
        
        downloader = DocumentDownloader()
        
        with patch.object(downloader, '_detect_top_portal', return_value='example.com'):
            with patch('src.documents.downloader.get_db') as mock_db:
                mock_session = Mock()
                mock_query = Mock()
                mock_query.filter.return_value.limit.return_value.all.return_value = []
                mock_session.query.return_value = mock_query
                mock_db.return_value.__enter__.return_value = mock_session
                
                result = downloader.download_for_portal(None, limit=5, auto_detect=True)
                
                assert result == 0
    
    @patch('src.documents.downloader.MinIOStorage')
    @patch('src.documents.downloader.ANACPortalScraper')
    @patch('src.documents.downloader.asyncio.run')
    def test_download_for_portal_with_tenders(self, mock_asyncio, mock_scraper, mock_storage):
        """Test document download with tenders"""
        mock_storage_instance = Mock()
        mock_storage_instance.enabled = True
        mock_storage.return_value = mock_storage_instance
        
        mock_scraper_instance = Mock()
        mock_scraper.return_value = mock_scraper_instance
        
        mock_asyncio.return_value = True
        
        downloader = DocumentDownloader()
        
        with patch('src.documents.downloader.get_db') as mock_db:
            mock_session = Mock()
            mock_tender = Mock(spec=Tender)
            mock_tender.id = 1
            mock_tender.tender_id = "TEST-001"
            mock_tender.document_portal_url = "https://example.com/tender/123"
            
            mock_query = Mock()
            mock_query.filter.return_value.limit.return_value.all.return_value = [mock_tender]
            mock_session.query.return_value = mock_query
            mock_db.return_value.__enter__.return_value = mock_session
            
            result = downloader.download_for_portal("example.com", limit=5)
            
            assert result == 1
            mock_asyncio.assert_called_once()
    
    def test_detect_top_portal(self):
        """Test top portal detection"""
        with patch('src.documents.downloader.MinIOStorage'):
            with patch('src.documents.downloader.ANACPortalScraper'):
                downloader = DocumentDownloader()
                
                with patch('src.documents.downloader.get_db') as mock_db:
                    mock_session = Mock()
                    
                    mock_tender1 = Mock()
                    mock_tender1.document_portal_url = "https://portal1.example.com/doc"
                    mock_tender2 = Mock()
                    mock_tender2.document_portal_url = "https://portal1.example.com/doc2"
                    mock_tender3 = Mock()
                    mock_tender3.document_portal_url = "https://portal2.example.com/doc"
                    
                    mock_query = Mock()
                    mock_query.filter.return_value.all.return_value = [mock_tender1, mock_tender2, mock_tender3]
                    mock_session.query.return_value = mock_query
                    mock_db.return_value.__enter__.return_value = mock_session
                    
                    result = downloader._detect_top_portal()
                    
                    assert result == "portal1.example.com"
