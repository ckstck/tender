import openai
from typing import List
import logging
from src.config import Config
import hashlib
import random

logger = logging.getLogger(__name__)

class TenderEnrichment:
    def __init__(self):
        openai.api_key = Config.OPENAI_API_KEY
        self.model = Config.OPENAI_MODEL
        self.embedding_model = Config.EMBEDDING_MODEL
    
    def generate_summary(self, tender_data: dict) -> str:
        """Generate a 240-character summary using OpenAI"""
        if not Config.OPENAI_API_KEY:
            logger.warning("No OpenAI API key, using title as summary")
            return tender_data['title'][:240]
        
        try:
            cpv_str = ', '.join(tender_data.get('cpv_codes', [])[:2])
            value_str = f"€{tender_data.get('estimated_value', 'N/A'):,.0f}" if tender_data.get('estimated_value') else 'N/A'
            
            prompt = f"""Summarize this Italian public tender in max 240 characters:
Title: {tender_data['title']}
Type: {tender_data.get('contract_type', 'N/A')}
Value: {value_str}
Location: {tender_data.get('execution_location', 'N/A')}
CPV: {cpv_str}

Summary (max 240 chars):"""
            
            response = openai.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                # The OpenAI Python SDK version in this repo only exposes
                # `max_tokens`, but newer model families may require
                # `max_completion_tokens`. Passing it via `extra_body`
                # avoids SDK signature mismatch.
                extra_body={"max_completion_tokens": 100},
            )
            
            summary = response.choices[0].message.content.strip()
            return summary[:240]
            
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return tender_data['title'][:240]
    
    def generate_searchable_text(self, tender_data: dict) -> str:
        """Create rich searchable text for RAG"""
        issuer_name = tender_data.get('issuer', {}).get('name', 'N/A')
        value = tender_data.get('estimated_value', 0)
        
        parts = [
            f"Tender: {tender_data['title']}",
            f"Issuer: {issuer_name}",
            f"Type: {tender_data.get('contract_type', 'N/A')}",
            f"Value: EUR {value:,.2f}" if value else "Value: N/A",
            f"Location: {tender_data.get('execution_location', 'N/A')}",
            f"CPV Codes: {', '.join(tender_data.get('cpv_codes', []))}",
            f"NUTS Codes: {', '.join(tender_data.get('nuts_codes', []))}",
            f"EU Funded: {'Yes' if tender_data.get('eu_funded') else 'No'}",
            f"Renewable: {'Yes' if tender_data.get('renewable') else 'No'}",
        ]
        
        if tender_data.get('has_lots'):
            lots_info = tender_data.get('lots_data', {}).get('lots', [])
            parts.append(f"Lots: {len(lots_info)} lots available")
        
        return " | ".join(parts)
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding vector using OpenAI"""
        if not Config.OPENAI_API_KEY:
            # Deterministic fallback for demo/reproducibility:
            # - avoids `nan` similarity results caused by zero vectors
            # - keeps output stable across machines/runs
            logger.warning(
                "No OpenAI API key, using deterministic pseudo-embedding fallback"
            )
            normalized = text if isinstance(text, str) else str(text)
            seed = int(hashlib.sha256(normalized.encode("utf-8")).hexdigest(), 16) % (2**32)
            rng = random.Random(seed)
            return [rng.uniform(-0.05, 0.05) for _ in range(Config.EMBEDDING_DIMENSIONS)]
        
        try:
            response = openai.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
            
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return [0.0] * Config.EMBEDDING_DIMENSIONS
