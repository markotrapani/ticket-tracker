"""Zendesk ticket page navigation, PDF generation, and ticket discovery.

Handles navigating to ticket pages, triggering the Zendesk print view,
and generating PDFs via Playwright's page.pdf() method.

Also supports discovering ticket IDs from Zendesk list pages (search
results, saved views, plugin views) with pagination.

Supports parallel scraping via multiple browser tabs for throughput.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server.browser import BrowserManager

logger = logging.getLogger(__name__)

ZENDESK_BASE = 'https://redislabs.zendesk.com'
ZENDESK_AGENT_TICKETS = f'{ZENDESK_BASE}/agent/tickets'

# Minimum valid PDF size (bytes). Blank popup captures produce ~1KB files;
# the smallest real Zendesk ticket PDF is ~150KB.
MIN_PDF_SIZE_BYTES = 10_000  # 10 KB


class AuthenticationError(Exception):
    """Raised when the Zendesk session has expired."""
    pass


class ScraperError(Exception):
    """Raised on scraping failures."""
    pass


async def scrape_ticket_pdf(browser: BrowserManager, ticket_id: str, output_dir: str) -> str:
    """Navigate to a Zendesk ticket and generate a PDF using a dedicated tab.

    Opens a new tab, navigates to the ticket, triggers the print view,
    saves the PDF, and closes the tab.

    Args:
        browser: BrowserManager instance (must be started)
        ticket_id: Zendesk ticket ID (e.g. "153756")
        output_dir: Directory to save the PDF

    Returns:
        Path to the generated PDF file.

    Raises:
        AuthenticationError: If the session has expired
        ScraperError: If PDF generation fails
    """
    await browser.ensure_started()
    page = await browser._context.new_page()
    output_path = Path(output_dir) / f"#{ticket_id}.pdf"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    ticket_url = f"{ZENDESK_AGENT_TICKETS}/{ticket_id}"
    logger.info(f"Navigating to #{ticket_id}: {ticket_url}")

    try:
        # Navigate to the ticket
        try:
            await page.goto(ticket_url, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            logger.error(f"Navigation failed for #{ticket_id}: {e}")
            raise ScraperError(f"Failed to navigate to ticket #{ticket_id}: {e}")

        # Check for auth redirect
        current_url = page.url
        if 'login' in current_url.lower() or '/agent/' not in current_url:
            raise AuthenticationError(
                f"Session expired - redirected to {current_url}. "
                "Call zendesk_login to re-authenticate."
            )

        # Wait for ticket content to load (Zendesk is a SPA)
        try:
            await page.wait_for_selector(
                '[data-test-id="omni-header-ticket-actions-trigger"]',
                timeout=15000
            )
            logger.info(f"Ticket #{ticket_id} loaded - actions button visible")
        except Exception:
            logger.warning(f"Selector wait timed out for #{ticket_id}, waiting 5s fallback")
            await page.wait_for_timeout(5000)

        # Trigger Zendesk's print view via the ticket options menu
        pdf_generated = await _try_print_view(page, output_path)

        if not pdf_generated:
            raise ScraperError(
                f"Print view failed for #{ticket_id}. "
                f"Current URL: {page.url}"
            )

        if not output_path.exists():
            raise ScraperError(f"PDF file was not created for ticket #{ticket_id}")

        pdf_size = output_path.stat().st_size
        if pdf_size < MIN_PDF_SIZE_BYTES:
            output_path.unlink(missing_ok=True)
            raise ScraperError(
                f"PDF for #{ticket_id} was blank ({pdf_size} bytes) — deleted"
            )

        size_kb = pdf_size / 1024
        logger.info(f"PDF generated for #{ticket_id}: {output_path} ({size_kb:.1f} KB)")
        return str(output_path)

    finally:
        await page.close()


async def _try_print_view(page, output_path: Path, max_retries: int = 2) -> bool:
    """Try to open Zendesk's print ticket popup and generate PDF from it.

    Uses element visibility waits instead of hardcoded delays.
    Retries on failure (e.g. popup timing out).

    Returns True if successful, False if all attempts failed.
    """
    for attempt in range(max_retries):
        if attempt > 0:
            logger.info(f"Retry attempt {attempt + 1}/{max_retries}")

        # Step 1: Click the "Ticket Actions" three-dots button
        actions_selector = '[data-test-id="omni-header-ticket-actions-trigger"]'
        try:
            btn = page.locator(actions_selector)
            await btn.wait_for(state='visible', timeout=5000)
            await btn.click()
            logger.info("Clicked Ticket Actions button")
        except Exception as e:
            logger.info(f"Could not find Ticket Actions button: {e}")
            return False  # No retry — ticket didn't load

        # Step 2: Click "Print ticket" in the dropdown menu
        print_selector = '[data-test-id="omni-header-ticket-actions-menu-item-print"]'
        try:
            el = page.locator(print_selector)
            await el.wait_for(state='visible', timeout=5000)
            await el.click()
            logger.info("Clicked 'Print ticket' menu item")
        except Exception as e:
            logger.info(f"Could not find 'Print ticket' menu item: {e}")
            try:
                await page.keyboard.press('Escape')
            except Exception:
                pass
            continue  # Retry

        # Step 3: Click confirm button and capture the popup
        try:
            async with page.context.expect_page(timeout=15000) as popup_info:
                confirm_btn = page.locator('[data-test-id="print-ticket-confirm-button"]')
                await confirm_btn.wait_for(state='visible', timeout=5000)
                await confirm_btn.click()
                logger.info("Clicked print confirm button")

            popup = await popup_info.value

            # Suppress the native print dialog
            await popup.evaluate("window.print = () => {}")
            await popup.wait_for_load_state('domcontentloaded', timeout=15000)

            # Generate PDF
            await popup.pdf(
                path=str(output_path),
                format='Letter',
                print_background=True,
                margin={'top': '0.5in', 'bottom': '0.5in',
                        'left': '0.5in', 'right': '0.5in'},
            )

            await popup.close()

            # Guard: reject blank PDFs from failed popup renders
            pdf_size = output_path.stat().st_size
            if pdf_size < MIN_PDF_SIZE_BYTES:
                logger.warning(
                    f"PDF too small ({pdf_size} bytes < {MIN_PDF_SIZE_BYTES}) "
                    f"— blank popup capture, retrying"
                )
                output_path.unlink(missing_ok=True)
                continue  # Retry

            logger.info("PDF generated from print view")
            return True

        except Exception as e:
            logger.warning(f"Print popup failed (attempt {attempt + 1}): {e}")
            try:
                await page.keyboard.press('Escape')
            except Exception:
                pass
            # Loop continues to retry

    return False


async def scrape_batch_parallel(
    browser: BrowserManager,
    ticket_ids: list[str],
    output_dir: str,
    concurrency: int = 3,
) -> dict:
    """Scrape multiple tickets in parallel using separate browser tabs.

    Opens up to `concurrency` tabs simultaneously. Navigation happens in
    parallel, but the print-popup step is serialized via a lock to prevent
    popup cross-wiring (context.expect_page is context-scoped).

    Args:
        browser: BrowserManager instance (must be authenticated)
        ticket_ids: List of Zendesk ticket IDs to scrape
        output_dir: Directory to save PDFs
        concurrency: Max parallel tabs (default 3)

    Returns:
        Dict with 'success', 'failed', 'auth_error', 'errors', 'scraped_ids' keys.
    """
    semaphore = asyncio.Semaphore(concurrency)
    # Serialize the popup capture step to avoid cross-wiring
    print_lock = asyncio.Lock()
    results = {
        'success': 0,
        'failed': 0,
        'auth_error': False,
        'errors': [],
        'scraped_ids': [],
    }
    results_lock = asyncio.Lock()

    async def _scrape_one(tid: str):
        async with semaphore:
            if results['auth_error']:
                return

            try:
                await _scrape_with_lock(browser, tid, output_dir, print_lock)
                async with results_lock:
                    results['success'] += 1
                    results['scraped_ids'].append(tid)
                logger.info(f"Scraped #{tid}")
            except AuthenticationError:
                async with results_lock:
                    results['auth_error'] = True
                    results['errors'].append(f"#{tid}: Session expired")
                logger.error(f"Auth expired at ticket #{tid}")
            except (ScraperError, Exception) as e:
                async with results_lock:
                    results['failed'] += 1
                    results['errors'].append(f"#{tid}: {e}")
                logger.error(f"Failed #{tid}: {e}")

    tasks = [_scrape_one(tid) for tid in ticket_ids]
    await asyncio.gather(*tasks)

    return results


async def _scrape_with_lock(
    browser: BrowserManager,
    ticket_id: str,
    output_dir: str,
    print_lock: asyncio.Lock,
) -> str:
    """Scrape a ticket with navigation in parallel but print flow serialized.

    Navigation + SPA load (~5-6s) runs concurrently across tabs.
    The print popup step acquires print_lock to prevent cross-wiring.
    """
    await browser.ensure_started()
    page = await browser._context.new_page()
    output_path = Path(output_dir) / f"#{ticket_id}.pdf"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    ticket_url = f"{ZENDESK_AGENT_TICKETS}/{ticket_id}"
    logger.info(f"Navigating to #{ticket_id}: {ticket_url}")

    try:
        # ── PARALLEL PHASE: Navigate and wait for SPA load ──
        try:
            await page.goto(ticket_url, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            raise ScraperError(f"Failed to navigate to ticket #{ticket_id}: {e}")

        current_url = page.url
        if 'login' in current_url.lower() or '/agent/' not in current_url:
            raise AuthenticationError(
                f"Session expired - redirected to {current_url}."
            )

        try:
            await page.wait_for_selector(
                '[data-test-id="omni-header-ticket-actions-trigger"]',
                timeout=15000
            )
        except Exception:
            logger.warning(f"Selector wait timed out for #{ticket_id}, waiting 5s fallback")
            await page.wait_for_timeout(5000)

        # ── SERIALIZED PHASE: Print flow (popup capture) ──
        async with print_lock:
            pdf_generated = await _try_print_view(page, output_path)

        if not pdf_generated:
            raise ScraperError(f"Print view failed for #{ticket_id}.")

        if not output_path.exists():
            raise ScraperError(f"PDF file was not created for ticket #{ticket_id}")

        pdf_size = output_path.stat().st_size
        if pdf_size < MIN_PDF_SIZE_BYTES:
            output_path.unlink(missing_ok=True)
            raise ScraperError(
                f"PDF for #{ticket_id} was blank ({pdf_size} bytes) — deleted"
            )

        size_kb = pdf_size / 1024
        logger.info(f"PDF generated for #{ticket_id}: {output_path} ({size_kb:.1f} KB)")
        return str(output_path)

    finally:
        await page.close()


# ═══════════════════════════════════════════════════════════════════
# TICKET DISCOVERY (extract IDs from Zendesk list pages)
# ═══════════════════════════════════════════════════════════════════

# JS snippet to find all ticket links on any Zendesk page.
# Works across search results, saved views, and plugin views.
EXTRACT_TICKET_IDS_JS = """
() => {
    const links = document.querySelectorAll('a[href*="/agent/tickets/"]');
    const ids = new Set();
    const pattern = /\\/agent\\/tickets\\/(\\d+)/;
    for (const link of links) {
        const match = link.href.match(pattern);
        if (match) ids.add(match[1]);
    }
    return Array.from(ids);
}
"""


@dataclass
class DiscoveryResult:
    """Result of a ticket discovery operation."""
    discovered_ids: list = field(default_factory=list)
    new_ids: list = field(default_factory=list)
    existing_ids: list = field(default_factory=list)
    pages_scraped: int = 0
    total_results_hint: int | None = None
    auth_error: bool = False
    error: str | None = None


async def extract_ticket_ids_from_page(page) -> list:
    """Extract all ticket IDs from anchor hrefs on the current page.

    Works on any Zendesk page: search results, views, or plugins.
    Returns deduplicated list of ticket ID strings.
    """
    try:
        await page.wait_for_selector(
            'a[href*="/agent/tickets/"]',
            timeout=10000,
        )
    except Exception:
        logger.warning("No ticket links found on page")
        return []

    ids = await page.evaluate(EXTRACT_TICKET_IDS_JS)
    logger.info(f"Extracted {len(ids)} ticket IDs from page")
    return ids


async def discover_tickets_from_url(
    browser: BrowserManager,
    url: str,
    max_pages: int = 20,
    existing_ids: set | None = None,
) -> DiscoveryResult:
    """Navigate to a Zendesk list page and extract ticket IDs across all pages.

    Args:
        browser: BrowserManager instance (must be authenticated)
        url: Full Zendesk URL (search results, view, etc.)
        max_pages: Maximum pagination pages to traverse (safety limit)
        existing_ids: Set of zendesk_ids already in the database.
            If None, all are classified as "discovered".

    Returns:
        DiscoveryResult with discovered, new, and existing ID lists.
    """
    if existing_ids is None:
        existing_ids = set()

    result = DiscoveryResult()
    await browser.ensure_started()
    page = await browser._context.new_page()

    try:
        # Navigate to the target URL
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            result.error = f"Navigation failed: {e}"
            return result

        # Auth check
        current_url = page.url
        if 'login' in current_url.lower() or '/agent/' not in current_url:
            result.auth_error = True
            result.error = f"Session expired - redirected to {current_url}"
            return result

        # Wait for SPA content to settle
        await page.wait_for_timeout(3000)

        # Try to extract total result count
        result.total_results_hint = await _extract_result_count(page)

        all_ids = set()
        page_num = 0

        while page_num < max_pages:
            page_num += 1

            # Extract IDs from current page
            page_ids = await extract_ticket_ids_from_page(page)

            if not page_ids:
                logger.info(f"No ticket IDs found on page {page_num}, stopping")
                break

            new_on_page = set(page_ids) - all_ids
            if not new_on_page:
                logger.info(f"Page {page_num} yielded no new IDs, stopping")
                break

            all_ids.update(page_ids)
            logger.info(
                f"Page {page_num}: {len(page_ids)} IDs "
                f"({len(new_on_page)} new, {len(all_ids)} total)"
            )

            # Try to go to next page
            has_next = await _click_next_page(page)
            if not has_next:
                logger.info(f"No more pages after page {page_num}")
                break

            # Wait for content to update
            await page.wait_for_timeout(2000)

        result.discovered_ids = sorted(all_ids)
        result.pages_scraped = page_num
        result.new_ids = sorted(all_ids - existing_ids)
        result.existing_ids = sorted(all_ids & existing_ids)

        return result

    finally:
        await page.close()


async def _extract_result_count(page) -> int | None:
    """Try to parse the result count from Zendesk search results.

    Looks for text like "1-30 of 142 results" or "142 results".
    """
    try:
        text = await page.evaluate("""
            () => {
                const el = document.querySelector('[data-test-id="search-results-count"]')
                    || document.querySelector('.search-results-count')
                    || document.querySelector('[class*="result"] [class*="count"]');
                return el ? el.textContent : '';
            }
        """)
        if text:
            match = re.search(r'of\s+(\d[\d,]*)\s+result', text)
            if match:
                return int(match.group(1).replace(',', ''))
            match = re.search(r'(\d[\d,]*)\s+result', text)
            if match:
                return int(match.group(1).replace(',', ''))
    except Exception:
        pass
    return None


async def _click_next_page(page) -> bool:
    """Click the 'next page' button in Zendesk pagination.

    Returns True if a next button was found and clicked, False otherwise.
    """
    next_selectors = [
        '[data-test-id="generic-table-pagination-next"]',
        'button[aria-label="Next page"]',
        'nav[aria-label="pagination"] li:last-child a',
        '.pagination-next:not([disabled])',
        'a[rel="next"]',
    ]

    for selector in next_selectors:
        try:
            btn = page.locator(selector).first
            is_visible = await btn.is_visible()
            if not is_visible:
                continue

            is_disabled = await btn.is_disabled()
            if is_disabled:
                return False

            await btn.click()
            logger.info(f"Clicked next page button (selector: {selector})")
            return True
        except Exception:
            continue

    return False
