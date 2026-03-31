import asyncio
import random
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

from config import SESSION_STATE_FILE

logger = logging.getLogger("medvind.browser")

# Aktuell Chrome UA-sträng (Windows 11)
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

# Stealth-konfiguration
_stealth = Stealth(
    navigator_languages_override=("sv-SE", "sv", "en-US", "en"),
    navigator_platform_override="Win32",
    navigator_user_agent_override=CHROME_UA,
    navigator_vendor_override="Google Inc.",
    webgl_vendor_override="Google Inc. (Intel)",
    webgl_renderer_override="ANGLE (Intel, Intel(R) UHD Graphics, D3D11)",
)


@asynccontextmanager
async def create_stealth_browser(headless: bool = True):
    """Skapar en stealth Playwright-browser med realistisk kontext."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-infobars",
            ],
        )

        # Kontext med session-återanvändning om tillgänglig
        context_opts = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "sv-SE",
            "timezone_id": "Europe/Stockholm",
            "user_agent": CHROME_UA,
        }
        if SESSION_STATE_FILE.exists():
            try:
                context_opts["storage_state"] = str(SESSION_STATE_FILE)
                logger.info("Återanvänder sparad session")
            except Exception:
                logger.warning("Kunde inte ladda session, startar ny")

        context = await browser.new_context(**context_opts)
        await _stealth.apply_stealth_async(context)

        page = await context.new_page()

        try:
            yield browser, context, page
        finally:
            await browser.close()


async def save_session(context: BrowserContext):
    """Sparar cookies och localStorage till fil."""
    SESSION_STATE_FILE.parent.mkdir(exist_ok=True)
    await context.storage_state(path=str(SESSION_STATE_FILE))
    logger.info("Session sparad")


async def human_delay(min_ms: int = 300, max_ms: int = 1200):
    """Slumpmässig fördröjning som simulerar mänskligt beteende."""
    delay = random.uniform(min_ms, max_ms) / 1000
    await asyncio.sleep(delay)


async def human_type(page: Page, selector: str, text: str):
    """Skriver text tecken för tecken med variabel hastighet."""
    await page.click(selector)
    await human_delay(200, 500)

    for i, char in enumerate(text):
        await page.keyboard.type(char, delay=random.randint(50, 150))
        # Slumpmässig paus var 4-8:e tecken
        if i > 0 and i % random.randint(4, 8) == 0:
            await human_delay(100, 400)


async def human_click(page: Page, selector: str):
    """Klickar med slumpmässig offset från elementets mitt."""
    element = page.locator(selector)
    box = await element.bounding_box()
    if box:
        # Slumpmässig offset inom elementet
        offset_x = random.uniform(-box["width"] * 0.2, box["width"] * 0.2)
        offset_y = random.uniform(-box["height"] * 0.2, box["height"] * 0.2)
        x = box["x"] + box["width"] / 2 + offset_x
        y = box["y"] + box["height"] / 2 + offset_y

        # Flytta musen dit först, sedan klicka
        await page.mouse.move(x, y)
        await human_delay(100, 300)
        await page.mouse.click(x, y)
    else:
        # Fallback: vanlig klick
        await element.click()

    await human_delay(200, 600)


async def random_mouse_wander(page: Page):
    """Rör musen slumpmässigt på sidan — simulerar tomgång."""
    steps = random.randint(2, 4)
    for _ in range(steps):
        x = random.randint(100, 1800)
        y = random.randint(100, 900)
        await page.mouse.move(x, y, steps=random.randint(5, 15))
        await human_delay(200, 800)
