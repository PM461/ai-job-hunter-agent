import asyncio
import logging
import random
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

from app.config import settings, BROWSER_STATE_DIR

logger = logging.getLogger("JobAgent.Stealth")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]


async def human_delay(min_sec: float = 2.5, max_sec: float = 7.5):
    """Pausa casuale con distribuzione gaussiana troncata per imitare i tempi di lettura umani."""
    mean = (min_sec + max_sec) / 2
    std_dev = (max_sec - min_sec) / 4
    delay = random.gauss(mean, std_dev)
    delay = max(min_sec, min(max_sec, delay))
    await asyncio.sleep(delay)


async def human_type(page: Page, selector: str, text: str):
    """Digita il testo carattere per carattere con ritardi variabili umani."""
    element = page.locator(selector).first
    await element.click()
    await human_delay(0.4, 1.1)
    for char in text:
        await element.type(char, delay=random.randint(45, 165))
        if random.random() < 0.08:
            await asyncio.sleep(random.uniform(0.15, 0.45))


async def human_scroll(page: Page):
    """Simula lo scrolling graduale di lettura della pagina con movimenti del mouse."""
    try:
        viewport = page.viewport_size or {"width": 1366, "height": 768}
        for _ in range(random.randint(2, 5)):
            # Movimento mouse leggero
            x = random.randint(100, viewport["width"] - 100)
            y = random.randint(100, viewport["height"] - 100)
            await page.mouse.move(x, y, steps=random.randint(5, 15))

            scroll_amount = random.randint(250, 650)
            await page.mouse.wheel(0, scroll_amount)
            await human_delay(1.2, 3.2)
    except Exception as e:
        logger.debug(f"Scroll umano ignorato: {e}")


class StealthBrowserManager:
    """
    Gestisce il browser Playwright con profili e cookie persistenti su disco
    in modo che LinkedIn e i portali aziendali riconoscano la stessa sessione
    senza richiedere login ad ogni ora (evitando blocchi e captcha).
    """

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name
        self.state_file: Path = BROWSER_STATE_DIR / f"{profile_name}_state.json"
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    async def __aenter__(self) -> BrowserContext:
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=settings.HEADLESS_BROWSER,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
        )

        context_kwargs = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": USER_AGENTS[0],
            "locale": "it-IT",
            "timezone_id": "Europe/Rome",
        }

        if self.state_file.exists():
            logger.info(f"Caricamento sessione persistente da {self.state_file.name}")
            context_kwargs["storage_state"] = str(self.state_file)

        self.context = await self.browser.new_context(**context_kwargs)

        # Nasconde navigator.webdriver e fingerprint di automazione
        await self.context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['it-IT', 'it', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            """
        )
        return self.context

    async def new_stealth_page(self) -> Page:
        page = await self.context.new_page()
        if stealth_async is not None:
            try:
                await stealth_async(page)
            except Exception as e:
                logger.debug(f"Playwright stealth warning: {e}")
        return page

    async def save_state(self):
        if self.context:
            await self.context.storage_state(path=str(self.state_file))
            logger.info(f"Sessione e cookie salvati in {self.state_file.name}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.save_state()
        except Exception as e:
            logger.debug(f"Impossibile salvare lo stato del browser: {e}")
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

