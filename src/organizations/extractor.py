import logging
import re
import signal
from threading import Event
from typing import Any, Dict, Tuple

from src.config import Config
from src.database.connection import get_db
from src.database.models import Organization, Tender, TenderParticipant
from src.ingestion.client import ANACClient

logger = logging.getLogger(__name__)

_MAX_TAX_ID_LEN = 50
_MIN_TAX_ID_LEN = 5


def normalize_tax_id(raw: Any) -> str:
    """Project Part 2: strip + upper (no insert before this)."""
    if raw is None:
        return ""
    return str(raw).strip().upper()


def is_individual_italian_cf(tax_id: str) -> bool:
    """Italian CF personale: 16 alphanumeric — not an organization."""
    return len(tax_id) == 16 and tax_id.isalnum()


def prepare_org_name(raw: Any) -> Tuple[str, str]:
    """
    Display name (stripped, light cleanup) and normalized_name = UPPER for matching.
    Part 2: name = str(name).strip(); normalized_name = name.upper() after cleanup.
    """
    if raw is None:
        return "", ""
    name = str(raw).strip()
    name = re.sub(r"\*+", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    normalized_name = name.upper()
    return name, normalized_name


class OrganizationExtractor:
    def extract_from_tenders(self, start_date: str, end_date: str, should_stop=None):
        """Extract organizations from tender participants"""
        logger.info(
            "Starting organization extraction from %s to %s", start_date, end_date
        )

        client = ANACClient()

        org_count = 0
        participation_count = 0
        skipped_individual = 0
        skipped_invalid = 0

        stop_event = Event()

        def _on_stop_signal(signum, frame) -> None:
            # Catch SIGTERM from "Stop job" so we can commit partial work.
            logger.info("Stop signal received (%s); stopping extraction gracefully...", signum)
            stop_event.set()

        # Ensure we can gracefully handle termination even when the web layer kills the subprocess.
        signal.signal(signal.SIGTERM, _on_stop_signal)
        try:
            signal.signal(signal.SIGINT, _on_stop_signal)
        except Exception:
            # SIGINT handler might be restricted in some contexts.
            pass

        def _should_stop() -> bool:
            if should_stop is not None:
                try:
                    if should_stop():
                        return True
                except Exception:
                    # If stop callback misbehaves, don't block extraction forever.
                    return True
            return stop_event.is_set()

        with get_db() as db:
            processed_participants = 0
            committed_batches = 0
            last_commit_processed = 0
            stopped_early = False

            for tender_data in client.iter_tenders(
                start_date=start_date,
                end_date=end_date,
                batch_size=Config.INGESTION_BATCH_SIZE,
                should_stop=_should_stop,
            ):
                if _should_stop():
                    stopped_early = True
                    break

                tender = (
                    db.query(Tender)
                    .filter_by(tender_id=tender_data["tender_id"])
                    .first()
                )
                if not tender:
                    logger.warning(
                        "Tender %s not found in database, skipping",
                        tender_data["tender_id"],
                    )
                    continue

                for participant_data in tender_data.get("participants", []):
                    if _should_stop():
                        stopped_early = True
                        break

                    raw_tax = participant_data.get("tax_id")
                    tax_id = normalize_tax_id(raw_tax)
                    processed_participants += 1

                    if not tax_id or len(tax_id) < _MIN_TAX_ID_LEN:
                        logger.info(
                            "SKIP invalid tax_id (empty or len<%s): raw=%r",
                            _MIN_TAX_ID_LEN,
                            raw_tax,
                        )
                        skipped_invalid += 1
                        continue

                    if len(tax_id) > _MAX_TAX_ID_LEN:
                        logger.info(
                            "SKIP invalid tax_id (too long): %s…",
                            tax_id[:20],
                        )
                        skipped_invalid += 1
                        continue

                    if is_individual_italian_cf(tax_id):
                        logger.info(
                            "SKIP individual fiscal code (16-char CF): %s",
                            tax_id,
                        )
                        skipped_individual += 1
                        continue

                    name, normalized_name = prepare_org_name(
                        participant_data.get("name")
                    )
                    if not name:
                        logger.info(
                            "SKIP invalid name (empty after strip): tax_id=%s",
                            tax_id,
                        )
                        skipped_invalid += 1
                        continue

                    org, is_new = self._get_or_create_organization(
                        db, tax_id, name, normalized_name, participant_data
                    )
                    if is_new:
                        org_count += 1

                    existing_participation = (
                        db.query(TenderParticipant)
                        .filter_by(
                            tender_id=tender.id,
                            organization_id=org.id,
                            role=participant_data.get("role", "bidder"),
                        )
                        .first()
                    )

                    if not existing_participation:
                        participation = TenderParticipant(
                            tender_id=tender.id,
                            organization_id=org.id,
                            role=participant_data.get("role", "bidder"),
                            awarded=participant_data.get("awarded", False),
                            award_value=participant_data.get("award_value"),
                        )
                        db.add(participation)
                        participation_count += 1

                    # Incremental commit: keeps already processed work durable if we get stopped/interrupted.
                    if Config.EXTRACT_ORGS_COMMIT_EVERY > 0 and (
                        processed_participants - last_commit_processed
                    ) >= Config.EXTRACT_ORGS_COMMIT_EVERY:
                        db.flush()
                        db.commit()
                        committed_batches += 1
                        last_commit_processed = processed_participants
                        logger.info(
                            "extract-orgs committed batch=%s processed_participants=%s new_orgs=%s new_participations=%s",
                            committed_batches,
                            processed_participants,
                            org_count,
                            participation_count,
                        )
                if stopped_early:
                    break

            # Final commit on graceful stop so the UI count matches persisted state.
            if (stopped_early or processed_participants > last_commit_processed) and (
                processed_participants != last_commit_processed
            ):
                db.flush()
                db.commit()
                committed_batches += 1
                last_commit_processed = processed_participants

            # Make log flags reflect interruption requests even if we happened to finish right after.
            if stop_event.is_set():
                stopped_early = True
            elif should_stop is not None:
                try:
                    if should_stop():
                        stopped_early = True
                except Exception:
                    stopped_early = True

        logger.info(
            "Organization extraction completed: %s new organizations, %s new participations, "
            "%s skipped (individual CF), %s skipped (invalid)",
            org_count,
            participation_count,
            skipped_individual,
            skipped_invalid,
        )

        logger.info(
            "extract-orgs summary: processed_participants=%s committed_batches=%s stopped_early=%s new_orgs=%s new_participations=%s",
            processed_participants,
            committed_batches,
            stopped_early,
            org_count,
            participation_count,
        )
        return {
            "new_organizations": org_count,
            "new_participations": participation_count,
            "skipped_individual_cf": skipped_individual,
            "skipped_invalid": skipped_invalid,
            "committed_batches": committed_batches,
            "stopped_early": stopped_early,
        }

    def _get_or_create_organization(
        self,
        db,
        tax_id: str,
        name: str,
        normalized_name: str,
        org_data: dict,
    ) -> Tuple[Organization, bool]:
        """
        Safe upsert: find by tax_id; on hit increment source_count and skip insert.
        DB unique index on tax_id prevents duplicates.
        """
        existing = db.query(Organization).filter_by(tax_id=tax_id).first()

        if existing:
            logger.info("SKIP duplicate organization: %s", tax_id)
            existing.source_count = (existing.source_count or 0) + 1
            if not existing.normalized_name and normalized_name:
                existing.normalized_name = normalized_name
            return existing, False

        country_raw = org_data.get("country")
        if country_raw:
            cc = str(country_raw).strip().upper()[:2]
        else:
            cc = "IT"

        org = Organization(
            tax_id=tax_id,
            name=name[:500] if len(name) > 500 else name,
            normalized_name=normalized_name,
            source_count=1,
            country=cc,
            city=org_data.get("city"),
            region=org_data.get("region"),
            industry=org_data.get("industry"),
            size=org_data.get("size"),
        )
        db.add(org)
        db.flush()
        logger.info(
            "CREATED organization: tax_id=%s name=%s",
            tax_id,
            org.name,
        )

        return org, True
