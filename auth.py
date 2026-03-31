import logging
import time

from playwright.async_api import Page, BrowserContext

from config import MEDVIND_URL, SSO_EMAIL, SSO_PASSWORD, SESSION_STATE_FILE
from browser import (
    human_delay,
    human_type,
    human_click,
    random_mouse_wander,
    save_session,
)

logger = logging.getLogger("medvind.auth")

# Max ålder för sparad session (sekunder)
SESSION_MAX_AGE = 12 * 60 * 60  # 12 timmar


def session_is_fresh() -> bool:
    """Kolla om sparad session fortfarande är giltig (< 12h gammal)."""
    if not SESSION_STATE_FILE.exists():
        return False
    age = time.time() - SESSION_STATE_FILE.stat().st_mtime
    return age < SESSION_MAX_AGE


async def ensure_authenticated(page: Page, context: BrowserContext) -> bool:
    """Logga in via Microsoft SSO om det behövs. Returnerar True vid lyckad auth."""
    for attempt in range(3):
        try:
            return await _try_authenticate(page, context)
        except Exception as e:
            logger.error("Inloggningsförsök %d misslyckades: %s", attempt + 1, e)
            if attempt < 2:
                await human_delay(3000, 6000)
                try:
                    await page.screenshot(
                        path=str(SESSION_STATE_FILE.parent / "debug_screenshot.png")
                    )
                except Exception:
                    pass
            else:
                return False
    return False


async def _try_authenticate(page: Page, context: BrowserContext) -> bool:
    """Enskilt inloggningsförsök."""
    logger.info("Navigerar till Medvind...")
    await page.goto(MEDVIND_URL, wait_until="domcontentloaded", timeout=30000)
    await human_delay(2000, 4000)

    current_url = page.url

    # Redan inloggad?
    if "medvindwfm.se" in current_url and "login" not in current_url.lower():
        logger.info("Redan inloggad via sparad session")
        return True

    # Behöver logga in via Microsoft SSO
    if "microsoftonline.com" not in current_url and "login.medvindwfm.se" not in current_url:
        logger.warning("Oväntat URL efter redirect: %s", current_url)
        # Vänta lite och kolla igen
        await human_delay(3000, 5000)
        current_url = page.url
        if "microsoftonline.com" not in current_url:
            raise RuntimeError(f"Hamnade på oväntat URL: {current_url}")

    logger.info("SSO-inloggning krävs, startar...")

    # Simulera mänsklig aktivitet
    await random_mouse_wander(page)

    # --- E-post ---
    email_selector = 'input[type="email"], input[name="loginfmt"]'
    await page.wait_for_selector(email_selector, timeout=15000)
    await human_delay(500, 1500)
    await human_type(page, email_selector, SSO_EMAIL)
    await human_delay(400, 1000)

    # Klicka "Nästa"
    next_btn = 'input[type="submit"], #idSIButton9'
    await human_click(page, next_btn)
    await human_delay(2000, 4000)

    # --- Lösenord ---
    password_selector = 'input[type="password"], input[name="passwd"]'
    await page.wait_for_selector(password_selector, state="visible", timeout=15000)
    await human_delay(500, 1500)
    await random_mouse_wander(page)
    await human_type(page, password_selector, SSO_PASSWORD)
    await human_delay(400, 1000)

    # Klicka "Logga in"
    await human_click(page, next_btn)
    await human_delay(3000, 5000)

    # --- "Förbli inloggad?" / "Stay signed in?" ---
    try:
        stay_signed_in = '#idSIButton9, #idBtn_Back, input[value="Yes"], input[value="Ja"]'
        await page.wait_for_selector(stay_signed_in, timeout=8000)
        await human_delay(800, 2000)
        # Klicka "Ja" / "Yes"
        try:
            await human_click(page, '#idSIButton9')
        except Exception:
            await human_click(page, 'input[value="Yes"], input[value="Ja"]')
        await human_delay(2000, 4000)
    except Exception:
        # Prompten visades inte — fortsätt
        logger.debug("Ingen 'Förbli inloggad'-prompt")

    # --- Vänta på redirect tillbaka till Medvind ---
    try:
        await page.wait_for_url("**/medvindwfm.se/**", timeout=30000)
    except Exception:
        # Kolla om vi är på rätt sida ändå
        if "medvindwfm.se" not in page.url:
            raise RuntimeError(f"Redirect misslyckades, URL: {page.url}")

    await human_delay(1500, 3000)

    # Spara sessionen
    await save_session(context)
    logger.info("Inloggning lyckades!")
    return True
