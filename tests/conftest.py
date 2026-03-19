import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

@pytest.fixture
def mock_config():
    """Mock configuration for tests"""
    from unittest.mock import Mock
    config = Mock()
    config.MINIO_ENDPOINT = "localhost:9000"
    config.MINIO_ACCESS_KEY = "minioadmin"
    config.MINIO_SECRET_KEY = "minioadmin"
    config.MINIO_BUCKET = "test-bucket"
    config.MINIO_SECURE = False
    return config

@pytest.fixture
def sample_tender_data():
    """Sample tender data for testing"""
    return {
        "tender_id": "TEST-001",
        "title": "Test Tender",
        "estimated_value": 100000.00,
        "publication_date": "2026-03-15",
        "submission_deadline": "2026-04-15T23:59:59",
        "document_portal_url": "https://example.com/tender/123",
        "cpv_codes": ["45233140-2"],
        "nuts_codes": ["ITC4C"],
        "contract_type": "services"
    }

@pytest.fixture
def sample_document_list():
    """Sample document list for testing"""
    return [
        {
            "filename": "capitolato_tecnico.pdf",
            "url": "https://example.com/docs/capitolato.pdf",
            "type": "specification",
            "size": None
        },
        {
            "filename": "disciplinare_gara.pdf",
            "url": "https://example.com/docs/disciplinare.pdf",
            "type": "tender_rules",
            "size": None
        }
    ]
