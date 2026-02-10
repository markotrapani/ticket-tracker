"""MCP server for Zendesk ticket scraping and enrichment.

Decoupled two-phase architecture:
  Phase 1 (Scrape): Download ticket PDFs from Zendesk via browser automation.
    - scrape_ticket / bulk_scrape: download PDFs only, no parsing or DB writes
  Phase 2 (Enrich): Parse PDFs and generate AI STAR analysis.
    - enrich_from_pdf: parse PDF, store metadata + conversation notes, return
      formatted conversation for AI analysis (no heuristic STAR generation)
    - save_ticket_analysis: save AI-generated STAR fields (summary, root_cause,
      steps_taken, resolution)

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
from backend.models.ticket import Ticket, TicketNote
from backend.services.enrichment import enrich_ticket_from_parsed
from backend.services.pdf_parser import parse_zendesk_pdf
from backend.services import scoring

from mcp_server.browser import BrowserManager
from mcp_server.scraper import (
    scrape_ticket_pdf,
    scrape_batch_parallel,
    discover_tickets_from_url,
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

PDFS_DIR = os.path.join(DATA_DIR, 'pdfs')
BROWSER_DATA_DIR = os.path.join(DATA_DIR, 'browser_data')
os.makedirs(PDFS_DIR, exist_ok=True)
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
                "- Call scrape_ticket(ticket_id) for a single ticket\n"
                "- Call bulk_scrape() to download PDFs in batches of 20\n"
                "- Call enrich_from_pdf(ticket_id) to parse and prepare for AI analysis"
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
async def navigate_to_ticket(ticket_id: str) -> str:
    """Navigate the browser to a Zendesk ticket page and wait for it to load.

    Use this to verify the ticket loads correctly before scraping.

    Args:
        ticket_id: The Zendesk ticket ID (e.g. "133735")
    """
    page = await browser.get_page()
    ticket_url = f"https://redislabs.zendesk.com/agent/tickets/{ticket_id}"

    try:
        await page.goto(ticket_url, wait_until='domcontentloaded', timeout=30000)
    except Exception as e:
        return f"Navigation failed: {e}"

    current_url = page.url
    if 'login' in current_url.lower() or '/agent/' not in current_url:
        return f"Auth redirect detected. Current URL: {current_url}"

    # Wait for ticket to fully load
    try:
        await page.wait_for_selector(
            '[data-test-id="omni-header-ticket-actions-trigger"]',
            timeout=15000
        )
        return f"Ticket #{ticket_id} loaded. URL: {page.url}\nTicket Actions button is visible."
    except Exception:
        return f"Ticket page loaded but Ticket Actions button not found. URL: {page.url}"


@mcp.tool()
async def click_ticket_actions() -> str:
    """Click the three-dots 'Ticket Actions' button on the current ticket page.

    Call navigate_to_ticket first. This opens the dropdown menu.
    """
    page = await browser.get_page()
    selector = '[data-test-id="omni-header-ticket-actions-trigger"]'

    try:
        btn = page.locator(selector)
        await btn.wait_for(state='visible', timeout=5000)
        await btn.click()
        await page.wait_for_timeout(500)
        return f"Clicked Ticket Actions button. URL: {page.url}"
    except Exception as e:
        return f"Failed to click Ticket Actions: {e}\nURL: {page.url}"


@mcp.tool()
async def click_print_ticket() -> str:
    """Click 'Print ticket' in the dropdown menu.

    Call click_ticket_actions first to open the dropdown.
    """
    page = await browser.get_page()

    selector = '[data-test-id="omni-header-ticket-actions-menu-item-print"]'
    try:
        el = page.locator(selector)
        await el.wait_for(state='visible', timeout=5000)
        await el.click()
        await page.wait_for_timeout(500)
        return f"Clicked 'Print ticket'. URL: {page.url}"
    except Exception as e:
        return f"Could not find 'Print ticket' menu item: {e}\nURL: {page.url}"


@mcp.tool()
async def click_print_confirm_and_save(ticket_id: str) -> str:
    """Click the blue confirm 'Print ticket' button, capture the popup, and save PDF.

    Call click_print_ticket first. This generates the PDF from the print view.

    Args:
        ticket_id: The Zendesk ticket ID (used for the PDF filename)
    """
    page = await browser.get_page()
    output_path = os.path.join(PDFS_DIR, f"#{ticket_id}.pdf")

    try:
        async with page.context.expect_page(timeout=10000) as popup_info:
            confirm_btn = page.locator('[data-test-id="print-ticket-confirm-button"]')
            await confirm_btn.wait_for(state='visible', timeout=5000)
            await confirm_btn.click()

        popup = await popup_info.value
        await popup.evaluate("window.print = () => {}")
        await popup.wait_for_load_state('domcontentloaded', timeout=15000)

        await popup.pdf(
            path=output_path,
            format='Letter',
            print_background=True,
            margin={'top': '0.5in', 'bottom': '0.5in',
                    'left': '0.5in', 'right': '0.5in'},
        )

        size_kb = os.path.getsize(output_path) / 1024
        await popup.close()

        return (
            f"PDF saved: {output_path} ({size_kb:.1f} KB)\n"
            f"Popup URL was: {popup.url}"
        )
    except Exception as e:
        return f"Failed: {e}\nURL: {page.url}"


@mcp.tool()
async def enrich_from_pdf(ticket_id: str) -> str:
    """Parse a saved PDF and enrich the ticket in the database.

    Parses the PDF, stores metadata and conversation notes, and returns
    the full conversation formatted for AI STAR analysis. Does NOT run
    heuristic STAR generation — use save_ticket_analysis to save AI-generated
    summary, root_cause, steps_taken, and resolution.

    Call after scraping (scrape_ticket or bulk_scrape) to process each ticket.

    Args:
        ticket_id: The Zendesk ticket ID
    """
    pdf_path = os.path.join(PDFS_DIR, f"#{ticket_id}.pdf")
    if not os.path.exists(pdf_path):
        return f"No PDF found at {pdf_path}. Scrape the ticket first."

    try:
        with flask_app.app_context():
            ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
            if not ticket:
                return f"Ticket #{ticket_id} not found in database."

            parsed = parse_zendesk_pdf(pdf_path)
            enrich_ticket_from_parsed(ticket, parsed, skip_star=True)
            db.session.commit()

            # Now build the formatted conversation for AI analysis
            notes = (
                TicketNote.query
                .filter_by(ticket_id=ticket.id, note_type='conversation')
                .order_by(TicketNote.created_at.asc())
                .all()
            )

            lines = [
                f"=== TICKET #{ticket.zendesk_id} ===",
                f"Subject: {ticket.subject or 'N/A'}",
                f"Customer: {ticket.customer_name or 'N/A'}",
                f"Product: {ticket.product_line or ticket.product_area or 'N/A'}",
                f"Priority: {ticket.priority or 'N/A'} | Severity: {ticket.severity or 'N/A'}",
                f"Status: {ticket.status} | Created: {ticket.created_date} | Solved: {ticket.solved_date or 'Open'}",
                f"Resolution Days: {ticket.resolution_days if ticket.resolution_days is not None else 'N/A'}",
                f"Production: {'Yes' if ticket.is_production_outage else 'No'}",
                f"Category: {ticket.category or 'N/A'}",
            ]

            if ticket.problem_summary_zd:
                lines.append(f"\nZendesk Problem Summary: {ticket.problem_summary_zd}")
            if ticket.resolution_summary_zd:
                lines.append(f"Zendesk Resolution Summary: {ticket.resolution_summary_zd}")

            if notes:
                lines.append(f"\n=== CONVERSATION ({len(notes)} comments) ===\n")
                lines.append(_format_conversation(notes))
            else:
                lines.append("\n(No conversation data found in PDF)")

            lines.append(
                "\n\n=== INSTRUCTIONS ==="
                "\nAnalyze this ticket and generate concise STAR-format fields for interview prep:"
                "\n1. summary: 2-4 sentence overview (Situation - what happened and why it mattered)"
                "\n2. root_cause: The technical root cause (what was actually wrong)"
                "\n3. steps_taken: Bullet list of what the support engineer did (investigation, debugging, coordination)"
                "\n4. resolution: The fix/workaround delivered and outcome"
                "\n\nThen call save_ticket_analysis with the generated fields."
            )

            return '\n'.join(lines)
    except Exception as e:
        return f"Enrichment failed: {e}"


@mcp.tool()
async def scrape_ticket(ticket_id: str) -> str:
    """Scrape a single ticket PDF from Zendesk (no enrichment).

    Downloads the ticket as a PDF to data/pdfs/. Does NOT parse or enrich.
    Call enrich_from_pdf afterward to parse and prepare for AI analysis.

    Args:
        ticket_id: The Zendesk ticket ID (e.g. "153756")
    """
    # Verify ticket exists
    with flask_app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            return f"Ticket #{ticket_id} not found in database. Import CSV first."

    # Check if PDF already exists
    pdf_path = os.path.join(PDFS_DIR, f"#{ticket_id}.pdf")
    if os.path.exists(pdf_path):
        size_kb = os.path.getsize(pdf_path) / 1024
        return (
            f"PDF already exists for #{ticket_id}: {pdf_path} ({size_kb:.1f} KB)\n"
            f"Call enrich_from_pdf to parse and analyze."
        )

    try:
        pdf_path = await scrape_ticket_pdf(browser, str(ticket_id), PDFS_DIR)
        size_kb = os.path.getsize(pdf_path) / 1024
        return (
            f"Scraped #{ticket_id}: {pdf_path} ({size_kb:.1f} KB)\n"
            f"Call enrich_from_pdf to parse and analyze."
        )
    except AuthenticationError as e:
        return f"Authentication expired: {e}\nCall zendesk_login to re-authenticate."
    except ScraperError as e:
        return f"Scraping failed for #{ticket_id}: {e}"
    except Exception as e:
        return f"Unexpected error scraping #{ticket_id}: {e}"


@mcp.tool()
async def bulk_scrape(
    batch_size: int = 20,
    delay_seconds: float = 3.0,
) -> str:
    """Batch scrape PDFs for unenriched tickets (no enrichment).

    Downloads PDFs only — does NOT parse or enrich. Call enrich_from_pdf
    on each ticket afterward to parse and prepare for AI STAR analysis.

    Processes tickets in order of highest auto-score first.
    Skips tickets that already have PDFs on disk.
    Stops on authentication failure so the user can re-login.

    For ~900 tickets at batch_size=20, call this ~45 times.

    Args:
        batch_size: Number of tickets to process in this batch (default 20, max 50)
        delay_seconds: Seconds to wait between tickets to avoid throttling (default 3.0)
    """
    batch_size = min(max(batch_size, 1), 50)

    # Get unenriched tickets, skipping those that already have PDFs
    with flask_app.app_context():
        candidates = (
            Ticket.query
            .filter_by(enrichment_level='metadata_only')
            .order_by(Ticket.auto_score.desc().nullslast())
            .all()
        )

        if not candidates:
            total = Ticket.query.count()
            return f"All {total} tickets are already enriched! Nothing to scrape."

        # Filter out tickets that already have PDFs
        ticket_ids = []
        for t in candidates:
            pdf_path = os.path.join(PDFS_DIR, f"#{t.zendesk_id}.pdf")
            if not os.path.exists(pdf_path):
                ticket_ids.append(t.zendesk_id)
                if len(ticket_ids) >= batch_size:
                    break

        total_unenriched = Ticket.query.filter_by(enrichment_level='metadata_only').count()

    if not ticket_ids:
        return (
            f"{total_unenriched} tickets are unenriched but all have PDFs on disk.\n"
            f"Run enrich_from_pdf on each to parse and prepare for AI analysis."
        )

    # Scrape in parallel (4 concurrent tabs)
    results = await scrape_batch_parallel(
        browser, ticket_ids, PDFS_DIR, concurrency=4
    )

    # Count PDFs on disk
    pdf_count = sum(1 for f in os.listdir(PDFS_DIR) if f.endswith('.pdf'))

    lines = [
        f"Batch complete: {results['success']} scraped, "
        f"{results['failed']} failed "
        f"(out of {len(ticket_ids)} attempted)",
        f"Total PDFs on disk: {pdf_count}",
        f"Remaining unenriched: {total_unenriched}",
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

    if results['scraped_ids']:
        lines.append(f"\nScraped tickets: {', '.join(results['scraped_ids'])}")

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
    if os.path.exists(PDFS_DIR):
        for f in os.listdir(PDFS_DIR):
            if f.endswith('.pdf'):
                pdf_count += 1
                pdf_size_mb += os.path.getsize(
                    os.path.join(PDFS_DIR, f)
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
# TICKET DISCOVERY TOOLS (find new tickets from Zendesk pages)
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
async def discover_tickets(
    url: str,
    max_pages: int = 20,
) -> str:
    """Navigate to a Zendesk list page and discover ticket IDs not yet in the database.

    Works with any Zendesk page that shows ticket links:
    - Search results: https://redislabs.zendesk.com/agent/search/1?q=...
    - Saved views: https://redislabs.zendesk.com/agent/filters/...
    - Plugin views (lovely-views-plus)

    Read-only: does NOT import tickets. Call import_discovered_tickets to create DB entries.

    Args:
        url: Full Zendesk URL to scrape ticket IDs from
        max_pages: Maximum pagination pages to traverse (default 20, max 50)
    """
    max_pages = min(max(max_pages, 1), 50)

    with flask_app.app_context():
        existing_ids = set(
            row[0] for row in db.session.query(Ticket.zendesk_id).all()
        )

    result = await discover_tickets_from_url(
        browser, url, max_pages=max_pages, existing_ids=existing_ids,
    )

    if result.auth_error:
        return (
            f"Authentication expired: {result.error}\n"
            "Call zendesk_login to re-authenticate."
        )

    if result.error:
        return f"Discovery failed: {result.error}"

    lines = [
        "Ticket Discovery Results",
        "=" * 35,
        f"Pages scanned:    {result.pages_scraped}",
        f"Total found:      {len(result.discovered_ids)}",
        f"Already in DB:    {len(result.existing_ids)}",
        f"NEW (not in DB):  {len(result.new_ids)}",
    ]

    if result.total_results_hint:
        lines.append(f"Zendesk reports:  {result.total_results_hint} total results")

    if result.new_ids:
        lines.append(f"\nNew ticket IDs: {', '.join(result.new_ids[:50])}")
        if len(result.new_ids) > 50:
            lines.append(f"  ... and {len(result.new_ids) - 50} more")
        lines.append(
            f"\nCall import_discovered_tickets to create DB entries for these "
            f"{len(result.new_ids)} tickets."
        )
    else:
        lines.append("\nAll discovered tickets are already in the database.")

    return '\n'.join(lines)


@mcp.tool()
async def import_discovered_tickets(
    ticket_ids: str,
    status: str = "Closed",
) -> str:
    """Create database entries for newly discovered Zendesk ticket IDs.

    Creates minimal ticket records (metadata_only enrichment level) for
    ticket IDs not already in the database. These can then be scraped
    and enriched using bulk_scrape + enrich_from_pdf.

    Args:
        ticket_ids: Comma-separated list of Zendesk ticket IDs (e.g. "153756,153800,154022")
        status: Default status for new tickets (default "Closed")
    """
    from datetime import date as date_type

    ids = [tid.strip() for tid in ticket_ids.split(',') if tid.strip()]
    if not ids:
        return "No ticket IDs provided."

    created = []
    already_exist = []
    errors = []

    with flask_app.app_context():
        for tid in ids:
            try:
                existing = Ticket.query.filter_by(zendesk_id=tid).first()
                if existing:
                    already_exist.append(tid)
                    continue

                ticket = Ticket(
                    zendesk_id=tid,
                    status=status,
                    group_name='Support - L3',
                    assignee='Marko Trapani',
                    created_date=date_type.today(),
                    zendesk_url=f'https://redislabs.zendesk.com/agent/tickets/{tid}',
                    enrichment_level='metadata_only',
                )
                db.session.add(ticket)
                db.session.flush()
                scoring.score_ticket(ticket)
                created.append(tid)
            except Exception as e:
                errors.append(f"#{tid}: {e}")

        db.session.commit()

    lines = [
        f"Import complete: {len(created)} created, "
        f"{len(already_exist)} already existed, "
        f"{len(errors)} errors",
    ]

    if created:
        lines.append(f"\nCreated: {', '.join(created)}")
    if already_exist:
        lines.append(f"Already in DB: {', '.join(already_exist)}")
    if errors:
        lines.append(f"\nErrors:")
        for err in errors:
            lines.append(f"  - {err}")

    if created:
        lines.append(
            f"\nNext: call bulk_scrape() to download PDFs, "
            f"then enrich_from_pdf() for each to parse and analyze."
        )

    return '\n'.join(lines)


@mcp.tool()
async def discover_and_import(
    url: str,
    max_pages: int = 20,
) -> str:
    """Discover tickets from a Zendesk page and import new ones in one step.

    Combines discover_tickets + import_discovered_tickets: navigates to the
    URL, extracts ticket IDs across all pages, compares with the database,
    and creates entries for new tickets.

    Args:
        url: Full Zendesk URL (search results, saved view, etc.)
        max_pages: Maximum pagination pages to traverse (default 20, max 50)
    """
    from datetime import date as date_type

    max_pages = min(max(max_pages, 1), 50)

    with flask_app.app_context():
        existing_ids = set(
            row[0] for row in db.session.query(Ticket.zendesk_id).all()
        )

    result = await discover_tickets_from_url(
        browser, url, max_pages=max_pages, existing_ids=existing_ids,
    )

    if result.auth_error:
        return (
            f"Authentication expired: {result.error}\n"
            "Call zendesk_login to re-authenticate."
        )

    if result.error:
        return f"Discovery failed: {result.error}"

    lines = [
        "Ticket Discovery + Import",
        "=" * 35,
        f"Pages scanned:    {result.pages_scraped}",
        f"Total found:      {len(result.discovered_ids)}",
        f"Already in DB:    {len(result.existing_ids)}",
        f"NEW (not in DB):  {len(result.new_ids)}",
    ]

    if not result.new_ids:
        lines.append("\nAll discovered tickets are already in the database.")
        return '\n'.join(lines)

    # Import new tickets
    created = []
    errors = []

    with flask_app.app_context():
        for tid in result.new_ids:
            try:
                if Ticket.query.filter_by(zendesk_id=tid).first():
                    continue
                ticket = Ticket(
                    zendesk_id=tid,
                    status='Closed',
                    group_name='Support - L3',
                    assignee='Marko Trapani',
                    created_date=date_type.today(),
                    zendesk_url=f'https://redislabs.zendesk.com/agent/tickets/{tid}',
                    enrichment_level='metadata_only',
                )
                db.session.add(ticket)
                db.session.flush()
                scoring.score_ticket(ticket)
                created.append(tid)
            except Exception as e:
                errors.append(f"#{tid}: {e}")

        db.session.commit()
        total_tickets = Ticket.query.count()

    lines.append(f"\nImported {len(created)} new tickets (DB total: {total_tickets})")
    if errors:
        lines.append(f"Errors: {len(errors)}")
        for err in errors:
            lines.append(f"  - {err}")

    lines.append(
        f"\nNext steps:\n"
        f"1. Call bulk_scrape() to download PDFs for new tickets\n"
        f"2. Call enrich_from_pdf(ticket_id) for each to parse + prepare for AI analysis\n"
        f"3. Call get_ticket_for_analysis(ticket_id) + save_ticket_analysis() for STAR fields"
    )

    return '\n'.join(lines)


@mcp.tool()
async def discover_my_closed_tickets(
    max_pages: int = 20,
) -> str:
    """Discover and import closed tickets assigned to Marko Trapani.

    Convenience wrapper that searches Zendesk for all closed tickets
    assigned to Marko Trapani, finds ones not yet in the database,
    and creates DB entries for them.

    Args:
        max_pages: Maximum pagination pages to traverse (default 20, max 50)
    """
    search_url = (
        'https://redislabs.zendesk.com/agent/search/1'
        '?q=assignee%3A%22Marko+Trapani%22+type%3Aticket+status%3Aclosed'
    )
    return await discover_and_import(url=search_url, max_pages=max_pages)


# ═══════════════════════════════════════════════════════════════════
# AI ENRICHMENT TOOLS (for Claude Code to generate STAR summaries)
# ═══════════════════════════════════════════════════════════════════


def _format_conversation(notes, max_chars_per_comment=800):
    """Format TicketNote conversation for AI analysis."""
    lines = []
    for note in notes:
        prefix = "[INTERNAL]" if note.is_internal else "[PUBLIC]"
        ts = note.created_at.strftime('%b %d, %Y %I:%M %p') if note.created_at else '?'
        author = note.author or 'Unknown'
        body = note.content.strip()
        if len(body) > max_chars_per_comment:
            body = body[:max_chars_per_comment] + '...'
        lines.append(f"{prefix} {author} - {ts}\n{body}")
    return '\n\n---\n\n'.join(lines)


@mcp.tool()
async def get_ticket_for_analysis(ticket_id: str) -> str:
    """Get a ticket's full conversation formatted for AI STAR analysis.

    Returns the ticket metadata and chronological conversation thread.
    Use this to read the ticket, then generate STAR fields (summary,
    root_cause, steps_taken, resolution) and save via save_ticket_analysis.

    Args:
        ticket_id: The Zendesk ticket ID
    """
    with flask_app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            return f"Ticket #{ticket_id} not found in database."

        # Get conversation notes in chronological order
        notes = (
            TicketNote.query
            .filter_by(ticket_id=ticket.id, note_type='conversation')
            .order_by(TicketNote.created_at.asc())
            .all()
        )

        lines = [
            f"=== TICKET #{ticket.zendesk_id} ===",
            f"Subject: {ticket.subject or 'N/A'}",
            f"Customer: {ticket.customer_name or 'N/A'}",
            f"Product: {ticket.product_line or ticket.product_area or 'N/A'}",
            f"Priority: {ticket.priority or 'N/A'} | Severity: {ticket.severity or 'N/A'}",
            f"Status: {ticket.status} | Created: {ticket.created_date} | Solved: {ticket.solved_date or 'Open'}",
            f"Resolution Days: {ticket.resolution_days if ticket.resolution_days is not None else 'N/A'}",
            f"Production: {'Yes' if ticket.is_production_outage else 'No'}",
            f"Category: {ticket.category or 'N/A'}",
        ]

        if ticket.problem_summary_zd:
            lines.append(f"\nZendesk Problem Summary: {ticket.problem_summary_zd}")
        if ticket.resolution_summary_zd:
            lines.append(f"Zendesk Resolution Summary: {ticket.resolution_summary_zd}")

        if notes:
            lines.append(f"\n=== CONVERSATION ({len(notes)} comments) ===\n")
            lines.append(_format_conversation(notes))
        else:
            lines.append("\n(No conversation data - ticket needs PDF enrichment first)")

        lines.append(
            "\n\n=== INSTRUCTIONS ==="
            "\nAnalyze this ticket and generate concise STAR-format fields for interview prep:"
            "\n1. summary: 2-4 sentence overview (Situation - what happened and why it mattered)"
            "\n2. root_cause: The technical root cause (what was actually wrong)"
            "\n3. steps_taken: Bullet list of what the support engineer did (investigation, debugging, coordination)"
            "\n4. resolution: The fix/workaround delivered and outcome"
            "\n\nThen call save_ticket_analysis with the generated fields."
        )

        return '\n'.join(lines)


@mcp.tool()
async def save_ticket_analysis(
    ticket_id: str,
    summary: str,
    root_cause: str,
    steps_taken: str,
    resolution: str,
) -> str:
    """Save AI-generated STAR analysis fields to a ticket.

    Call get_ticket_for_analysis first to read the ticket data,
    then generate the STAR fields and pass them here.

    Args:
        ticket_id: The Zendesk ticket ID
        summary: 2-4 sentence situation overview
        root_cause: Technical root cause analysis
        steps_taken: Bullet list of investigation/debugging steps
        resolution: Fix/workaround and outcome
    """
    with flask_app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            return f"Ticket #{ticket_id} not found in database."

        ticket.summary = summary.strip()
        ticket.root_cause = root_cause.strip()
        ticket.steps_taken = steps_taken.strip()
        ticket.resolution = resolution.strip()
        ticket.enrichment_level = 'full'

        # Re-score after updating content
        scoring.score_ticket(ticket)
        from datetime import datetime
        ticket.updated_at = datetime.utcnow()

        db.session.commit()

        return (
            f"Saved STAR analysis for #{ticket_id}:\n"
            f"  Summary: {len(summary)} chars\n"
            f"  Root Cause: {len(root_cause)} chars\n"
            f"  Steps Taken: {len(steps_taken)} chars\n"
            f"  Resolution: {len(resolution)} chars\n"
            f"  New Score: {ticket.final_score}"
        )


@mcp.tool()
async def reset_ticket_enrichment(ticket_id: str) -> str:
    """Reset a ticket's enrichment data back to metadata_only state.

    Clears all enriched fields (summary, description, root_cause, steps_taken,
    resolution, conversation notes) while preserving CSV-imported metadata.
    Useful for re-enriching tickets from scratch.

    Args:
        ticket_id: The Zendesk ticket ID
    """
    from backend.services.enrichment import reset_ticket_enrichment as _reset

    with flask_app.app_context():
        ticket = Ticket.query.filter_by(zendesk_id=str(ticket_id)).first()
        if not ticket:
            return f"Ticket #{ticket_id} not found in database."

        deleted = _reset(ticket)
        db.session.commit()

        return (
            f"Reset #{ticket_id} to metadata_only.\n"
            f"  Cleared: summary, description, root_cause, steps_taken, resolution\n"
            f"  Deleted: {deleted} conversation notes\n"
            f"  New score: {ticket.final_score}"
        )


# ── Chat / Retrieval ─────────────────────────────────────────────


@mcp.tool()
async def query_tickets(question: str) -> str:
    """Search tickets using natural language and return formatted results for analysis.

    Parses the question to extract keywords and filters, searches the FTS5 index
    and structured fields, and returns the top matching tickets with their STAR
    analysis data.

    Examples:
        "What CRDB tickets involved data loss?"
        "Show me P1 production outages"
        "Top scored tickets about TLS certificate issues"
        "Find tickets where we created a custom script"
        "Most significant cluster failover problems"

    Args:
        question: Natural language question about support tickets
    """
    from backend.services.retrieval import query_tickets as _query

    with flask_app.app_context():
        result = _query(question)

    parsed = result['parsed_query']
    lines = [
        f"Query: {question}",
        f"Parsed: keywords={parsed['keywords']}, filters={parsed['filters']}",
        f"Found: {result['ticket_count']} matching tickets",
        "",
        result['formatted_context'],
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
