import click
import logging
from src.ingestion.pipeline import IngestionPipeline
from src.organizations.extractor import OrganizationExtractor
from src.search.hybrid import HybridSearch
from src.documents.analyzer import DocumentPortalAnalyzer
from src.documents.downloader import DocumentDownloader
from src.database.connection import get_db
from src.database.models import Organization, SearchQuery, Tender, Issuer, Document

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@click.group()
def cli():
    """Italian Tender Intelligence System CLI"""
    pass

@cli.command()
@click.option('--days', default=30, help='Number of days to look back')
def ingest(days):
    """Run tender ingestion pipeline"""
    click.echo(f"Starting ingestion for last {days} days...")
    pipeline = IngestionPipeline()
    result = pipeline.run(days_back=days)
    click.echo(f"\n✓ Ingestion completed:")
    click.echo(f"  - Ingested: {result['ingested']}")
    click.echo(f"  - Skipped: {result['skipped']}")
    click.echo(f"  - Errors: {result['errors']}")

@cli.command()
@click.option('--days', default=30, help='Number of days to look back')
def extract_orgs(days):
    """Extract organizations from tenders"""
    click.echo("Extracting organizations from tender participants...")
    extractor = OrganizationExtractor()
    result = extractor.extract_from_tenders(days_back=days)
    click.echo(f"\n✓ Organization extraction completed:")
    click.echo(f"  - New organizations: {result['new_organizations']}")
    click.echo(f"  - New participations: {result['new_participations']}")

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
        org = db.query(Organization).get(org_id)
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
@click.option('--portal', help='Portal domain to download from')
@click.option('--auto-detect', is_flag=True, help='Auto-detect top portal')
@click.option('--limit', default=10, help='Number of tenders to process')
def download_docs(portal, auto_detect, limit):
    """Download documents from a portal with real implementation"""
    if not portal and not auto_detect:
        click.echo("Error: Either --portal or --auto-detect must be specified")
        return
    
    if auto_detect:
        click.echo("Auto-detecting top portal...")
    else:
        click.echo(f"Processing documents from {portal}...")
    
    downloader = DocumentDownloader()
    count = downloader.download_for_portal(portal, limit=limit, auto_detect=auto_detect)
    
    if auto_detect and count > 0:
        click.echo(f"\n✓ Processed {count} tenders")
    elif count > 0:
        click.echo(f"\n✓ Processed {count} tenders from {portal}")
    else:
        click.echo("\n⚠ No documents processed")

@cli.command()
def status():
    """Show system status"""
    with get_db() as db:
        tender_count = db.query(Tender).count()
        org_count = db.query(Organization).count()
        issuer_count = db.query(Issuer).count()
        doc_count = db.query(Document).count()
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
