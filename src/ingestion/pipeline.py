import logging
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from src.database.connection import get_db, verify_database_connection
from src.database.models import Tender, Issuer
from src.ingestion.client import ANACClient
from src.ingestion.enrichment import TenderEnrichment
from src.config import Config

logger = logging.getLogger(__name__)

class IngestionPipeline:
    def __init__(self):
        self.client = ANACClient()
        self.enrichment = TenderEnrichment()
    
    def run(
        self,
        start_date: str,
        end_date: str,
        should_stop: Optional[Callable[[], bool]] = None,
        job_id: Optional[str] = None,
    ):
        """Run the complete ingestion pipeline"""
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
        if start_dt > end_dt:
            raise ValueError(f"Invalid date range: start_date {start_date} > end_date {end_date}")

        start_ts = time.time()
        logger.info(f"Starting ingestion from {start_date} to {end_date}")

        verify_database_connection()

        fetched_count = 0
        ingested_count = 0
        skipped_count = 0
        error_count = 0
        
        stopped = False

        with get_db() as db:
            for tender_data in self.client.iter_tenders(
                start_date=start_date,
                end_date=end_date,
                batch_size=Config.INGESTION_BATCH_SIZE,
                max_tenders=Config.INGESTION_MAX_TENDERS,
                should_stop=should_stop,
            ):
                if should_stop and should_stop():
                    stopped = True
                    logger.info("Job %s stopped by user", job_id or "unknown")
                    break
                try:
                    fetched_count += 1
                    if self._process_tender(db, tender_data):
                        ingested_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    tender_id = (tender_data or {}).get("tender_id") or "UNKNOWN"
                    logger.error(f"Error processing tender {tender_id}: {e}")
                    error_count += 1
                    # Ensure the SQLAlchemy session is usable for the next tender.
                    db.rollback()
                    continue

            # Ensure portal analysis always has usable inputs even when OCDS is missing
            # `tender.documents[].url` and/or platform IDs were not present in older rows.
            self._backfill_missing_portals(db)
        
        duration_s = time.time() - start_ts

        if stopped:
            logger.info(
                "Ingestion stopped for range %s -> %s. duration_s=%.2f fetched=%s inserted=%s skipped=%s errors=%s",
                start_date,
                end_date,
                duration_s,
                fetched_count,
                ingested_count,
                skipped_count,
                error_count,
            )
            return {
                "status": "stopped",
                "stopped": True,
                "fetched": fetched_count,
                "ingested": ingested_count,
                "inserted": ingested_count,
                "skipped": skipped_count,
                "errors": error_count,
                "duration_s": duration_s,
            }

        if fetched_count == 0:
            logger.warning(
                f"Ingestion completed for range {start_date} -> {end_date}. "
                "Ingestion completed successfully (no tenders yielded). "
                f"duration_s={duration_s:.2f} fetched=0 ingested=0 skipped=0 errors={error_count}"
            )
        elif error_count == 0:
            logger.info(
                f"Ingestion completed for range {start_date} -> {end_date}. "
                "Ingestion completed successfully. "
                f"duration_s={duration_s:.2f} fetched={fetched_count} inserted={ingested_count} skipped={skipped_count} errors=0"
            )
        else:
            logger.warning(
                f"Ingestion completed for range {start_date} -> {end_date}. "
                "Ingestion completed with errors. "
                f"duration_s={duration_s:.2f} fetched={fetched_count} inserted={ingested_count} skipped={skipped_count} errors={error_count}"
            )
        return {
            "status": "completed",
            "stopped": False,
            "fetched": fetched_count,
            "ingested": ingested_count,  # backward-compatible with CLI output
            "inserted": ingested_count,  # alias
            "skipped": skipped_count,
            "errors": error_count,
            "duration_s": duration_s,
        }

    def _backfill_missing_portals(self, db) -> None:
        """
        Local-only backfill for existing rows:
        - If `source_platform` is NULL/empty -> set to 'ANAC'
        - If `document_portal_url` is NULL/empty -> set to the platform base domain
        """
        from sqlalchemy import text

        missing_doc_count = db.execute(
            text("SELECT COUNT(*) FROM tenders WHERE document_portal_url IS NULL OR document_portal_url = ''")
        ).fetchone()[0]

        if missing_doc_count == 0:
            return

        # Default unknown platform to ANAC.
        db.execute(
            text("UPDATE tenders SET source_platform = 'ANAC' WHERE source_platform IS NULL OR source_platform = ''")
        )

        db.execute(
            text(
                """
                UPDATE tenders
                SET document_portal_url = CASE
                    WHEN source_platform = 'MEPA' THEN 'https://acquistinretepa.it'
                    ELSE 'https://pubblicitalegale.anticorruzione.it'
                END
                WHERE document_portal_url IS NULL OR document_portal_url = ''
                """
            )
        )

        logger.info(
            "Backfilled missing document_portal_url rows: %s (tenders with NULL/empty doc urls)",
            missing_doc_count,
        )
    
    def _process_tender(self, db, tender_data: Dict) -> bool:
        """Process a single tender. Returns True if ingested, False if skipped."""
        tender_id = (tender_data or {}).get("tender_id")
        title = (tender_data or {}).get("title")
        issuer_data = (tender_data or {}).get("issuer") or {}

        if not tender_id or not isinstance(tender_id, str):
            logger.debug("SKIP tender_id=%s reason=missing/invalid tender_id", tender_id or "UNKNOWN")
            return False
        if not title or not isinstance(title, str):
            logger.debug("SKIP tender_id=%s reason=missing/invalid title", tender_id)
            return False
        if not issuer_data.get("issuer_id") or not issuer_data.get("name"):
            logger.debug("SKIP tender_id=%s reason=missing issuer fields", tender_id)
            return False

        issuer = self._get_or_create_issuer(db, issuer_data)
        
        summary = self.enrichment.generate_summary(tender_data)
        searchable_text = self.enrichment.generate_searchable_text(tender_data)
        embedding = self.enrichment.generate_embedding(searchable_text)
        
        publication_date = None
        publication_raw = tender_data.get("publication_date")
        if publication_raw:
            publication_dt = self.client.parse_date_safe(publication_raw)
            publication_date = publication_dt.date() if publication_dt else None

        submission_deadline = None
        deadline_raw = tender_data.get("submission_deadline")
        if deadline_raw:
            submission_deadline = self.client.parse_date_safe(deadline_raw)

        insert_stmt = insert(Tender).values(
            tender_id=tender_data["tender_id"],
            title=tender_data["title"],
            issuer_id=issuer.id,
            source_platform=tender_data.get("source_platform"),
            estimated_value=tender_data.get("estimated_value"),
            award_criteria=tender_data.get("award_criteria"),
            publication_date=publication_date,
            submission_deadline=submission_deadline,
            execution_location=tender_data.get("execution_location"),
            nuts_codes=tender_data.get("nuts_codes", []),
            cpv_codes=tender_data.get("cpv_codes", []),
            contract_type=tender_data.get("contract_type"),
            eu_funded=tender_data.get("eu_funded"),
            renewable=tender_data.get("renewable"),
            has_lots=tender_data.get("has_lots", False),
            lots_data=tender_data.get("lots_data"),
            tender_url=tender_data.get("tender_url"),
            document_portal_url=tender_data.get("document_portal_url"),
            summary=summary,
            searchable_text=searchable_text,
            embedding=embedding,
        )
        # Backfill `document_portal_url` on conflict: re-runs should enrich existing tenders
        # when ANAC OCDS is missing `tender.documents`.
        #
        # We only update document_portal_url; other fields stay untouched on conflict.
        insert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["tender_id"],
            set_={
                "source_platform": func.coalesce(
                    insert_stmt.excluded.source_platform,
                    func.nullif(Tender.source_platform, ""),
                ),
                "document_portal_url": func.coalesce(
                    insert_stmt.excluded.document_portal_url,
                    func.nullif(Tender.document_portal_url, ""),
                )
            },
        ).returning(Tender.id)
        inserted_id = db.execute(insert_stmt).scalar()
        db.flush()

        if inserted_id is None:
            logger.debug("SKIP tender_id=%s reason=duplicate (upsert conflict)", tender_id)
            return False

        logger.info("Ingested tender: %s", tender_data["tender_id"])
        return True
    
    def _get_or_create_issuer(self, db, issuer_data: Dict) -> Issuer:
        """Get or create issuer using UPSERT to avoid deadlocks."""
        stmt = insert(Issuer).values(
            issuer_id=issuer_data["issuer_id"],
            name=issuer_data["name"],
            contact_email=issuer_data.get("contact_email"),
            contact_phone=issuer_data.get("contact_phone"),
            address=issuer_data.get("address"),
            city=issuer_data.get("city"),
            region=issuer_data.get("region"),
            nuts_code=issuer_data.get("nuts_code"),
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["issuer_id"]).returning(Issuer.id)
        inserted_id = db.execute(stmt).scalar()
        db.flush()

        if inserted_id is not None:
            issuer = db.get(Issuer, inserted_id)
            if issuer:
                return issuer

        issuer = db.query(Issuer).filter_by(issuer_id=issuer_data["issuer_id"]).first()
        if issuer is None:
            raise RuntimeError(f"Issuer upsert failed for issuer_id={issuer_data['issuer_id']}")
        return issuer
