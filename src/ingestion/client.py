import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict
import logging
from pathlib import Path
from src.config import Config

logger = logging.getLogger(__name__)

class ANACClient:
    def __init__(self):
        self.base_url = Config.ANAC_BASE_URL
        self.api_key = Config.ANAC_API_KEY
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({'Authorization': f'Bearer {self.api_key}'})
    
    def fetch_tenders(self, days_back: int = 30) -> List[Dict]:
        """
        Fetch tenders from ANAC API for the last N days.
        Falls back to mock data if API is unavailable.
        """
        logger.info(f"Fetching tenders from last {days_back} days")
        
        try:
            tenders = self._fetch_from_api(days_back)
            if tenders:
                logger.info(f"Fetched {len(tenders)} tenders from API")
                return tenders
        except Exception as e:
            logger.warning(f"API fetch failed: {e}, falling back to mock data")
        
        return self._load_mock_tenders()
    
    def _fetch_from_api(self, days_back: int) -> List[Dict]:
        """
        Attempt to fetch from ANAC API.
        Returns empty list if unavailable.
        """
        date_from = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        endpoint = f"{self.base_url}/tenders"
        params = {
            'date_from': date_from,
            'format': 'json'
        }
        
        response = self.session.get(endpoint, params=params, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.warning(f"API returned status {response.status_code}")
            return []
    
    def _load_mock_tenders(self) -> List[Dict]:
        """Load mock tender data from JSON file"""
        mock_file = Path(__file__).parent.parent.parent / 'data' / 'mock_tenders.json'
        
        if not mock_file.exists():
            logger.error(f"Mock data file not found: {mock_file}")
            return []
        
        with open(mock_file, 'r', encoding='utf-8') as f:
            tenders = json.load(f)
        
        logger.info(f"Loaded {len(tenders)} mock tenders")
        return tenders
