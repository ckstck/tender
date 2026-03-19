import pytest
from unittest.mock import Mock, patch, AsyncMock
from src.documents.portal_scrapers.anac_scraper import ANACPortalScraper

class TestANACPortalScraper:
    """Test ANAC portal scraper"""
    
    @pytest.mark.asyncio
    async def test_fetch_document_list_success(self):
        """Test successful document list fetching"""
        scraper = ANACPortalScraper()
        
        html_content = """
        <html>
            <body>
                <a href="/docs/capitolato.pdf">Capitolato Tecnico</a>
                <a href="/docs/disciplinare.pdf">Disciplinare di Gara</a>
            </body>
        </html>
        """
        
        with patch.object(scraper, '_get_session') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=html_content)
            
            mock_session_obj = AsyncMock()
            mock_session_obj.get = AsyncMock(return_value=mock_response)
            mock_session_obj.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session_obj.__aexit__ = AsyncMock()
            
            mock_session.return_value = mock_session_obj
            
            docs = await scraper.fetch_document_list("https://example.com/tender/123")
            
            assert len(docs) >= 2
            assert any('capitolato' in d['filename'].lower() for d in docs)
    
    @pytest.mark.asyncio
    async def test_download_document_success(self):
        """Test successful document download"""
        scraper = ANACPortalScraper()
        
        test_data = b"PDF content here"
        
        with patch.object(scraper, '_get_session') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.read = AsyncMock(return_value=test_data)
            
            mock_session_obj = AsyncMock()
            mock_session_obj.get = AsyncMock(return_value=mock_response)
            mock_session_obj.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session_obj.__aexit__ = AsyncMock()
            
            mock_session.return_value = mock_session_obj
            
            result = await scraper.download_document("https://example.com/doc.pdf")
            
            assert result == test_data
    
    @pytest.mark.asyncio
    async def test_download_document_failure(self):
        """Test document download failure"""
        scraper = ANACPortalScraper()
        
        with patch.object(scraper, '_get_session') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 404
            
            mock_session_obj = AsyncMock()
            mock_session_obj.get = AsyncMock(return_value=mock_response)
            mock_session_obj.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session_obj.__aexit__ = AsyncMock()
            
            mock_session.return_value = mock_session_obj
            
            result = await scraper.download_document("https://example.com/notfound.pdf")
            
            assert result is None
    
    def test_classify_document(self):
        """Test document type classification"""
        scraper = ANACPortalScraper()
        
        assert scraper._classify_document("capitolato_tecnico.pdf") == "specification"
        assert scraper._classify_document("disciplinare_gara.pdf") == "tender_rules"
        assert scraper._classify_document("allegato_1.pdf") == "attachment"
        assert scraper._classify_document("modulo_offerta.pdf") == "form"
        assert scraper._classify_document("unknown.pdf") == "other"
