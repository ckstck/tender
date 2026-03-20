import click
import logging
from datetime import datetime
import csv
from pathlib import Path
import subprocess
import sys
from src.config import Config, ENV_PATH, describe_database_url
from src.ingestion.pipeline import IngestionPipeline
from src.organizations.extractor import OrganizationExtractor
from src.search.hybrid import HybridSearch
from src.documents.analyzer import DocumentPortalAnalyzer
from src.documents.downloader import DocumentDownloader
from src.database.connection import get_db
from src.database.models import Organization, SearchQuery, Tender, Issuer, TenderDocument
from src.ingestion.enrichment import TenderEnrichment

from sqlalchemy.orm import joinedload
from src.ingestion.demo_loader import load_demo_data

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info(
    "CLI startup: ENV_PATH=%s DATABASE_URL=%s (%s)",
    ENV_PATH,
    Config.DATABASE_URL,
    describe_database_url(Config.DATABASE_URL),
)


def _validate_iso_date(value: str, field_name: str) -> str:
    try:
        datetime.fromisoformat(value)
    except Exception as exc:
        raise click.BadParameter(f"{field_name} must be YYYY-MM-DD (got {value!r})") from exc
    return value


def _validate_date_range(start_date: str, end_date: str) -> None:
    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date)
    if start_dt > end_dt:
        raise click.BadParameter(
            f"Invalid range: start-date {start_date} is after end-date {end_date}"
        )

@click.group()
def cli():
    """Italian Tender Intelligence System CLI"""
    pass


@cli.command("init-db")
def init_db() -> None:
    """Initialize the database schema (creates tables + pgvector)."""
    repo_root = Path(__file__).resolve().parents[2]  # tender/
    init_script = repo_root / "scripts" / "init_db.py"
    click.echo(f"Initializing database using {init_script} ...")
    # Use the same interpreter used to run the CLI.
    subprocess.check_call([sys.executable, str(init_script)])


@cli.command()
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
@click.option(
    '--demo-data',
    is_flag=True,
    default=False,
    help='Load deterministic demo dataset from db_dumps/ (offline-friendly).',
)
def ingest(start_date, end_date, demo_data: bool):
    """Run tender ingestion pipeline"""
    start_date = _validate_iso_date(start_date, "start-date")
    end_date = _validate_iso_date(end_date, "end-date")
    _validate_date_range(start_date, end_date)

    if demo_data:
        click.echo("Loading deterministic demo dataset into PostgreSQL...")
        counts = load_demo_data()
        click.echo(
            "✓ Demo load completed: "
            f"issuers={counts['issuers_loaded']} tenders={counts['tenders_loaded']} "
            f"organizations={counts['organizations_loaded']} "
            f"participants={counts['tender_participants_loaded']}"
        )
        return

    click.echo(f"Starting ingestion from {start_date} to {end_date}...")
    pipeline = IngestionPipeline()
    result = pipeline.run(start_date=start_date, end_date=end_date)
    click.echo(f"\n✓ Ingestion completed:")
    click.echo(f"  - Range: {start_date} → {end_date}")
    click.echo(f"  - Fetched: {result['fetched']}")
    click.echo(f"  - Ingested: {result['ingested']}")
    click.echo(f"  - Skipped: {result['skipped']}")
    click.echo(f"  - Errors: {result['errors']}")

@cli.command()
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
def extract_orgs(start_date, end_date):
    """Extract organizations from tenders"""
    start_date = _validate_iso_date(start_date, "start-date")
    end_date = _validate_iso_date(end_date, "end-date")
    _validate_date_range(start_date, end_date)

    click.echo("Extracting organizations from tender participants...")
    extractor = OrganizationExtractor()
    result = extractor.extract_from_tenders(start_date=start_date, end_date=end_date)
    click.echo(f"\n✓ Organization extraction completed:")
    click.echo(f"  - New organizations: {result['new_organizations']}")
    click.echo(f"  - New participations: {result['new_participations']}")
    click.echo(f"  - Skipped (individual CF): {result.get('skipped_individual_cf', 0)}")
    click.echo(f"  - Skipped (invalid): {result.get('skipped_invalid', 0)}")

    # Verify committed state in the database immediately after extraction.
    with get_db() as db:
        org_count = db.query(Organization).count()
    logger.info("extract-orgs final organizations=%s (DATABASE_URL=%s)", org_count, Config.DATABASE_URL)
    click.echo(f"\n✓ Database organizations count: {org_count}")

@cli.command()
@click.option('--query', required=True, help='Search query text')
@click.option('--min-value', type=float, help='Minimum tender value')
@click.option('--max-value', type=float, help='Maximum tender value')
@click.option('--cpv', help='CPV code filter')
@click.option('--nuts', help='NUTS code filter')
@click.option('--contract-type', help='Contract type (services, supplies, works)')
@click.option('--eu-funded', type=bool, help='EU funded filter')
@click.option('--limit', default=10, help='Number of results')
def search(query, min_value, max_value, cpv, nuts, contract_type, eu_funded, limit):
    """Search for tenders using hybrid search"""
    filters = {}
    if min_value:
        filters['min_value'] = min_value
    if max_value:
        filters['max_value'] = max_value
    if cpv:
        filters['cpv_codes'] = [cpv]
    if nuts:
        filters['nuts_codes'] = [nuts]
    if contract_type:
        filters['contract_type'] = contract_type
    if eu_funded is not None:
        filters['eu_funded'] = eu_funded
    
    click.echo(f"\n🔍 Searching for: '{query}'")
    if filters:
        click.echo(f"Filters: {filters}")
    click.echo()
    
    # Persist every search attempt (even if results are empty) for demo observability.
    with get_db() as db:
        row = SearchQuery(
            query_text=query,
            filters=filters if filters else {},
            results=None,
        )
        db.add(row)
        db.flush()  # populate `id` before commit for logging
        inserted_id = row.id
        db.commit()
    logger.info("Persisted search query id=%s query=%r filters=%s", inserted_id, query, filters)
    click.echo(f"✓ Saved search query id={inserted_id}")

    searcher = HybridSearch()
    results = searcher.search(query, filters=filters, limit=limit)
    
    if not results:
        click.echo("No results found.")
        return
    
    click.echo(f"Found {len(results)} results:\n")
    for i, result in enumerate(results, 1):
        click.echo(f"{i}. {result['title']}")
        click.echo(f"   ID: {result['tender_id']}")
        if result['estimated_value']:
            click.echo(f"   Value: €{result['estimated_value']:,.2f}")
        if result['submission_deadline']:
            click.echo(f"   Deadline: {result['submission_deadline']}")
        click.echo(f"   Type: {result['contract_type']}")
        click.echo(f"   Similarity: {result['similarity_score']:.3f}")
        click.echo(f"   URL: {result['tender_url']}\n")


@cli.command()
@click.option(
    "--limit",
    default=0,
    type=int,
    help="Max tenders to regenerate (0 = all).",
)
@click.option(
    "--commit-every",
    default=25,
    type=int,
    help="Commit every N updates to reduce transaction risk.",
)
@click.option(
    "--only-missing",
    is_flag=True,
    default=False,
    help="Regenerate only tenders with missing summary/searchable_text/embedding.",
)
def regenerate_ai(limit: int, commit_every: int, only_missing: bool) -> None:
    """
    Regenerate LLM-derived tender summary, searchable_text, and embeddings
    using the currently configured OPENAI_MODEL.

    This is needed when you change Config.OPENAI_MODEL because ingestion
    upserts intentionally only backfills portal URLs on conflict.
    """
    enrichment = TenderEnrichment()

    click.echo(
        f"Regenerating LLM fields for existing tenders (model={Config.OPENAI_MODEL})..."
    )

    total_seen = 0
    updated_count = 0
    skipped_count = 0
    failures = 0

    with get_db() as db:
        q = db.query(Tender).options(joinedload(Tender.issuer))

        if only_missing:
            # Only update tenders that are missing any of the AI fields.
            q = q.filter(
                (Tender.summary.is_(None))
                | (Tender.searchable_text.is_(None))
                | (Tender.embedding.is_(None))
            )

        if limit and limit > 0:
            q = q.limit(limit)

        tenders = q.all()
        total_seen = len(tenders)

        click.echo(f"Found {total_seen} tenders to process.")

        last_commit_processed = 0
        for tender in tenders:
            try:
                issuer_name = tender.issuer.name if tender.issuer else "N/A"

                tender_data = {
                    "title": tender.title,
                    "contract_type": tender.contract_type,
                    "estimated_value": tender.estimated_value,
                    "execution_location": tender.execution_location,
                    "cpv_codes": tender.cpv_codes or [],
                    "nuts_codes": tender.nuts_codes or [],
                    "eu_funded": tender.eu_funded,
                    "renewable": tender.renewable,
                    "has_lots": tender.has_lots,
                    "lots_data": tender.lots_data,
                    "issuer": {"name": issuer_name},
                }

                summary = enrichment.generate_summary(tender_data)
                searchable_text = enrichment.generate_searchable_text(tender_data)
                embedding = enrichment.generate_embedding(searchable_text)

                tender.summary = summary
                tender.searchable_text = searchable_text
                tender.embedding = embedding

                updated_count += 1

                last_commit_processed += 1
                if last_commit_processed >= commit_every:
                    db.commit()
                    last_commit_processed = 0
                    logger.info(
                        "Regenerate AI progress: updated=%s/%s",
                        updated_count,
                        total_seen,
                    )
            except Exception as e:
                failures += 1
                logger.exception(
                    "Failed to regenerate AI for tender_id=%s: %s",
                    tender.tender_id,
                    e,
                )

        # Final commit for remaining updates.
        db.commit()

    click.echo(
        f"\n✓ Regeneration completed: total={total_seen} updated={updated_count} skipped={skipped_count} failures={failures}"
    )

@cli.command()
@click.option('--org-id', required=True, type=int, help='Organization ID')
def demo_search(org_id):
    """Run 5 demo searches for an organization"""
    
    demo_queries = [
        {
            "query": "road maintenance and infrastructure services",
            "filters": {"contract_type": "services"}
        },
        {
            "query": "IT equipment computers and technology supplies",
            "filters": {"min_value": 100000}
        },
        {
            "query": "building renovation construction works",
            "filters": {"contract_type": "works"}
        },
        {
            "query": "digital transformation consulting services",
            "filters": {"eu_funded": True}
        },
        {
            "query": "renewable energy solar panels installation",
            "filters": {}
        }
    ]
    
    searcher = HybridSearch()
    
    with get_db() as db:
        # SQLAlchemy 2.x: Session.get() avoids Query.get() deprecation warning.
        org = db.get(Organization, org_id)
        if not org:
            click.echo(f"❌ Organization {org_id} not found")
            return
        
        click.echo(f"\n🏢 Running demo searches for: {org.name} ({org.tax_id})\n")
        
        for i, demo in enumerate(demo_queries, 1):
            click.echo(f"\n{'='*70}")
            click.echo(f"Query {i}: {demo['query']}")
            if demo['filters']:
                click.echo(f"Filters: {demo['filters']}")
            click.echo(f"{'='*70}\n")
            
            results = searcher.search(demo['query'], filters=demo['filters'], limit=5)
            
            search_query = SearchQuery(
                organization_id=org_id,
                query_text=demo['query'],
                filters=demo['filters'],
                results=[r['tender_id'] for r in results]
            )
            db.add(search_query)
            
            if results:
                for j, result in enumerate(results, 1):
                    click.echo(f"{j}. {result['title']}")
                    if result['estimated_value']:
                        click.echo(f"   Value: €{result['estimated_value']:,.2f}")
                    click.echo(f"   Score: {result['similarity_score']:.3f}\n")
            else:
                click.echo("   No results found.\n")
        
        click.echo("\n✓ Demo searches completed and saved")

@cli.command()
@click.option('--output', default='portal_analysis.csv', help='Output CSV file')
def analyze_portals(output):
    """Analyze document portal distribution"""
    click.echo("Analyzing document portals...")
    analyzer = DocumentPortalAnalyzer()
    results = analyzer.analyze(output)
    
    if results:
        click.echo(f"\n📊 Document Portal Analysis\n")
        for portal, count in list(results.items())[:10]:
            click.echo(f"  {portal}: {count} tenders")
        click.echo(f"\n✓ Full analysis saved to {output}")
    else:
        click.echo("\nNo document portals found in database.")

@cli.command()
@click.option('--portal', required=True, help='Portal domain to download from')
@click.option('--limit', default=10, help='Number of tenders to process')
def download_docs(portal, limit):
    """Download documents from a portal"""
    click.echo(f"Processing documents from {portal}...")
    downloader = DocumentDownloader()
    result = downloader.download_for_portal(portal, limit=limit)
    click.echo(
        f"\n✓ Done: total={result.get('total_processed')} uploaded={result.get('successful_uploads')} "
        f"skipped={result.get('skipped_already_processed')} failures={result.get('failures')} "
        f"from {portal}"
    )


@cli.command()
@click.option('--portal-domain', required=False, help='Portal domain to download from')
@click.option('--portal-analysis-file', default='portal_analysis.csv', help='CSV produced by analyze-portals')
@click.option('--limit', default=10, help='Number of tenders to process')
def download_documents(portal_domain, portal_analysis_file, limit):
    """
    Download and store tender documents.

    If --portal-domain is not provided, picks the top domain from portal_analysis.csv.
    """
    base_dir = Path(__file__).resolve().parents[2]  # tender/
    analysis_path = Path(portal_analysis_file)
    if not analysis_path.is_absolute():
        analysis_path = base_dir / analysis_path

    selected_portal = portal_domain
    if not selected_portal:
        if not analysis_path.exists():
            raise click.ClickException(f"Portal analysis file not found: {analysis_path}")

        with analysis_path.open('r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            first = next(reader, None)
            if not first or len(first) < 1:
                click.echo(f"No portal rows found in {analysis_path}; skipping download.")
                return
            selected_portal = first[0].strip()

    click.echo(f"Processing documents from {selected_portal}...")
    downloader = DocumentDownloader()
    result = downloader.download_for_portal(selected_portal, limit=limit)
    click.echo(
        f"\n✓ Done: total={result.get('total_processed')} uploaded={result.get('successful_uploads')} "
        f"skipped={result.get('skipped_already_processed')} failures={result.get('failures')} "
        f"from {selected_portal}"
    )

@cli.command()
def status():
    """Show system status"""
    with get_db() as db:
        tender_count = db.query(Tender).count()
        org_count = db.query(Organization).count()
        issuer_count = db.query(Issuer).count()
        doc_count = db.query(TenderDocument).count()
        search_count = db.query(SearchQuery).count()
        
        click.echo("\n📈 System Status\n")
        click.echo(f"  Tenders: {tender_count}")
        click.echo(f"  Organizations: {org_count}")
        click.echo(f"  Issuers: {issuer_count}")
        click.echo(f"  Documents: {doc_count}")
        click.echo(f"  Search Queries: {search_count}\n")

@cli.command()
def list_orgs():
    """List all organizations"""
    with get_db() as db:
        orgs = db.query(Organization).all()
        
        if not orgs:
            click.echo("No organizations found.")
            return
        
        click.echo(f"\n📋 Organizations ({len(orgs)} total)\n")
        for org in orgs:
            click.echo(f"  ID: {org.id} | {org.name} ({org.tax_id})")
            if org.region:
                click.echo(f"      Region: {org.region}")

if __name__ == '__main__':
    cli()
