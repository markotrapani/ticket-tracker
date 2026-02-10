"""Playwright browser lifecycle manager with persistent session.

Uses a persistent Chromium context so Zendesk login (including SSO/MFA)
survives across MCP server restarts.
"""

import logging
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

logger = logging.getLogger(__name__)

ZENDESK_DASHBOARD = 'https://redislabs.zendesk.com/agent/dashboard'


class BrowserManager:
    """Manages a persistent Playwright browser context for Zendesk access."""

    def __init__(self, user_data_dir: str, headless: bool = True):
        self._user_data_dir = str(Path(user_data_dir).resolve())
        self._default_headless = headless
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._is_headless: bool | None = None

        # Ensure data dir exists
        Path(self._user_data_dir).mkdir(parents=True, exist_ok=True)

    async def ensure_started(self, headless: bool | None = None):
        """Start browser if not already running."""
        if self._context is not None:
            return

        use_headless = headless if headless is not None else self._default_headless
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            headless=use_headless,
            accept_downloads=False,
            viewport={'width': 1280, 'height': 1024},
        )
        self._is_headless = use_headless

        # Reuse existing page or create new one
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        logger.info(f"Browser started (headless={use_headless})")

    async def get_page(self) -> Page:
        """Get the active page, starting browser if needed."""
        await self.ensure_started()
        return self._page

    async def restart_headed(self):
        """Restart browser in headed (visible) mode for manual login."""
        await self.close()
        await self.ensure_started(headless=False)

    async def restart_headless(self):
        """Restart browser in headless mode for efficient scraping."""
        await self.close()
        await self.ensure_started(headless=True)

    async def close(self):
        """Close browser and cleanup."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.warning(f"Error closing browser context: {e}")
            self._context = None
            self._page = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            self._playwright = None
        self._is_headless = None

    async def is_authenticated(self) -> bool:
        """Check if the current session is authenticated to Zendesk.

        Navigates to the agent dashboard and checks whether we land on
        a login page or the actual dashboard.
        """
        page = await self.get_page()
        try:
            await page.goto(ZENDESK_DASHBOARD,
                            wait_until='domcontentloaded', timeout=20000)
            current_url = page.url
            # Authenticated if we're on an /agent/ page and not redirected to login
            is_auth = '/agent/' in current_url and 'login' not in current_url.lower()
            logger.info(f"Auth check: url={current_url}, authenticated={is_auth}")
            return is_auth
        except Exception as e:
            logger.error(f"Auth check failed: {e}")
            return False

    @property
    def is_headless(self) -> bool | None:
        """Whether the browser is currently in headless mode."""
        return self._is_headless

    @property
    def is_running(self) -> bool:
        """Whether the browser is currently running."""
        return self._context is not None
