import logging
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterator, List, Optional, Set

import ijson
import requests
from urllib.parse import urlparse, urlunparse

from src.config import Config

logger = logging.getLogger(__name__)


class ANACClient:
    """
    ANAC Open Data client for OCDS data.

    Strategy:
    - Try `/opendata/records` first (as requested).
    - If that endpoint is blocked by WAF / returns non-JSON, fall back to the portal monthly
      bulk filesystem JSON releases.
    - Do not use mock fallback.
    """

    def __init__(self):
        self.base_url = Config.ANAC_BASE_URL
        self.api_key = Config.ANAC_API_KEY
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://dati.anticorruzione.it/",
                "Origin": "https://dati.anticorruzione.it",
            }
        )
        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    @staticmethod
    def parse_date_safe(date_str: Optional[str]) -> Optional[datetime]:
        if not date_str or not isinstance(date_str, str):
            return None

        normalized = date_str.strip()
        if not normalized:
            return None

        formats = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y",
            "%d/%m/%Y %H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(normalized, fmt)
            except Exception:
                continue

        # Best-effort support for ISO timestamps (e.g. 2026-03-01T10:30:00Z)
        try:
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            return datetime.fromisoformat(normalized).replace(tzinfo=None)
        except Exception:
            return None

    # Fallback portal sources (real-world pages).
    # Used when OCDS doesn't provide tender.documents[].url.
    ANAC_UI_BASE_URL = "https://pubblicitalegale.anticorruzione.it"
    MEPA_BASE_URL = "https://www.acquistinretepa.it"

    @staticmethod
    def detect_source_platform(
        tender_id: Optional[str],
        tender_url: Optional[str],
        title: Optional[str],
    ) -> str:
        """
        Best-effort platform detection.
        We only need this to choose a fallback portal base domain.
        """
        haystack = " ".join(
            [
                tender_id or "",
                tender_url or "",
                title or "",
            ]
        ).lower()

        if "acquistinretepa" in haystack or "mepa" in haystack:
            return "MEPA"
        return "ANAC"

    @staticmethod
    def normalize_portal_domain(url: str) -> str:
        """
        Extract a normalized domain for portal counting.
        - lowercase
        - remove leading `www.`
        - ignore query params/fragments
        """
        if not url or not isinstance(url, str):
            return ""

        candidate = url.strip()
        if not candidate:
            return ""

        if not (candidate.startswith("http://") or candidate.startswith("https://")):
            candidate = "https://" + candidate

        parsed = urlparse(candidate)
        host = (parsed.netloc or "").strip().lower()
        if host.startswith("www."):
            host = host[len("www.") :]
        return host

    @staticmethod
    def normalize_portal_url(url: str) -> Optional[str]:
        """
        Canonicalize URL for storage in `tenders.document_portal_url`.
        Removes query/fragment and removes `www.` from host.
        """
        if not url or not isinstance(url, str):
            return None

        candidate = url.strip()
        if not candidate:
            return None

        if not (candidate.startswith("http://") or candidate.startswith("https://")):
            candidate = "https://" + candidate

        parsed = urlparse(candidate)
        domain = ANACClient.normalize_portal_domain(candidate)
        if not domain:
            return None

        # Keep path only (no query/fragment).
        path = parsed.path or ""
        # Ensure path is stable: drop empty path to return https://domain.
        if path == "":
            return f"https://{domain}"
        return urlunparse(("https", domain, path, "", "", ""))

    def _get_with_retries(
        self,
        url: str,
        *,
        params: Optional[Dict] = None,
        stream: bool = False,
        timeout: int = 30,
        context: str = "request",
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Optional[requests.Response]:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            if should_stop and should_stop():
                logger.info("Stopping: cancellation requested during %s", context)
                return None
            try:
                response = self.session.get(url, params=params, timeout=timeout, stream=stream)
            except Exception as exc:
                logger.warning("%s attempt %s/%s failed: %s", context, attempt, max_attempts, exc)
                if attempt < max_attempts:
                    if should_stop and should_stop():
                        logger.info("Stopping: cancellation requested during %s", context)
                        return None
                    time.sleep(2 ** (attempt - 1))
                continue

            content_type = (response.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                snippet = ""
                if not stream:
                    snippet = (response.text or "")[:300]
                logger.error(
                    "%s returned HTML (WAF suspected) attempt %s/%s status=%s snippet=%r",
                    context,
                    attempt,
                    max_attempts,
                    response.status_code,
                    snippet,
                )
                response.close()
                if attempt < max_attempts:
                    if should_stop and should_stop():
                        logger.info("Stopping: cancellation requested during %s", context)
                        return None
                    time.sleep(2 ** (attempt - 1))
                continue

            return response

        logger.error("%s failed after %s attempts; skipping safely.", context, max_attempts)
        return None

    def iter_tenders(
        self,
        start_date: str,
        end_date: str,
        batch_size: int = None,
        max_tenders: Optional[int] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[Dict]:
        """
        Stream tenders in batches using offset-based pagination from `/records`.

        If `/records` is blocked/non-JSON, falls back to streaming OCDS monthly bulk
        JSON files without loading everything in memory.
        """
        if batch_size is None:
            batch_size = Config.INGESTION_BATCH_SIZE

        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
        if start_dt > end_dt:
            raise ValueError(f"Invalid date range: start_date {start_date} > end_date {end_date}")

        date_from = start_dt.strftime("%Y-%m-%d")
        date_to = end_dt.strftime("%Y-%m-%d")

        seen_tender_ids: Set[str] = set()
        yielded = 0
        # Track how we populated Tender.document_portal_url during normalization.
        # "documents" means tender.documents[].url was present.
        # "compiled_release" means we used OCDS compiledRelease uri/url.
        # "fallback_platform_base" means we used the platform base URL.
        doc_portal_total = 0
        doc_portal_from_documents = 0
        doc_portal_from_compiled_release = 0
        doc_portal_from_fallback_platform_base = 0
        unique_domains: Set[str] = set()

        # 1) Try `/records` with offset-based pagination.
        used_records = False
        page_offset = 0
        page_number = 0
        max_pages = 1000
        pages_with_no_new_tenders = 0
        max_pages_without_new = 3
        while page_number < max_pages:
            if should_stop and should_stop():
                logger.info("Stopping: cancellation requested during page scan")
                return
            page_number += 1
            endpoint = f"{self.base_url}/records"
            params = {
                "date_from": date_from,
                "date_to": date_to,
                "format": "json",
                "limit": batch_size,
                "offset": page_offset,
            }

            response = self._get_with_retries(
                endpoint,
                params=params,
                context=f"/records page offset={page_offset}",
                should_stop=should_stop,
            )
            if response is None:
                # Skip this API branch safely and move to bulk fallback.
                if should_stop and should_stop():
                    return
                break

            body_text = response.text or ""
            body_snippet = body_text[:300]

            content_type = (response.headers.get("content-type") or "").lower()
            body_lstrip = body_text.lstrip()
            looks_like_json = (
                "application/json" in content_type
                or body_lstrip.startswith("{")
                or body_lstrip.startswith("[")
            )

            # Detect WAF / HTML blocks even when HTTP status is 200.
            blocked_by_waf = (
                "request rejected" in body_text.lower()
                or "<html" in body_lstrip.lower()
                or "text/html" in content_type
            )

            if response.status_code != 200 or blocked_by_waf or not looks_like_json:
                if blocked_by_waf:
                    logger.error(
                        "ANAC /records blocked by WAF (or HTML). "
                        f"status={response.status_code} content_type={content_type!r} snippet={body_snippet!r}"
                    )
                    # Force bulk fallback even if we already yielded some tenders.
                    used_records = False
                # Records endpoint blocked/unusable.
                break

            payload = response.json()
            if isinstance(payload, dict):
                records = payload.get("records") or payload.get("data") or []
            elif isinstance(payload, list):
                records = payload
            else:
                records = []

            if not records:
                # No more pages.
                break

            used_records = True
            new_in_page = 0
            outside_range_log_count = 0

            for record in records:
                if should_stop and should_stop():
                    logger.info("Stopping: cancellation requested during record scan")
                    return
                compiled_release = self._extract_compiled_release(record)
                if not compiled_release:
                    continue
                tender = self._normalize_compiled_release(compiled_release)
                if not tender:
                    continue

                # Strict date filtering: publication_date must be within the window.
                pub = tender.get("publication_date")
                pub_date = self.parse_date_safe(pub)
                if not pub_date:
                    logger.info(
                        "Skipping tender with missing/invalid publication_date: tender_id=%s publication_date=%r",
                        tender.get("tender_id"),
                        pub,
                    )
                    continue
                if pub_date < start_dt:
                    logger.info("Stopping: reached older than start_date")
                    return
                if pub_date > end_dt:
                    if outside_range_log_count < 5:
                        logger.info(
                            "Skipping tender outside date range: tender_id=%s publication_date=%s",
                            tender.get("tender_id"),
                            pub,
                        )
                        outside_range_log_count += 1
                    continue

                tid = tender.get("tender_id")
                if not tid or tid in seen_tender_ids:
                    continue
                seen_tender_ids.add(tid)
                yield tender
                yielded += 1
                doc_portal_total += 1
                src = tender.get("document_portal_url_source")
                if src == "documents":
                    doc_portal_from_documents += 1
                elif src == "compiled_release":
                    doc_portal_from_compiled_release += 1
                elif src == "fallback_platform_base":
                    doc_portal_from_fallback_platform_base += 1

                # Keep track of unique domains for observability.
                doc_url = tender.get("document_portal_url")
                domain = self.normalize_portal_domain(doc_url) if isinstance(doc_url, str) else ""
                if domain:
                    unique_domains.add(domain)
                new_in_page += 1

                if max_tenders is not None and yielded >= max_tenders:
                    logger.info(
                        "Document portal URL population (records, early stop): total=%s from_documents=%s from_compiled_release=%s from_fallback_platform_base=%s unique_domains=%s",
                        doc_portal_total,
                        doc_portal_from_documents,
                        doc_portal_from_compiled_release,
                        doc_portal_from_fallback_platform_base,
                        len(unique_domains),
                    )
                    return

            # If we keep getting pages but no new in-window tenders, stop early.
            if new_in_page == 0:
                pages_with_no_new_tenders += 1
                if pages_with_no_new_tenders >= max_pages_without_new:
                    logger.warning(
                        "ANAC /records returned pages but no new in-window tenders. Stopping pagination."
                    )
                    break
            else:
                pages_with_no_new_tenders = 0

            # Offset-based pagination: advance by how many records we actually received.
            page_offset += len(records)

        if page_number >= max_pages:
            logger.warning("Stopping: reached max page limit (%s)", max_pages)

        # 2) Fallback: stream OCDS monthly bulk files.
        # Only do this if `/records` didn't yield anything.
        if used_records and yielded > 0:
            logger.info(
                "Document portal URL population (records): total=%s from_documents=%s from_compiled_release=%s from_fallback_platform_base=%s unique_domains=%s",
                doc_portal_total,
                doc_portal_from_documents,
                doc_portal_from_compiled_release,
                doc_portal_from_fallback_platform_base,
                len(unique_domains),
            )
            return

        if used_records and yielded == 0:
            logger.info(
                "Document portal URL population (records yielded none): total=%s from_documents=%s from_compiled_release=%s from_fallback_platform_base=%s unique_domains=%s",
                doc_portal_total,
                doc_portal_from_documents,
                doc_portal_from_compiled_release,
                doc_portal_from_fallback_platform_base,
                len(unique_domains),
            )
            # Endpoint returned pages, but none normalized into valid tenders.
            return

        # Bulk fallback: scan OCDS monthly bulk releases with strict date filtering and bounded work.
        months_span = ((end_dt.year - start_dt.year) * 12) + (end_dt.month - start_dt.month) + 1
        max_months_to_try = max(3, months_span + 1)
        y, m = end_dt.year, end_dt.month
        months_tried = 0
        yielded_in_bulk = 0
        outside_range_log_count = 0

        while months_tried < max_months_to_try:
            if should_stop and should_stop():
                logger.info("Stopping: cancellation requested during month scan")
                return
            month_start = datetime(y, m, 1)
            if month_start < start_dt:
                logger.info(
                    "Stopping: month before start_date (month=%04d-%02d, start_date=%s)",
                    y,
                    m,
                    start_date,
                )
                break

            month_label = f"{y}-{m:02d}"
            url = self._bulk_url(y, m)

            try:
                logger.info("Fetching month %s", month_label)
                if not self._bulk_month_exists(url):
                    raise FileNotFoundError("bulk month does not exist")

                resp = self._get_with_retries(
                    url,
                    stream=True,
                    context=f"bulk month {month_label}",
                    should_stop=should_stop,
                )
                if resp is None:
                    # Skip this month safely.
                    if should_stop and should_stop():
                        return
                    continue
                if resp.status_code != 200:
                    resp.close()
                    raise FileNotFoundError(f"bulk month returned {resp.status_code}")

                month_yielded = 0
                for release in ijson.items(resp.raw, "releases.item"):
                    if should_stop and should_stop():
                        logger.info("Stopping: cancellation requested during bulk release scan")
                        resp.close()
                        return
                    if not isinstance(release, dict):
                        continue

                    tender = self._normalize_compiled_release(release)
                    if not tender:
                        continue

                    # Strict date filtering by publication_date.
                    pub = tender.get("publication_date")
                    pub_date = self.parse_date_safe(pub)
                    if not pub_date:
                        logger.info(
                            "Skipping tender with missing/invalid publication_date: tender_id=%s publication_date=%r",
                            tender.get("tender_id"),
                            pub,
                        )
                        continue
                    if pub_date < start_dt:
                        logger.info("Stopping: reached older than start_date")
                        resp.close()
                        return
                    if pub_date > end_dt:
                        if outside_range_log_count < 5:
                            logger.info(
                                "Skipping tender outside date range: tender_id=%s publication_date=%s",
                                tender.get("tender_id"),
                                pub,
                            )
                            outside_range_log_count += 1
                        continue

                    tid = tender.get("tender_id")
                    if not tid or tid in seen_tender_ids:
                        continue

                    seen_tender_ids.add(tid)
                    yield tender
                    yielded += 1
                    yielded_in_bulk += 1
                    doc_portal_total += 1
                    src = tender.get("document_portal_url_source")
                    if src == "documents":
                        doc_portal_from_documents += 1
                    elif src == "compiled_release":
                        doc_portal_from_compiled_release += 1
                    elif src == "fallback_platform_base":
                        doc_portal_from_fallback_platform_base += 1

                    # Track unique domains for observability.
                    doc_url = tender.get("document_portal_url")
                    domain = self.normalize_portal_domain(doc_url) if isinstance(doc_url, str) else ""
                    if domain:
                        unique_domains.add(domain)
                    month_yielded += 1

                    if max_tenders is not None and yielded >= max_tenders:
                        resp.close()
                        logger.info(
                            "Document portal URL population (bulk, early stop): total=%s from_documents=%s from_compiled_release=%s from_fallback_platform_base=%s unique_domains=%s",
                            doc_portal_total,
                            doc_portal_from_documents,
                            doc_portal_from_compiled_release,
                            doc_portal_from_fallback_platform_base,
                            len(unique_domains),
                        )
                        return

                resp.close()
            except Exception as e:
                logger.warning(f"Bulk fetch/parse failed for {month_label}: {e}")
            finally:
                months_tried += 1
                m -= 1
                if m == 0:
                    m = 12
                    y -= 1

            if max_tenders is not None and yielded >= max_tenders:
                return

        if yielded_in_bulk == 0:
            logger.info(
                "Bulk fallback completed for the bounded window but yielded no valid tenders."
            )
        else:
            logger.info(
                "Document portal URL population (bulk): total=%s from_documents=%s from_compiled_release=%s from_fallback_platform_base=%s unique_domains=%s",
                doc_portal_total,
                doc_portal_from_documents,
                doc_portal_from_compiled_release,
                doc_portal_from_fallback_platform_base,
                len(unique_domains),
            )

    def fetch_tenders(
        self,
        start_date: str,
        end_date: str,
        batch_size: int = None,
        max_tenders: Optional[int] = 100,
    ) -> List[Dict]:
        """
        Non-streaming convenience wrapper.

        Prefer `iter_tenders()` for production/large runs.
        """
        return list(
            self.iter_tenders(
                start_date=start_date,
                end_date=end_date,
                batch_size=batch_size,
                max_tenders=max_tenders,
            )
        )

    def _fetch_from_api_or_bulk(self, days_back: int, tender_limit: int) -> List[Dict]:
        date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        # 1) Requested endpoint: `/records`
        tenders: List[Dict] = []
        seen_tender_ids = set()
        try:
            endpoint = f"{self.base_url}/records"
            params = {
                # Keep parameters minimal; the portal may require dataset-specific params
                # and/or ignore unknown ones. We'll still print debug info.
                "date_from": date_from,
                "format": "json",
                "limit": tender_limit,
            }
            response = self.session.get(endpoint, params=params, timeout=30)

            # Required debug info: status code + first 300 chars
            body_snippet = (response.text or "")[:300]
            print(f"[ANACClient] records status={response.status_code}")
            print(f"[ANACClient] records body[0:300]={body_snippet!r}")

            # Some WAF pages return 200 with HTML; treat it as failure.
            content_type = (response.headers.get("content-type") or "").lower()
            body_lstrip = (response.text or "").lstrip()
            looks_like_json = (
                "application/json" in content_type
                or body_lstrip.startswith("{")
                or body_lstrip.startswith("[")
            )
            if response.status_code == 200 and looks_like_json:
                payload = response.json()
                records = None
                if isinstance(payload, dict):
                    records = payload.get("records") or payload.get("data")
                if records is None and isinstance(payload, list):
                    records = payload

                records = records or []
                for record in records[:tender_limit]:
                    compiled_release = self._extract_compiled_release(record)
                    if not compiled_release:
                        continue
                    tender = self._normalize_compiled_release(compiled_release)
                    if not tender:
                        continue
                    tid = tender.get("tender_id")
                    if tid in seen_tender_ids:
                        continue
                    seen_tender_ids.add(tid)
                    tenders.append(tender)

                if tenders:
                    return tenders
            else:
                logger.warning(
                    "ANAC /records blocked or non-JSON; falling back to bulk releases "
                    f"(status={response.status_code}, content_type={content_type!r})."
                )
        except Exception as e:
            logger.warning(f"ANAC /records fetch failed; will use bulk releases. Error: {e}")

        # 2) Bulk fallback (still not mock; uses real OCDS releases JSON files)
        return self._fetch_from_bulk(days_back=days_back, tender_limit=tender_limit)

    def _fetch_from_bulk(self, days_back: int, tender_limit: int) -> List[Dict]:
        now = datetime.now()
        start = now - timedelta(days=days_back)

        # Bulk files can lag; keep stepping back a bit until we find data.
        # With streaming parsing, this should be fast enough to not hang.
        max_months_to_try = 30
        # We want results to come from multiple valid months when possible.
        # This prevents "first valid month wins" behaviour.
        min_months_contribute = min(3, max_months_to_try)
        base_month_cap = max(1, tender_limit // max(1, min_months_contribute))

        y, m = now.year, now.month
        months_tried = 0
        tenders: List[Dict] = []
        seen_tender_ids = set()

        while months_tried < max_months_to_try and len(tenders) < tender_limit:
            month_label = f"{y}-{m:02d}"
            url = self._bulk_url(y, m)
            try:
                logger.info(f"Fetching OCDS bulk releases: {month_label}")

                # Only request months that exist. `HEAD` is unreliable here (returns 200 HTML),
                # so use a tiny ranged GET (should be 206 for partial content, 404 otherwise).
                if not self._bulk_month_exists(url):
                    months_tried += 1
                    m -= 1
                    if m == 0:
                        m = 12
                        y -= 1
                    continue

                resp = self.session.get(url, timeout=30, stream=True)
                print(f"[ANACClient] bulk status={resp.status_code} url={month_label}")

                if resp.status_code != 200:
                    resp.close()
                    months_tried += 1
                    m -= 1
                    if m == 0:
                        m = 12
                        y -= 1
                    continue

                # Stream-parse only `releases` array; stop as soon as we have enough tenders.
                remaining = tender_limit - len(tenders)
                month_cap = min(base_month_cap, remaining)
                month_taken = 0
                for release in ijson.items(resp.raw, "releases.item"):
                    if not isinstance(release, dict):
                        continue
                    tender = self._normalize_compiled_release(release)
                    if not tender:
                        continue
                    tid = tender.get("tender_id")
                    if tid in seen_tender_ids:
                        continue
                    seen_tender_ids.add(tid)
                    tenders.append(tender)
                    month_taken += 1
                    if len(tenders) >= tender_limit:
                        resp.close()
                        return tenders
                    if month_taken >= month_cap:
                        break
                resp.close()
            except Exception as e:
                logger.warning(f"Bulk fetch/parse failed for {month_label}: {e}")
            finally:
                months_tried += 1
                m -= 1
                if m == 0:
                    m = 12
                    y -= 1

        if not tenders:
            raise RuntimeError(
                "ANAC OCDS bulk fetch returned no tenders. "
                f"Tried up to {max_months_to_try} months backwards from {now.date()} "
                f"(days_back={days_back})."
            )

        return tenders

    def _bulk_url(self, year: int, month: int) -> str:
        # Confirmed working pattern for this portal:
        # https://dati.anticorruzione.it/opendata/download/dataset/ocds/filesystem/bulk/{YYYY}/{MM}.json
        return (
            f"{self.base_url}/download/dataset/ocds/filesystem/bulk/"
            f"{year:04d}/{month:02d}.json"
        )

    def _bulk_month_exists(self, url: str) -> bool:
        """
        Fast existence check using a ranged GET.
        - Valid JSON months should return 206 Partial Content.
        - Missing months return 404.
        """
        try:
            resp = self.session.get(
                url,
                headers={"Range": "bytes=0-0"},
                timeout=15,
                stream=False,
            )
            return resp.status_code in (206, 200)
        except Exception:
            return False

    def _extract_compiled_release(self, record: Dict) -> Optional[Dict]:
        """
        Extract compiledRelease from a single OCDS record.
        """
        if not isinstance(record, dict):
            return None

        compiled_release = record.get("compiledRelease")
        if compiled_release:
            return compiled_release

        # Sometimes nested under other keys
        for key in ("data", "record", "ocds", "payload"):
            nested = record.get(key)
            if isinstance(nested, dict):
                compiled_release = nested.get("compiledRelease") or nested.get("compiled_release")
                if compiled_release:
                    return compiled_release

        return None

    def _normalize_compiled_release(self, compiled_release: Dict) -> Optional[Dict]:
        """
        Normalize OCDS compiledRelease/release into the internal tender format.
        """
        tender_obj = compiled_release.get("tender", {}) or {}
        parties = compiled_release.get("parties", []) or []
        awards = compiled_release.get("awards", []) or []

        tender_id = (
            compiled_release.get("id")
            or compiled_release.get("ocid")
            or compiled_release.get("release_id")
        )

        title = (
            tender_obj.get("title")
            or tender_obj.get("description")
            or ""
        )

        tender_id_str = str(tender_id) if tender_id else None
        if not tender_id_str:
            return None
        if not isinstance(title, str):
            title = ""
        title = title.strip()

        buyer_party = next(
            (
                p
                for p in parties
                if "buyer" in (p.get("roles") or []) or "buyer" in (p.get("role") or [])
            ),
            (parties[0] if parties else {}) or {},
        )

        identifier = buyer_party.get("identifier") or {}
        issuer_id = identifier.get("id") or identifier.get("value")
        issuer_name = buyer_party.get("name") or buyer_party.get("legalName") or issuer_id

        address = buyer_party.get("address") or {}
        contact_point = buyer_party.get("contactPoint") or {}
        additional_identifiers = buyer_party.get("additionalIdentifiers") or []

        nuts_code = None
        for ai in additional_identifiers:
            if not isinstance(ai, dict):
                continue
            scheme = (ai.get("scheme") or ai.get("type") or "").lower()
            if "nuts" in scheme or "nuc" in scheme:
                nuts_code = ai.get("id") or ai.get("value")
                break

        if not nuts_code:
            # Heuristic: NUTS codes look like ITC4C / ITI43 etc.
            for ai in additional_identifiers:
                if not isinstance(ai, dict):
                    continue
                cand = ai.get("id") or ai.get("value")
                if isinstance(cand, str) and cand.startswith("IT") and 4 <= len(cand) <= 6:
                    nuts_code = cand
                    break

        issuer = {
            "issuer_id": issuer_id or None,
            "name": issuer_name or None,
            "contact_email": contact_point.get("email") or buyer_party.get("email"),
            "contact_phone": contact_point.get("telephone") or buyer_party.get("phone"),
            "address": address.get("streetAddress") or address.get("addressLine"),
            "city": address.get("locality") or address.get("city"),
            "region": address.get("region") or address.get("state"),
            "nuts_code": nuts_code,
        }

        tender_period = tender_obj.get("tenderPeriod") or {}
        publication_date = tender_period.get("startDate") or tender_obj.get("publicationDate")
        submission_deadline = tender_period.get("endDate") or tender_obj.get("submissionDeadline")

        def _normalize_iso_datetime(value: Optional[str]) -> Optional[str]:
            if not value or not isinstance(value, str):
                return None
            value = value.replace("Z", "+00:00") if value.endswith("Z") else value
            try:
                return datetime.fromisoformat(value).isoformat()
            except ValueError:
                return value

        def _normalize_iso_date(value: Optional[str]) -> Optional[str]:
            if not value or not isinstance(value, str):
                return None
            return value[:10] if len(value) >= 10 else value

        publication_date_norm = _normalize_iso_date(publication_date)
        submission_deadline_norm = _normalize_iso_datetime(submission_deadline)

        value_obj = tender_obj.get("value") or {}
        estimated_value = value_obj.get("amount")

        main_category = (tender_obj.get("mainProcurementCategory") or "").lower()
        contract_type = (
            "works"
            if "work" in main_category
            else "supplies"
            if "supply" in main_category
            else "services"
            if "service" in main_category
            else tender_obj.get("contractType")
        )

        cpv_codes: List[str] = []
        for item in (tender_obj.get("items") or []) or []:
            if not isinstance(item, dict):
                continue
            classification = item.get("classification") or {}
            scheme = (classification.get("scheme") or "").lower()
            if "cpv" in scheme or classification.get("id"):
                cpv_id = classification.get("id")
                if isinstance(cpv_id, str) and cpv_id:
                    cpv_codes.append(cpv_id)

        nuts_codes: List[str] = []
        if isinstance(tender_obj.get("nutsCodes"), list):
            nuts_codes = [n for n in tender_obj["nutsCodes"] if isinstance(n, str)]

        has_lots = False
        lots_data: Optional[Dict] = None
        lots = tender_obj.get("lots") or []
        if isinstance(lots, list) and lots:
            has_lots = True
            lots_payload = []
            for lot in lots:
                if not isinstance(lot, dict):
                    continue
                lot_value = (lot.get("value") or {}).get("amount")
                lots_payload.append(
                    {
                        "lot_id": lot.get("id"),
                        "description": lot.get("title") or lot.get("description"),
                        "value": lot_value,
                    }
                )
            lots_data = {"lots": lots_payload, "max_lots_per_bidder": None}

        awardees_by_tax_id: Dict[str, float] = {}
        for award in awards:
            if not isinstance(award, dict):
                continue
            award_value_obj = award.get("value") or {}
            award_amount = award_value_obj.get("amount")
            for supplier in award.get("suppliers") or []:
                if not isinstance(supplier, dict):
                    continue
                ident = supplier.get("identifier") or {}
                tax_id = ident.get("id") or ident.get("value")
                if tax_id and award_amount is not None:
                    try:
                        awardees_by_tax_id[str(tax_id)] = float(award_amount)
                    except Exception:
                        pass

        participants: List[Dict] = []
        for p in parties:
            if not isinstance(p, dict):
                continue
            roles = set(p.get("roles") or [])
            if not (roles & {"supplier", "bidder", "awardee"}):
                continue
            ident = p.get("identifier") or {}
            tax_id = ident.get("id") or ident.get("value")
            name = p.get("name") or p.get("legalName")
            if not tax_id or not name:
                continue
            participants.append(
                {
                    "tax_id": str(tax_id),
                    "name": name,
                    "role": "bidder",
                    "awarded": str(tax_id) in awardees_by_tax_id,
                    "award_value": awardees_by_tax_id.get(str(tax_id)),
                }
            )

        documents = tender_obj.get("documents") or []
        document_portal_url = None
        document_portal_url_source = "none"
        if isinstance(documents, list) and documents:
            for d in documents:
                if not isinstance(d, dict):
                    continue
                # Priority (real-world): tender.documents[].url
                url = d.get("url")
                if isinstance(url, str) and url.strip():
                    document_portal_url = url.strip()
                    document_portal_url_source = "documents"
                    break

        # Portal identifiers from OCDS.
        tender_url = (
            compiled_release.get("uri")
            or compiled_release.get("url")
            or tender_obj.get("uri")
            or tender_obj.get("url")
        )
        if not tender_url:
            # Some payloads keep release metadata in a nested `release` object.
            release_obj = compiled_release.get("release") or compiled_release.get("compiledRelease") or {}
            if isinstance(release_obj, dict):
                tender_url = release_obj.get("uri") or release_obj.get("url")

        source_platform = self.detect_source_platform(
            str(tender_id) if tender_id else None,
            tender_url if isinstance(tender_url, str) else None,
            title,
        )

        # Fallbacks (source-aware).
        if not document_portal_url and isinstance(tender_url, str) and tender_url.strip():
            # Priority (messy OCDS): compiledRelease.uri/url
            document_portal_url = tender_url.strip()
            document_portal_url_source = "compiled_release"

        if not document_portal_url:
            # Final priority: platform base domain (ANAC UI / MePA)
            base = (
                self.MEPA_BASE_URL
                if source_platform == "MEPA"
                else self.ANAC_UI_BASE_URL
            )
            document_portal_url = base
            document_portal_url_source = "fallback_platform_base"

        # Canonicalize for storage + "LIKE %domain%" queries.
        normalized_portal_url = self.normalize_portal_url(document_portal_url)
        if normalized_portal_url:
            document_portal_url = normalized_portal_url
        else:
            document_portal_url = self.ANAC_UI_BASE_URL
            document_portal_url_source = "fallback_platform_base"
            source_platform = "ANAC"

        return {
            "tender_id": str(tender_id) if tender_id else "",
            "title": title,
            "issuer": issuer,
            "estimated_value": estimated_value,
            # Avoid passing complex non-JSON-serializable objects (e.g. Decimal) into JSONB.
            # The MVP doesn't depend on award criteria for core functionality.
            "award_criteria": None,
            "publication_date": publication_date_norm,
            "submission_deadline": submission_deadline_norm,
            "execution_location": None,
            "nuts_codes": nuts_codes,
            "cpv_codes": cpv_codes,
            "contract_type": contract_type,
            "eu_funded": tender_obj.get("hasEUFunding"),
            "renewable": tender_obj.get("renewable"),
            "has_lots": has_lots,
            # lots_data is stored in JSONB, so make it JSON-serializable.
            "lots_data": self._sanitize_json(lots_data),
            "tender_url": tender_url,
            "document_portal_url": document_portal_url,
            "document_portal_url_source": document_portal_url_source,
            "source_platform": source_platform,
            "participants": participants,
        }

    @staticmethod
    def _sanitize_json(value):
        """
        Convert non-JSON types (e.g. Decimal) into JSON-serializable equivalents.
        Used for JSONB columns (`award_criteria`, `lots_data`).
        """
        if value is None:
            return None

        # Lazy import to avoid unused dependency warnings.
        from decimal import Decimal

        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {str(k): ANACClient._sanitize_json(v) for k, v in value.items()}
        if isinstance(value, list):
            return [ANACClient._sanitize_json(v) for v in value]
        # Keep primitives as-is (int/float/str/bool)
        return value
