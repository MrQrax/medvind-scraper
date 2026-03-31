import logging
from datetime import datetime, timedelta

from playwright.async_api import Page

from browser import human_delay, random_mouse_wander

logger = logging.getLogger("medvind.klarmarkera")


def _last_sunday() -> str:
    """Returnera senaste söndag (eller idag om söndag) som YYYY-MM-DD."""
    today = datetime.now().date()
    days_since_sunday = today.isoweekday() % 7  # sön=0, mån=1, …, lör=6
    sunday = today - timedelta(days=days_since_sunday)
    return sunday.strftime("%Y-%m-%d")


async def klarmarkera_week(page: Page) -> bool:
    """Klarmarkera senaste veckan i Medvind.

    Öppnar Klarmarkera-dialogen, sätter T.o.m-datum till senaste söndag,
    bockar i raden och sparar. Returnerar True om det lyckades.
    """
    sunday = _last_sunday()
    logger.info("Klarmarkerar t.o.m %s", sunday)

    try:
        # Säkerställ att vi är på Kalender-vyn
        try:
            kal = page.locator('a:has-text("Kalender")').first
            if await kal.is_visible(timeout=3000):
                await kal.click()
                await human_delay(2000, 3000)
        except Exception:
            pass

        await random_mouse_wander(page)

        # ── 1. Öppna Klarmarkera-menyn ──
        btn = page.locator('.x-btn:has-text("Klarmarkera")').first
        box = await btn.bounding_box()
        if not box:
            logger.error("Hittade inte Klarmarkera-knappen")
            return False

        await page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
        await human_delay(1500, 2500)

        menu_item = page.locator('.x-menu-item:has-text("Klarmarkera"):visible').first
        await menu_item.click()
        await human_delay(2000, 3000)

        # ── 2. Verifiera att dialogen öppnades ──
        dialog = page.locator('.x-window:visible:has-text("Klarmarkera")')
        if not await dialog.count():
            logger.error("Klarmarkera-dialogen öppnades inte")
            return False

        # ── 3. Kolla om redan klarmarkerad t.o.m detta datum ──
        row_text = await dialog.locator('.x-grid-row').first.inner_text(timeout=3000)
        if sunday in row_text:
            logger.info("Redan klarmarkerad t.o.m %s", sunday)
            await dialog.locator('.x-btn:has-text("Avbryt")').first.click()
            await human_delay(500, 1000)
            return True

        # ── 4. Sätt T.o.m-datum via ExtJS API ──
        date_set = await page.evaluate("""(dateStr) => {
            const fields = Ext.ComponentQuery.query('datefield');
            for (const f of fields) {
                // Hitta fältet som ligger i dialogen
                if (f.el && f.el.dom.closest('.x-window')) {
                    f.setValue(dateStr);
                    return f.getRawValue();
                }
            }
            if (fields.length > 0) {
                fields[0].setValue(dateStr);
                return fields[0].getRawValue();
            }
            return null;
        }""", sunday)

        if not date_set:
            logger.error("Kunde inte sätta datum")
            await dialog.locator('.x-btn:has-text("Avbryt")').first.click()
            return False

        logger.info("Datum satt till: %s", date_set)
        await human_delay(800, 1500)

        # ── 5. Klicka Utför ──
        utfor_btn = dialog.locator('.x-btn:has-text("Utför")').first
        if await utfor_btn.is_visible(timeout=2000):
            await utfor_btn.click()
            await human_delay(2000, 4000)

        # ── 6. Bocka i checkboxen för raden ──
        checked = await page.evaluate("""() => {
            // Hitta gridden i dialogen och välj första raden
            const wins = Ext.ComponentQuery.query('window');
            for (const w of wins) {
                if (w.isVisible() && w.title && w.title.includes('Klarmarkera')) {
                    const grids = w.query('grid');
                    for (const g of grids) {
                        const store = g.getStore();
                        if (store && store.getCount() > 0) {
                            const selModel = g.getSelectionModel();
                            selModel.selectAll();
                            return true;
                        }
                    }
                }
            }
            return false;
        }""")

        if not checked:
            logger.warning("Kunde inte bocka i raden, försöker klicka")
            row = dialog.locator('.x-grid-row').first
            await row.click()
            await human_delay(500, 1000)

        await human_delay(500, 1000)

        # ── 7. Spara ──
        save_btn = dialog.locator('.x-btn:has-text("Spara")').first
        await save_btn.click()
        await human_delay(3000, 5000)

        # Kolla om dialogen stängdes
        still_visible = False
        try:
            still_visible = await dialog.first.is_visible(timeout=2000)
        except Exception:
            pass

        if still_visible:
            text = await dialog.first.inner_text()
            logger.warning("Dialogen fortfarande öppen: %s", text[:200])
            await dialog.locator('.x-btn:has-text("Avbryt")').first.click()
            return False

        logger.info("Klarmarkering sparad t.o.m %s", sunday)
        return True

    except Exception as e:
        logger.error("Klarmarkering misslyckades: %s", e, exc_info=True)
        try:
            cancel = page.locator('.x-window:visible >> .x-btn:has-text("Avbryt")')
            if await cancel.count():
                await cancel.first.click()
        except Exception:
            pass
        return False
