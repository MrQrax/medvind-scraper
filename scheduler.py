import asyncio
import random
import logging
from datetime import datetime

import schedule

from config import CHECK_INTERVAL_HOURS
from browser import create_stealth_browser
from auth import ensure_authenticated
from scraper import scrape_shifts
from notifier import check_and_notify
from klarmarkera import klarmarkera_week

logger = logging.getLogger("medvind.scheduler")


async def run_check_cycle():
    """Kör en komplett check: logga in, hämta schema, detektera ändringar, notifiera."""
    logger.info("Startar schema-check...")

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

            # Ändringsdetektering + påminnelser
            check_and_notify(shifts)

            # Klarmarkera automatiskt på söndagar
            if datetime.now().isoweekday() == 7:
                logger.info("Söndag — kör automatisk klarmarkering")
                try:
                    await page.locator('a:has-text("Kalender")').first.click()
                    await asyncio.sleep(2)
                except Exception:
                    pass
                await klarmarkera_week(page)

    except Exception as e:
        logger.error("Schema-check misslyckades: %s", e, exc_info=True)

    logger.info("Schema-check klar")


async def _run_check_with_jitter_async():
    jitter = random.randint(-5, 5) * 60  # ±5 min
    if jitter > 0:
        await asyncio.sleep(jitter)
    await run_check_cycle()


def _run_check_with_jitter():
    asyncio.run(_run_check_with_jitter_async())


def start_scheduler():
    """Starta schemaläggaren: check varje timme + klarmarkering söndag 21:00."""
    logger.info("Kör initial schema-check...")
    asyncio.run(run_check_cycle())

    # Schema varje timme
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(_run_check_with_jitter)

    # Klarmarkera söndag kväll kl 21:00
    schedule.every().sunday.at("21:00").do(
        lambda: asyncio.run(run_klarmarkera())
    )

    logger.info(
        "Schemaläggare startad: check varje %dh, klarmarkering söndag 21:00",
        CHECK_INTERVAL_HOURS,
    )

    import time
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
