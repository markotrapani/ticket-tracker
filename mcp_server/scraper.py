"""Zendesk ticket page navigation and PDF generation.

Handles navigating to ticket pages, triggering the Zendesk print view,
and generating PDFs via Playwright's page.pdf() method.
"""

import asyncio
import logging
from pathlib import Path

from mcp_server.browser import BrowserManager

logger = logging.getLogger(__name__)

ZENDESK_BASE = 'https://redislabs.zendesk.com'
ZENDESK_AGENT_TICKETS = f'{ZENDESK_BASE}/agent/tickets'


class AuthenticationError(Exception):
    """Raised when the Zendesk session has expired."""
    pass


class ScraperError(Exception):
    """Raised on scraping failures."""
    pass


async def scrape_ticket_pdf(browser: BrowserManager, ticket_id: str, output_dir: str) -> str:
    """Navigate to a Zendesk ticket and generate a PDF.

    Attempts to use Zendesk's built-in print view (which produces the
    structured format our PDF parser expects). Falls back to direct
    page.pdf() on the agent view if the print view isn't accessible.

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
    page = await browser.get_page()
    output_path = Path(output_dir) / f"#{ticket_id}.pdf"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    ticket_url = f"{ZENDESK_AGENT_TICKETS}/{ticket_id}"
    logger.info(f"Navigating to #{ticket_id}: {ticket_url}")

    # Navigate to the ticket
    try:
        await page.goto(ticket_url, wait_until='networkidle', timeout=30000)
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
            '[data-test-id="omni-log-container"], '
            '[data-test-id="ticket-pane-container"], '
            '.conversation, '
            '.ticket_main',
            timeout=15000
        )
    except Exception:
        # Fallback: give it extra time to load
        logger.warning(f"Selector wait timed out for #{ticket_id}, waiting 5s fallback")
        await page.wait_for_timeout(5000)

    # Try to trigger Zendesk's print view via the ticket options menu
    pdf_generated = False
    try:
        pdf_generated = await _try_print_view(page, output_path)
    except Exception as e:
        logger.warning(f"Print view approach failed for #{ticket_id}: {e}")

    # Fallback: direct page.pdf() on the agent view
    if not pdf_generated:
        logger.info(f"Using direct page.pdf() fallback for #{ticket_id}")
        try:
            await page.pdf(
                path=str(output_path),
                format='Letter',
                print_background=True,
                margin={'top': '0.5in', 'bottom': '0.5in', 'left': '0.5in', 'right': '0.5in'},
            )
            pdf_generated = True
        except Exception as e:
            raise ScraperError(f"Failed to generate PDF for #{ticket_id}: {e}")

    if not output_path.exists():
        raise ScraperError(f"PDF file was not created for ticket #{ticket_id}")

    size_kb = output_path.stat().st_size / 1024
    logger.info(f"PDF generated for #{ticket_id}: {output_path} ({size_kb:.1f} KB)")
    return str(output_path)


async def _try_print_view(page, output_path: Path) -> bool:
    """Try to open Zendesk's print ticket popup and generate PDF from it.

    Returns True if successful, False if the approach failed.
    """
    # Look for the ticket options/overflow menu button
    # Zendesk uses various selectors depending on version
    overflow_selectors = [
        '[data-test-id="header-toolbar-overflow-menu"]',
        'button[aria-label="Ticket actions"]',
        '[data-garden-id="buttons.icon_button"][aria-haspopup="true"]',
        '.pane_header button[data-test-id]',
    ]

    clicked = False
    for selector in overflow_selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                clicked = True
                logger.info(f"Clicked overflow menu with selector: {selector}")
                break
        except Exception:
            continue

    if not clicked:
        logger.info("Could not find overflow menu button")
        return False

    await page.wait_for_timeout(500)

    # Look for "Print ticket" menu item
    print_selectors = [
        'text=Print ticket',
        '[data-test-id="print-ticket"]',
        'li:has-text("Print ticket")',
        'a:has-text("Print")',
    ]

    # Listen for popup (print view opens in new window)
    try:
        async with page.context.expect_page(timeout=10000) as popup_info:
            for selector in print_selectors:
                try:
                    el = page.locator(selector).first
                    if await el.is_visible(timeout=1000):
                        await el.click()
                        logger.info(f"Clicked print with selector: {selector}")
                        break
                except Exception:
                    continue

        popup = await popup_info.value
        await popup.wait_for_load_state('networkidle', timeout=15000)

        # Generate PDF from the print view popup
        await popup.pdf(
            path=str(output_path),
            format='Letter',
            print_background=True,
            margin={'top': '0.5in', 'bottom': '0.5in', 'left': '0.5in', 'right': '0.5in'},
        )

        await popup.close()
        logger.info("PDF generated from print view popup")
        return True

    except Exception as e:
        logger.warning(f"Print popup approach failed: {e}")
        # Close any menu that might still be open
        try:
            await page.keyboard.press('Escape')
        except Exception:
            pass
        return False


async def scrape_with_rate_limit(
    browser: BrowserManager,
    ticket_id: str,
    output_dir: str,
    delay_seconds: float = 3.0,
) -> str:
    """Scrape a ticket PDF with a delay afterward for rate limiting.

    Args:
        browser: BrowserManager instance
        ticket_id: Zendesk ticket ID
        output_dir: Directory to save the PDF
        delay_seconds: Seconds to wait after scraping

    Returns:
        Path to the generated PDF file.
    """
    result = await scrape_ticket_pdf(browser, ticket_id, output_dir)
    await asyncio.sleep(delay_seconds)
    return result
