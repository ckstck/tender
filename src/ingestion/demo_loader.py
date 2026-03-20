import csv
import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.dialects.postgresql import insert

from src.config import Config
from src.database.connection import get_db
from src.database.models import Issuer, Tender, Organization, TenderParticipant

logger = logging.getLogger(__name__)


_DEFAULT_DOC_PORTAL_URL = "https://pubblicitalegale.anticorruzione.it"


def _empty_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value if value != "" else None


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    v = _empty_to_none(value)
    if v is None:
        return None
    return v.lower() in {"true", "1", "t", "yes", "y"}


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    v = _empty_to_none(value)
    if v is None:
        return None
    try:
        return Decimal(v)
    except Exception:
        return None


def _parse_date(value: Optional[str]):
    v = _empty_to_none(value)
    if not v:
        return None
    try:
        # CSV uses YYYY-MM-DD
        return datetime.fromisoformat(v).date()
    except Exception:
        return None


def _parse_timestamp(value: Optional[str]):
    v = _empty_to_none(value)
    if not v:
        return None
    try:
        # CSV uses ISO timestamps like 2026-03-20T03:01:30.089654
        return datetime.fromisoformat(v)
    except Exception:
        return None


def _parse_json_field(value: Optional[str]) -> Any:
    v = _empty_to_none(value)
    if v is None:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def _parse_json_array(value: Optional[str]) -> List[str]:
    v = _empty_to_none(value)
    if v is None:
        return []
    if v == "[]":
        return []
    # CSV stores arrays like: ["72510000"]
    parsed = _parse_json_field(v)
    return parsed if isinstance(parsed, list) else []


def _parse_embedding(value: Optional[str]) -> List[float]:
    v = _empty_to_none(value)
    if v is None:
        # Ensure we always provide the correct dim length.
        return [0.0] * Config.EMBEDDING_DIMENSIONS
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list) and len(parsed) == Config.EMBEDDING_DIMENSIONS:
            return [float(x) for x in parsed]
    except Exception:
        pass
    # Fallback if the CSV is unexpectedly formatted.
    return [0.0] * Config.EMBEDDING_DIMENSIONS


def load_demo_data() -> Dict[str, int]:
    """
    Load deterministic demo dataset from `db_dumps/` into PostgreSQL.

    This allows the demo to run even if ANAC endpoints are blocked by WAF/rate limiting.
    """
    repo_root = Path(__file__).resolve().parents[2]  # tender/
    dumps_dir = repo_root / "db_dumps"
    issuers_path = dumps_dir / "issuers.csv"
    tenders_path = dumps_dir / "tenders.csv"

    if not issuers_path.exists() or not tenders_path.exists():
        raise FileNotFoundError(
            f"Missing demo dumps: expected {issuers_path} and {tenders_path}"
        )

    issuers_rows: List[Dict[str, Any]] = []
    tenders_rows: List[Dict[str, Any]] = []

    with issuers_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            issuers_rows.append(
                {
                    "id": int(row["id"]),
                    "issuer_id": row["issuer_id"],
                    "name": row["name"],
                    "contact_email": _empty_to_none(row.get("contact_email")),
                    "contact_phone": _empty_to_none(row.get("contact_phone")),
                    "address": _empty_to_none(row.get("address")),
                    "city": _empty_to_none(row.get("city")),
                    "region": _empty_to_none(row.get("region")),
                    "nuts_code": _empty_to_none(row.get("nuts_code")),
                    "organization_type": _empty_to_none(row.get("organization_type")),
                    "created_at": _parse_timestamp(row.get("created_at")),
                }
            )

    with tenders_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Offline dumps do not include document URLs. For demo purposes we set
            # a stable portal URL so `download-documents` can still run end-to-end.
            doc_url = _empty_to_none(row.get("document_portal_url"))
            if not doc_url:
                doc_url = _DEFAULT_DOC_PORTAL_URL

            tenders_rows.append(
                {
                    "id": int(row["id"]),
                    "tender_id": row["tender_id"],
                    "title": row["title"],
                    "issuer_id": int(row["issuer_id"]),
                    "estimated_value": _parse_decimal(row.get("estimated_value")),
                    "currency": _empty_to_none(row.get("currency")) or "EUR",
                    "award_criteria": _parse_json_field(row.get("award_criteria")),
                    "publication_date": _parse_date(row.get("publication_date")),
                    "submission_deadline": _parse_timestamp(row.get("submission_deadline")),
                    "execution_start_date": _parse_date(row.get("execution_start_date")),
                    "execution_end_date": _parse_date(row.get("execution_end_date")),
                    "execution_location": _empty_to_none(row.get("execution_location")),
                    "nuts_codes": _parse_json_array(row.get("nuts_codes")),
                    "cpv_codes": _parse_json_array(row.get("cpv_codes")),
                    "contract_type": _empty_to_none(row.get("contract_type")),
                    "eu_funded": _parse_bool(row.get("eu_funded")),
                    "renewable": _parse_bool(row.get("renewable")),
                    "has_lots": bool(_parse_bool(row.get("has_lots"))),
                    "lots_data": _parse_json_field(row.get("lots_data")),
                    "tender_url": _empty_to_none(row.get("tender_url")) or doc_url,
                    "document_portal_url": doc_url,
                    "source_platform": row.get("source_platform") or "ANAC",
                    "summary": _empty_to_none(row.get("summary")) or "",
                    "searchable_text": _empty_to_none(row.get("searchable_text")) or "",
                    "embedding": _parse_embedding(row.get("embedding")),
                    "created_at": _parse_timestamp(row.get("created_at")),
                    "updated_at": _parse_timestamp(row.get("updated_at")),
                }
            )

    with get_db() as db:
        # 1) Upsert issuers
        if issuers_rows:
            stmt = insert(Issuer).values(issuers_rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["issuer_id"],
                set_={
                    "name": stmt.excluded.name,
                    "contact_email": stmt.excluded.contact_email,
                    "contact_phone": stmt.excluded.contact_phone,
                    "address": stmt.excluded.address,
                    "city": stmt.excluded.city,
                    "region": stmt.excluded.region,
                    "nuts_code": stmt.excluded.nuts_code,
                    "organization_type": stmt.excluded.organization_type,
                    "created_at": stmt.excluded.created_at,
                },
            )
            db.execute(stmt)

        # 2) Upsert tenders (includes embeddings/searchable_text/summary)
        if tenders_rows:
            stmt = insert(Tender).values(tenders_rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["tender_id"],
                set_={
                    "issuer_id": stmt.excluded.issuer_id,
                    "estimated_value": stmt.excluded.estimated_value,
                    "currency": stmt.excluded.currency,
                    "award_criteria": stmt.excluded.award_criteria,
                    "publication_date": stmt.excluded.publication_date,
                    "submission_deadline": stmt.excluded.submission_deadline,
                    "execution_start_date": stmt.excluded.execution_start_date,
                    "execution_end_date": stmt.excluded.execution_end_date,
                    "execution_location": stmt.excluded.execution_location,
                    "nuts_codes": stmt.excluded.nuts_codes,
                    "cpv_codes": stmt.excluded.cpv_codes,
                    "contract_type": stmt.excluded.contract_type,
                    "eu_funded": stmt.excluded.eu_funded,
                    "renewable": stmt.excluded.renewable,
                    "has_lots": stmt.excluded.has_lots,
                    "lots_data": stmt.excluded.lots_data,
                    "tender_url": stmt.excluded.tender_url,
                    "document_portal_url": stmt.excluded.document_portal_url,
                    "source_platform": stmt.excluded.source_platform,
                    "summary": stmt.excluded.summary,
                    "searchable_text": stmt.excluded.searchable_text,
                    "embedding": stmt.excluded.embedding,
                    "created_at": stmt.excluded.created_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            db.execute(stmt)

        # 3) Create organizations + tender_participants based on issuers (demo UX)
        # Organizations are deduplicated by tax_id. We reuse `issuer_id` as a stable tax_id.
        tax_ids = [i["issuer_id"] for i in issuers_rows]
        if tax_ids:
            existing_orgs = db.query(Organization).filter(Organization.tax_id.in_(tax_ids)).all()
            existing_tax_ids = {o.tax_id for o in existing_orgs}

            new_orgs = []
            for issuer in issuers_rows:
                if issuer["issuer_id"] in existing_tax_ids:
                    continue
                name = issuer["name"]
                new_orgs.append(
                    Organization(
                        tax_id=issuer["issuer_id"],
                        name=name,
                        normalized_name=name.upper() if isinstance(name, str) else None,
                        source_count=1,
                        country=None,
                        city=issuer.get("city"),
                        region=issuer.get("region"),
                        industry=None,
                        size=None,
                    )
                )
            if new_orgs:
                db.add_all(new_orgs)
                db.flush()

        # Refresh mapping after inserts/updates.
        orgs = db.query(Organization).filter(Organization.tax_id.in_(tax_ids)).all()
        org_by_tax_id = {o.tax_id: o for o in orgs}

        # Link each tender to the org derived from its issuer_id.
        # We reuse `tenders_rows` to keep the mapping stable.
        issuer_by_db_id = {i["id"]: i for i in issuers_rows}
        participants_added = 0

        # Insert only when missing to keep the loader repeatable.
        for t in tenders_rows:
            issuer = issuer_by_db_id.get(t["issuer_id"])
            if not issuer:
                continue
            org = org_by_tax_id.get(issuer["issuer_id"])
            if not org:
                continue

            existing = (
                db.query(TenderParticipant)
                .filter_by(tender_id=t["id"], organization_id=org.id, role="bidder")
                .first()
            )
            if existing:
                continue

            db.add(
                TenderParticipant(
                    tender_id=t["id"],
                    organization_id=org.id,
                    role="bidder",
                    awarded=False,
                    award_value=None,
                    created_at=datetime.utcnow(),
                )
            )
            participants_added += 1

        db.flush()

        # Return final counts for demo verification.
        return {
            "issuers_loaded": db.query(Issuer).count(),
            "tenders_loaded": db.query(Tender).count(),
            "organizations_loaded": db.query(Organization).count(),
            "tender_participants_loaded": db.query(TenderParticipant).count(),
            "tender_participants_added": participants_added,
        }

