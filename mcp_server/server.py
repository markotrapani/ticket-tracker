"""MCP server for Zendesk ticket scraping and enrichment.

Provides tools for Claude to automate the process of:
1. Authenticating with Zendesk via browser session
2. Scraping ticket pages as PDFs (batch of 20, delete after enrichment)
3. Enriching ticket data through the existing PDF parser pipeline

Usage:
    python -m mcp_server.server
"""

import logging
import os
import sys

# Ensure project root is on sys.path for backend imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mcp.server.fastmcp import FastMCP

from backend.app import create_app
from backend.config import DATA_DIR
from backend.models.database import db
from backend.models.ticket import Ticket
from backend.services.enrichment import enrich_ticket_from_parsed
from backend.services.pdf_parser import parse_zendesk_pdf

from mcp_server.browser import BrowserManager
from mcp_server.scraper import (
    scrape_ticket_pdf,
    scrape_with_rate_limit,
    AuthenticationError,
    ScraperError,
)

# ── Logging (MUST go to stderr - stdout is MCP JSON-RPC) ────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    stream=sys.stderr,
)
logger = logging.getLogger('zendesk-mcp')

# ── Paths ────────────────────────────────────────────────────────

SCRAPED_PDFS_DIR = os.path.join(DATA_DIR, 'scraped_pdfs')
BROWSER_DATA_DIR = os.path.join(DATA_DIR, 'browser_data')
os.makedirs(SCRAPED_PDFS_DIR, exist_ok=True)
os.makedirs(BROWSER_DATA_DIR, exist_ok=True)

# ── Singletons ───────────────────────────────────────────────────

flask_app = create_app()
browser = BrowserManager(user_data_dir=BROWSER_DATA_DIR, headless=True)
mcp = FastMCP("zendesk-scraper")


# ═══════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def zendesk_login() -> str:
    """Open a visible browser window for the user to log in to Zendesk.

    This launches a Chromium browser pointed at the Zendesk agent dashboard.
    The user must complete authentication manually (supports SSO, MFA, etc.).
    The session persists across server restarts.

    After login is complete, call check_auth to verify and switch to headless mode.
    """
    try:
        await browser.restart_headed()
        page = await browser.get_page()
        await page.goto(
            'https://redislabs.zendesk.com/agent/dashboard',
            wait_until='domcontentloaded',
            timeout=30000,
        )
        return (
            "Browser opened at Zendesk login page. "
            "Please complete authentication (including any SSO/MFA steps) "
            "in the browser window.\n\n"
            "Once logged in and you see the agent dashboard, "
            "call check_auth to verify the session."
        )
    except Exception as e:
        return f"Failed to open browser: {e}"


@mcp.tool()
async def check_auth() -> str:
    """Check if the browser session is authenticated with Zendesk.

    If authenticated, automatically switches to headless mode for
    efficient scraping. Call this after zendesk_login.
    """
    try:
        is_auth = await browser.is_authenticated()

        if not is_auth:
            return (
                "Not authenticated. Please call zendesk_login first "
                "and complete the login in the browser window."
            )

        # Switch to headless for scraping efficiency
        await browser.restart_headless()

        # Verify auth persisted after headless restart
        still_auth = await browser.is_authenticated()
        if still_auth:
            return (
                "Authenticated successfully! Browser switched to headless mode. "
                "Ready to scrape tickets.\n\n"
                "Next steps:\n"
                "- Call list_unenriched() to see tickets needing enrichment\n"
                "- Call scrape_and_enrich(ticket_id) for a single ticket\n"
                "- Call bulk_scrape_and_enrich() to process a batch of 20"
            )
        else:
            return (
                "Authentication was detected but did not persist after "
                "switching to headless mode. Please call zendesk_login again."
            )
    except Exception as e:
        return f"Auth check error: {e}"


@mcp.tool()
async def list_unenriched(limit: int = 50) -> str:
    """List tickets that still need enrichment (metadata_only), ordered by score.

    These are tickets imported from CSV that haven't been enriched with
    conversation data from their Zendesk PDF yet.

    Args:
        limit: Maximum number of tickets to show (default 50, max 500)
    """
    limit = min(max(limit, 1), 500)

    with flask_app.app_context():
        tickets = (
            Ticket.query
            .filter_by(enrichment_level='metadata_only')
            .order_by(Ticket.auto_score.desc().nullslast())
            .limit(limit)
            .all()
        )

        total = Ticket.query.filter_by(enrichment_level='metadata_only').count()
        total_all = Ticket.query.count()
        enriched = total_all - total

        if not tickets:
            return (
                f"All {total_all} tickets are enriched! "
                "No unenriched tickets remaining."
            )

        lines = [
            f"Unenriched: {total} of {total_all} total "
            f"({enriched} already enriched)\n",
            f"{'ID':<8} {'Score':>5}  {'Status':<8} {'Created':<12} {'Days':>5}",
            '-' * 50,
        ]
        for t in tickets:
            days = str(t.resolution_days) if t.resolution_days is not None else "open"
            score = f"{t.auto_score:.0f}" if t.auto_score else "-"
            lines.append(
                f"{t.zendesk_id:<8} {score:>5}  {t.status:<8} "
                f"{str(t.created_date):<12} {days:>5}"
            )

        if total > limit:
            lines.append(f"\n... and {total - limit} more. Increase limit to see all.")

        return '\n'.join(lines)


@mcp.tool()
async def scrape_and_enrich(ticket_id: str) -> str:
    """Scrape a single ticket from Zendesk and enrich it in the database.

    Navigates to the ticket page, generates a PDF, parses it through
    the PDF parser, enriches the database record, then deletes the PDF.

    Args:
        ticket_id: The Zendesk ticket ID (e.g. "153756")
    """
    # Verify ticket exists
    with flask_app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            return f"Ticket #{ticket_id} not found in database. Import CSV first."

    # Step 1: Scrape PDF
    try:
        pdf_path = await scrape_with_rate_limit(
            browser, str(ticket_id), SCRAPED_PDFS_DIR, delay_seconds=2.0
        )
    except AuthenticationError as e:
        return f"Authentication expired: {e}\nCall zendesk_login to re-authenticate."
    except ScraperError as e:
        return f"Scraping failed for #{ticket_id}: {e}"
    except Exception as e:
        return f"Unexpected error scraping #{ticket_id}: {e}"

    # Step 2: Parse PDF and enrich
    try:
        with flask_app.app_context():
            ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
            parsed = parse_zendesk_pdf(pdf_path)
            enrich_ticket_from_parsed(ticket, parsed)
            db.session.commit()

            result = (
                f"Enriched #{ticket_id}:\n"
                f"  Subject: {ticket.subject or 'N/A'}\n"
                f"  Customer: {ticket.customer_name or 'N/A'}\n"
                f"  Enrichment: {ticket.enrichment_level}\n"
                f"  Score: {ticket.final_score}\n"
                f"  Comments: {len(parsed.get('comments', []))}"
            )
    except Exception as e:
        return f"PDF parsing/enrichment failed for #{ticket_id}: {e}"

    # Step 3: Delete PDF to save disk space
    try:
        os.remove(pdf_path)
        logger.info(f"Deleted PDF: {pdf_path}")
    except OSError as e:
        logger.warning(f"Could not delete PDF {pdf_path}: {e}")

    return result


@mcp.tool()
async def bulk_scrape_and_enrich(
    batch_size: int = 20,
    delay_seconds: float = 3.0,
) -> str:
    """Batch scrape and enrich unenriched tickets.

    Processes tickets in order of highest auto-score first.
    Each ticket's PDF is deleted immediately after enrichment to save disk space.
    Stops on authentication failure so the user can re-login.

    For 898 tickets at batch_size=20, call this ~45 times.

    Args:
        batch_size: Number of tickets to process in this batch (default 20, max 50)
        delay_seconds: Seconds to wait between tickets to avoid throttling (default 3.0)
    """
    batch_size = min(max(batch_size, 1), 50)

    # Get batch of unenriched tickets
    with flask_app.app_context():
        tickets = (
            Ticket.query
            .filter_by(enrichment_level='metadata_only')
            .order_by(Ticket.auto_score.desc().nullslast())
            .limit(batch_size)
            .all()
        )

        if not tickets:
            total = Ticket.query.count()
            return f"All {total} tickets are already enriched! Nothing to do."

        ticket_ids = [t.zendesk_id for t in tickets]
        total_unenriched = Ticket.query.filter_by(enrichment_level='metadata_only').count()

    results = {
        'success': 0,
        'failed': 0,
        'auth_error': False,
        'errors': [],
        'enriched_ids': [],
    }

    for i, tid in enumerate(ticket_ids):
        logger.info(f"[{i+1}/{len(ticket_ids)}] Processing #{tid}")

        # Scrape PDF
        try:
            pdf_path = await scrape_with_rate_limit(
                browser, tid, SCRAPED_PDFS_DIR, delay_seconds=delay_seconds
            )
        except AuthenticationError:
            results['auth_error'] = True
            results['errors'].append(f"#{tid}: Session expired")
            logger.error(f"Auth expired at ticket #{tid}")
            break
        except (ScraperError, Exception) as e:
            results['failed'] += 1
            results['errors'].append(f"#{tid}: Scrape failed - {e}")
            logger.error(f"Scrape failed for #{tid}: {e}")
            continue

        # Parse and enrich
        try:
            with flask_app.app_context():
                ticket = Ticket.query.filter_by(zendesk_id=tid).first()
                if ticket:
                    parsed = parse_zendesk_pdf(pdf_path)
                    enrich_ticket_from_parsed(ticket, parsed)
                    db.session.commit()
                    results['success'] += 1
                    results['enriched_ids'].append(tid)
                    logger.info(
                        f"  Enriched #{tid}: "
                        f"level={ticket.enrichment_level}, "
                        f"score={ticket.final_score}"
                    )
        except Exception as e:
            results['failed'] += 1
            results['errors'].append(f"#{tid}: Enrichment failed - {e}")
            logger.error(f"Enrichment failed for #{tid}: {e}")

        # Delete PDF immediately after enrichment
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except OSError:
            pass

    # Build summary
    with flask_app.app_context():
        remaining = Ticket.query.filter_by(enrichment_level='metadata_only').count()

    lines = [
        f"Batch complete: {results['success']} enriched, "
        f"{results['failed']} failed "
        f"(out of {len(ticket_ids)} attempted)",
        f"Remaining unenriched: {remaining}",
    ]

    if results['auth_error']:
        lines.append(
            "\nSTOPPED: Authentication expired. "
            "Call zendesk_login to re-authenticate, then continue."
        )

    if results['errors']:
        lines.append("\nErrors:")
        for err in results['errors']:
            lines.append(f"  - {err}")

    if results['enriched_ids']:
        lines.append(f"\nEnriched tickets: {', '.join(results['enriched_ids'])}")

    return '\n'.join(lines)


@mcp.tool()
async def get_scraping_status() -> str:
    """Get the current enrichment status of all tickets.

    Shows counts of enriched vs unenriched tickets, enrichment levels,
    and any PDFs currently on disk.
    """
    with flask_app.app_context():
        total = Ticket.query.count()
        metadata_only = Ticket.query.filter_by(enrichment_level='metadata_only').count()
        partial = Ticket.query.filter_by(enrichment_level='partial').count()
        full = Ticket.query.filter_by(enrichment_level='full').count()

    # Count PDFs on disk
    pdf_count = 0
    pdf_size_mb = 0.0
    if os.path.exists(SCRAPED_PDFS_DIR):
        for f in os.listdir(SCRAPED_PDFS_DIR):
            if f.endswith('.pdf'):
                pdf_count += 1
                pdf_size_mb += os.path.getsize(
                    os.path.join(SCRAPED_PDFS_DIR, f)
                ) / (1024 * 1024)

    lines = [
        "Ticket Enrichment Status",
        "=" * 30,
        f"Total tickets:      {total}",
        f"  Metadata only:    {metadata_only}",
        f"  Partially enriched: {partial}",
        f"  Fully enriched:   {full}",
        "",
        f"Enrichment progress: {total - metadata_only}/{total} "
        f"({((total - metadata_only) / total * 100) if total else 0:.1f}%)",
        "",
        f"PDFs on disk: {pdf_count} ({pdf_size_mb:.1f} MB)",
        f"Browser running: {browser.is_running}",
        f"Browser headless: {browser.is_headless}",
    ]

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════


def main():
    """Run the MCP server via stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
