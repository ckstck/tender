import logging
import os
import tempfile
import time
from typing import Optional
from urllib.parse import urlparse
from pathlib import Path

import requests
from datetime import datetime

from src.config import Config
from src.database.connection import get_db
from src.database.models import Tender, TenderDocument

logger = logging.getLogger(__name__)


_S3_CLIENT_SINGLETON: Optional[object] = None
_S3_CLIENT_BUCKET_READY: bool = False


class DocumentDownloader:
    def __init__(self):
        self.bucket_name = getattr(Config, "S3_BUCKET", "tenders") or "tenders"

        self.s3_enabled = bool(Config.S3_ENDPOINT and Config.S3_ACCESS_KEY)
        self._s3_client = None
        if self.s3_enabled:
            self._s3_client = self._get_s3_client_singleton()
    
    def download_for_portal(self, portal_domain: str, limit: int = 10):
        """
        Download documents from a specific portal domain.
        
        For each tender:
        - if already present in `tender_documents`, skip
        - download content from `tender.document_portal_url`
          - store PDF if response looks like a PDF
          - otherwise fallback to storing HTML
        - upload to object storage (MinIO/S3 bucket `tenders`) or local fallback
        - persist metadata in `tender_documents`
        """
        logger.info("Processing documents from %s (limit=%s)", portal_domain, limit)
        
        with get_db() as db:
            # Use document_portal_url if available; otherwise fallback to tender_url.
            # We store "normalized" document_portal_url so LIKE matching on domain is safe.
            tenders = (
                db.query(Tender)
                .filter(
                    (Tender.document_portal_url.isnot(None) & Tender.document_portal_url.like(f"%{portal_domain}%"))
                    | (
                        Tender.document_portal_url.is_(None)
                        & Tender.tender_url.isnot(None)
                        & Tender.tender_url.like(f"%{portal_domain}%")
                    )
                    | (
                        (Tender.document_portal_url == "")
                        & Tender.tender_url.isnot(None)
                        & Tender.tender_url.like(f"%{portal_domain}%")
                    )
                )
                .limit(limit)
                .all()
            )

            total_candidates = len(tenders)
            skipped_already_processed = 0
            successful_uploads = 0
            failures = 0

            for tender in tenders:
                try:
                    ok, skipped = self._process_tender_documents(db, tender)
                    if skipped:
                        skipped_already_processed += 1
                    elif ok:
                        successful_uploads += 1
                    else:
                        failures += 1
                except Exception as e:
                    logger.exception("Error processing documents for %s: %s", tender.tender_id, e)
                    failures += 1
            
            logger.info(
                "Document download summary for %s: total=%s skipped=%s uploaded=%s failures=%s",
                portal_domain,
                total_candidates,
                skipped_already_processed,
                successful_uploads,
                failures,
            )

            return {
                "portal_domain": portal_domain,
                "total_processed": total_candidates,
                "skipped_already_processed": skipped_already_processed,
                "successful_uploads": successful_uploads,
                "failures": failures,
            }

    def _detect_file_type(self, response: requests.Response) -> str:
        content_type = (response.headers.get("content-type") or "").lower()
        url = (response.url or "").lower()
        body_start = (response.content or b"")[:4]

        # Prefer actual response evidence:
        # - `content-type` contains "pdf"
        # - body magic bytes start with "%PDF"
        # This prevents mislabeling HTML pages served from ".pdf" URLs.
        if "pdf" in content_type or body_start == b"%PDF":
            return "pdf"
        return "html"

    def _upload_file(self, local_path: str, object_key: str, file_name: str) -> str:
        """
        Upload to MinIO/S3 (bucket `tenders`) or store locally as a fallback.
        Returns `storage_path` in the format `tenders/<file_name>`.
        """
        if self.s3_enabled and self._s3_client is not None:
            self._ensure_bucket()

            max_attempts = 4
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    self._s3_client.upload_file(local_path, self.bucket_name, object_key)
                    return f"{self.bucket_name}/{file_name}"
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts:
                        time.sleep(0.8 * attempt)
                        continue
                    raise last_exc

        # Local fallback for dev/testing.
        base_dir = Path(__file__).resolve().parents[2]  # tender/
        storage_dir = base_dir / ".tmp_storage" / self.bucket_name
        storage_dir.mkdir(parents=True, exist_ok=True)
        dest_path = storage_dir / file_name
        with open(local_path, "rb") as src, open(dest_path, "wb") as dst:
            dst.write(src.read())
        # Keep storage_path consistent with the "tenders/<file_name>" requirement.
        return f"{self.bucket_name}/{file_name}"

    def _process_tender_documents(self, db, tender: Tender) -> tuple[bool, bool]:
        """
        Returns: (ok, skipped_already_processed)
        """
        source_url = (tender.document_portal_url or "").strip() if tender.document_portal_url else ""
        if not source_url:
            source_url = (tender.tender_url or "").strip() if tender.tender_url else ""
        if not source_url:
            return False, False

        existing = db.query(TenderDocument).filter_by(tender_id=tender.id).first()
        if existing:
            return True, True

        logger.info("Downloading tender document for %s", tender.tender_id)

        resp = requests.get(
            source_url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (tender-intelligence)"},
        )
        resp.raise_for_status()

        file_type = self._detect_file_type(resp)
        ext = "pdf" if file_type == "pdf" else "html"

        file_name = f"{tender.tender_id}.{ext}"
        object_key = file_name

        # Download to temp file then upload.
        suffix = f".{ext}"
        with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False, dir="/tmp") as tmp:
            tmp.write(resp.content or b"")
            local_path = tmp.name

        try:
            storage_path = self._upload_file(
                local_path, object_key=object_key, file_name=file_name
            )
            doc = TenderDocument(
                tender_id=tender.id,
                file_name=file_name,
                storage_path=storage_path,
                source_url=source_url,
                file_type=file_type,
                created_at=datetime.utcnow(),
            )
            db.add(doc)
            logger.debug("Stored tender_document for %s: %s", tender.tender_id, storage_path)
            return True, False
        finally:
            try:
                os.unlink(local_path)
            except Exception:
                pass

    def _get_s3_client_singleton(self):
        global _S3_CLIENT_SINGLETON
        if _S3_CLIENT_SINGLETON is not None:
            return _S3_CLIENT_SINGLETON

        import boto3
        from botocore.config import Config as BotoConfig

        endpoint_url = Config.S3_ENDPOINT
        verify = True
        if endpoint_url and endpoint_url.startswith("http://"):
            # Local MinIO often uses HTTP and may have self-signed certs if HTTPS is used.
            # Setting verify=False provides the requested "secure=False" behavior for local setups.
            verify = False

        boto_cfg = BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 6, "mode": "standard"},
        )

        _S3_CLIENT_SINGLETON = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=Config.S3_ACCESS_KEY,
            aws_secret_access_key=Config.S3_SECRET_KEY,
            config=boto_cfg,
            verify=verify,
        )

        return _S3_CLIENT_SINGLETON

    def _ensure_bucket(self) -> None:
        global _S3_CLIENT_BUCKET_READY
        if not self.s3_enabled or self._s3_client is None:
            return
        if _S3_CLIENT_BUCKET_READY:
            return

        # Best-effort bucket create.
        try:
            self._s3_client.head_bucket(Bucket=self.bucket_name)
        except Exception:
            try:
                self._s3_client.create_bucket(Bucket=self.bucket_name)
            except Exception:
                # Permission or already-exists; ignore.
                pass

        _S3_CLIENT_BUCKET_READY = True
