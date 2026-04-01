import asyncio
import random
import logging
import time
from datetime import datetime

import schedule

from browser import create_stealth_browser
from auth import ensure_authenticated
from scraper import scrape_shifts
from notifier import check_and_notify
from klarmarkera import klarmarkera_week
from settings import load_settings

logger = logging.getLogger("medvind.scheduler")

_last_full_scrape: float = 0.0


async def run_full_scrape():
    """Full scrape: kalender + kollegor + rapport + korsvalidering."""
    global _last_full_scrape
    logger.info("=== FULL SCRAPE ===")

    try:
        async with create_stealth_browser(headless=True) as (browser, context, page):
            if not await ensure_authenticated(page, context):
                logger.error("Inloggning misslyckades")
                return

            shifts = await scrape_shifts(page)
            if not shifts:
                logger.warning("Inga pass hittades")
                return

            logger.info("Hittade %d pass", len(shifts))
            check_and_notify(shifts)

            settings = load_settings()
            if settings.get("auto_klarmarkera") and datetime.now().isoweekday() == 7:
                logger.info("Söndag — kör automatisk klarmarkering")
                try:
                    await page.locator('a:has-text("Kalender")').first.click()
                    await asyncio.sleep(2)
                except Exception:
                    pass
                await klarmarkera_week(page)

    except Exception as e:
        logger.error("Full scrape misslyckades: %s", e, exc_info=True)

    _last_full_scrape = time.time()
    logger.info("Full scrape klar")


async def run_quick_check():
    """Quick check: logga in, kolla Översikt planering, jämför med sparad data."""
    settings = load_settings()
    scrape_interval = settings["scrape_interval_hours"] * 3600

    # Om det är dags för full scrape, kör den istället
    if time.time() - _last_full_scrape >= scrape_interval:
        await run_full_scrape()
        return

    logger.info("--- Quick check ---")
    try:
        async with create_stealth_browser(headless=True) as (browser, context, page):
            if not await ensure_authenticated(page, context):
                logger.error("Inloggning misslyckades")
                return

            shifts = await scrape_shifts(page)
            if not shifts:
                logger.warning("Inga pass hittades vid quick check")
                return

            logger.info("Quick check: %d pass verifierade", len(shifts))
            check_and_notify(shifts)

    except Exception as e:
        logger.error("Quick check misslyckades: %s", e, exc_info=True)

    logger.info("Quick check klar")


async def _run_with_jitter_async(task):
    jitter = random.randint(-5, 5) * 60
    if jitter > 0:
        await asyncio.sleep(jitter)
    await task()


def _run_quick_check():
    asyncio.run(_run_with_jitter_async(run_quick_check))


def start_scheduler():
    """Starta schemaläggaren med inställningar från settings.json."""
    settings = load_settings()
    check_minutes = settings["check_interval_minutes"]

    logger.info("Kör initial full scrape...")
    asyncio.run(run_full_scrape())

    # Quick check varje X minuter (inkl. auto-full-scrape vid behov)
    schedule.every(check_minutes).minutes.do(_run_quick_check)

    # Klarmarkering
    if settings.get("auto_klarmarkera"):
        day = settings.get("klarmarkera_day", "sunday")
        t = settings.get("klarmarkera_time", "21:00")
        getattr(schedule.every(), day).at(t).do(
            lambda: asyncio.run(run_klarmarkera())
        )

    logger.info(
        "Schemaläggare startad: check var %d min, full scrape var %dh",
        check_minutes, settings["scrape_interval_hours"],
    )

    while True:
        schedule.run_pending()
        time.sleep(60)


async def run_klarmarkera():
    """Dedikerad klarmarkerings-körning."""
    logger.info("Kör schemalagd klarmarkering...")
    try:
        async with create_stealth_browser(headless=True) as (browser, context, page):
            if not await ensure_authenticated(page, context):
                logger.error("Inloggning misslyckades")
                return
            await klarmarkera_week(page)
    except Exception as e:
        logger.error("Klarmarkering misslyckades: %s", e, exc_info=True)
