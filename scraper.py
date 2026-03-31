import json
import re
import logging
from datetime import datetime

from playwright.async_api import Page

from config import SHIFTS_FILE
from browser import human_delay, random_mouse_wander

logger = logging.getLogger("medvind.scraper")

# ── Medvind-konstanter (NHC-konfiguration) ──────────────────────────

# Färgkategorier (style-color → status)
COLOR_WORKING = {"#0000FF"}                    # Blå = normalt schemalagt arbete
COLOR_ABSENT = {"#FF0000", "#800000"}          # Röd/mörkröd = frånvaro (fast/preliminär)
COLOR_EXTRA = {"#FF00FF"}                      # Rosa/magenta = extratid (hoppar in)
COLOR_OTHER = {"#00FF00", "#008000"}           # Grön = tillgänglig / uttag

# Typkoder → beskrivning
TYPE_DESCRIPTIONS = {
    "Ar": "Arbete",
    "Fr": "Frånvaro",
    "FL": "Föräldraledig",
    "Se": "Semester",
    "Sj": "Sjuk",
    "Tj": "Tjänstledig",
    "Vb": "Vikariebokad",
    "Ka": "Kan arbeta",
    "Ej": "Ej tillgänglig",
}

# Typkoder som alltid räknas som frånvaro oavsett färg
ABSENT_TYPES = {"Fr", "FL", "Se", "Sj", "Tj"}

# Annotations i celltext
ANNOTATION_PATTERNS = {
    "ckare": "Täckare",         # Täckare → jobbar på Kaninholmen & Kiaholmen
    "Vak": "Vakant",            # Vakant pass
    "Annan v": "Annan våning",  # Jobbar på annan våning
    "te/APT": "Möte/APT",       # Arbetsplatsträff
}


async def scrape_shifts(page: Page) -> list[dict]:
    """Hämtar schemapass + kollegor från Medvind-portalen."""
    await human_delay(1000, 2000)
    await random_mouse_wander(page)

    # Steg 1: Hämta egna pass från kalender-vyn
    try:
        kalender = page.locator('a:has-text("Kalender")').first
        if await kalender.is_visible(timeout=3000):
            await kalender.click()
            await human_delay(2000, 4000)
    except Exception:
        pass

    await page.wait_for_load_state("networkidle", timeout=15000)
    await human_delay(1000, 2000)

    shifts = await _extract_medvind_calendar(page)

    if not shifts:
        logger.warning("Inga pass hittades — sparar debug-screenshot")
        await page.screenshot(path=str(SHIFTS_FILE.parent / "debug_schedule.png"))
        _save_shifts(shifts)
        return shifts

    # Steg 2: Hämta kollegor + Täckare-dagar från Översikt planering
    coworker_map, tackare_dates = await _scrape_coworkers(page)

    for shift in shifts:
        # Sätt location baserat på Översikt planering (pålitligare än kalender-vy)
        if shift["date"] in tackare_dates:
            shift["location"] = "Kaninholmen & Kiaholmen"
        else:
            shift["location"] = "Rådmansholmen"

        people = coworker_map.get(shift["date"], [])

        # Tagga nattkollegor med avdelning
        workers = [p for p in people if p["status"] == "working"]
        for p in workers:
            if not p["note"]:
                p["note"] = "Rådmansholmen"

        shift["coworkers"] = [_format_person(p) for p in workers]
        shift["covering"] = [
            _format_person(p) for p in people if p["status"] == "covering"
        ]
        shift["absent"] = [
            _format_person(p) for p in people if p["status"] == "absent"
        ]
        shift["kan_arbeta"] = [
            p["name"] for p in people if p["status"] == "kan_arbeta"
        ]

    # Steg 3: Hämta dagpersonal som slutar ~21 för rapport
    await _scrape_rapport(page, shifts)

    _save_shifts(shifts)
    return shifts


def _format_person(p: dict) -> str:
    """Formatera person med anteckning, t.ex. 'Anna (Sjuk)' eller 'Erik (Täckare)'."""
    if p["note"]:
        return f"{p['name']} ({p['note']})"
    return p["name"]


async def _scrape_coworkers(page: Page) -> tuple[dict[str, list[dict]], set[str]]:
    """Navigera till Översikt planering och hämta teamets status per dag.

    Returnerar (coworker_map, tackare_dates) där:
    - coworker_map = {datum_str: [{name, status, note}, ...]} exklusive Jonny Nilsen
    - tackare_dates = set av datum-strängar där Jonny är Täckare
    """
    try:
        med = page.locator('.x-toolbar >> text=Medarbetare').first
        await med.click()
        await human_delay(2000, 3000)
        await page.wait_for_selector('.x-menu-item:visible', timeout=5000)
        await human_delay(500, 1000)

        item = page.locator('.x-menu-item:has-text("versikt planering"):visible').first
        await item.click()
        await human_delay(3000, 5000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await human_delay(1000, 2000)
        await random_mouse_wander(page)

    except Exception as e:
        logger.error("Kunde inte navigera till Översikt planering: %s", e)
        return {}, set()

    year = datetime.now().year
    try:
        period_text = await page.locator("text=/\\d{4}-\\d{2}-\\d{2}/").first.inner_text(timeout=3000)
        year_match = re.search(r"(\d{4})", period_text)
        if year_match:
            year = int(year_match.group(1))
    except Exception:
        pass

    # Extrahera namn, skifttext och färg per cell via JavaScript
    raw = await page.evaluate("""() => {
        const views = document.querySelectorAll('.x-grid-view');
        if (views.length < 2) return null;

        const nameView = views[0];
        const shiftView = views[1];
        const nameTbls = nameView.querySelectorAll('table');
        const shiftTbls = shiftView.querySelectorAll('table');

        const headerEls = document.querySelectorAll('.x-column-header-text');
        const headers = [];
        for (const h of headerEls) {
            const t = h.innerText.trim();
            if (t.match(/(\\d{1,2})\\/(\\d{1,2})/)) headers.push(t);
        }

        function parseName(raw) {
            const lines = raw.split('\\n').map(l => l.trim()).filter(l => l);
            const person = lines.filter(l => !l.match(/Avdelningar/));
            return person.length > 0 ? person[person.length - 1] : '';
        }

        const rows = [];
        const count = Math.min(nameTbls.length, shiftTbls.length);
        for (let i = 0; i < count; i++) {
            const name = parseName(nameTbls[i].innerText.trim());
            if (!name) continue;

            const cells = shiftTbls[i].querySelectorAll('td');
            const cellData = [];
            for (const c of cells) {
                const text = c.innerText.trim();

                // Hämta alla färger (kan finnas flera rader i en cell)
                const colors = [];
                c.querySelectorAll('[style*="color"]').forEach(el => {
                    const s = el.getAttribute('style') || '';
                    const m = s.match(/color\\s*:\\s*([^;"]+)/);
                    if (m) colors.push(m[1].trim().toUpperCase());
                });

                cellData.push({text, colors});
            }
            rows.push({name, cells: cellData});
        }

        return {headers, rows};
    }""")

    if not raw or not raw.get("rows"):
        logger.warning("Kunde inte extrahera kollegor")
        return {}, set()

    headers = raw["headers"]
    rows = raw["rows"]
    logger.info("Översikt: %d kollegor, %d dagar", len(rows), len(headers))

    coworker_map: dict[str, list[dict]] = {}
    tackare_dates: set[str] = set()

    for row in rows:
        name = row["name"]
        is_self = "jonny" in name.lower() and "nilsen" in name.lower()

        for col_idx, cell in enumerate(row["cells"]):
            if col_idx >= len(headers):
                break
            text = cell["text"]
            colors = set(cell.get("colors", []))

            if not text or text == "Ledig":
                continue
            if not re.search(r"\d{1,2}:\d{2}", text):
                continue

            date_str = _header_to_date(headers[col_idx], year)
            if not date_str:
                continue

            if is_self:
                if "ckare" in text:
                    tackare_dates.add(date_str)
                continue

            status, note = _classify_cell(text, colors)

            coworker_map.setdefault(date_str, []).append({
                "name": name,
                "status": status,
                "note": note,
            })

    logger.info("Täckare-dagar: %s", sorted(tackare_dates))
    return coworker_map, tackare_dates


def _classify_cell(text: str, colors: set[str]) -> tuple[str, str]:
    """Klassificera en cell baserat på färg, typkod och annotations.

    Returnerar (status, note) där status är 'working'|'absent'|'covering'.
    """
    # Extrahera typkod (Ar, Fr, FL, Sj, Se, Tj, Vb, Ka, Ej)
    type_match = re.search(r"\b(Ar|Fr|FL|Se|Sj|Tj|Vb|Ka|Ej)\b", text)
    type_code = type_match.group(1) if type_match else ""

    # Extrahera annotations (Täckare, Vak, Annan vån, Möte/APT)
    annotation = ""
    for pattern, label in ANNOTATION_PATTERNS.items():
        if pattern in text:
            annotation = label
            break

    # 1. Kolla färg först — den är mest tillförlitlig
    has_absent_color = bool(colors & COLOR_ABSENT)
    has_extra_color = bool(colors & COLOR_EXTRA)

    # 2. Frånvaro: röd/mörkröd färg ELLER känd frånvaro-typkod utan extratid-färg
    if has_absent_color or (type_code in ABSENT_TYPES and not has_extra_color):
        reason = TYPE_DESCRIPTIONS.get(type_code, "Frånvarande")
        return "absent", reason

    # 3. Extratid: rosa/magenta färg (utan Täckare-annotation)
    if has_extra_color and annotation != "Täckare":
        return "covering", "Extratid"

    # 4. Täckare = jobbar på Kaninholmen & Kiaholmen
    if annotation == "Täckare":
        return "working", "Kaninholmen & Kiaholmen"

    # 5. Ka = Kan arbeta (tillgänglig, inte schemalagd)
    if type_code == "Ka":
        return "kan_arbeta", ""

    # 6. Allt annat = jobbar normalt (Rådmansholmen)
    note = annotation if annotation else ""
    return "working", note


def _header_to_date(header: str, year: int) -> str | None:
    """Konvertera header-text (t.ex. '31/3 tis') till 'YYYY-MM-DD'."""
    m = re.search(r"(\d{1,2})/(\d{1,2})", header)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    try:
        datetime(year, month, day)  # Validera att datumet är giltigt
        return f"{year}-{month:02d}-{day:02d}"
    except ValueError:
        return None


async def _extract_medvind_calendar(page: Page) -> list[dict]:
    """Extrahera pass från Medvinds .mv-daycell-struktur med färginfo."""
    shifts = []

    year = datetime.now().year
    try:
        period_text = await page.locator(".x-toolbar").first.inner_text(timeout=3000)
        year_match = re.search(r"(\d{4})", period_text)
        if year_match:
            year = int(year_match.group(1))
    except Exception:
        pass

    cells = page.locator(".mv-daycell")
    count = await cells.count()
    logger.info("Hittade %d dagceller i kalendern", count)

    for i in range(count):
        cell = cells.nth(i)
        try:
            header = await cell.locator(".mv-dayheader").first.inner_text(timeout=1000)
            header = header.strip()

            time_el = cell.locator(".mv-time-text")
            if await time_el.count() == 0:
                continue

            time_text = (await time_el.first.inner_text(timeout=1000)).strip()
            if not time_text or time_text.lower() == "ledig":
                continue

            # Typkod (Ar, FL, etc.)
            shift_type = ""
            try:
                shift_type = (
                    await cell.locator(".mv-tidtyp-text").first.inner_text(timeout=1000)
                ).strip()
            except Exception:
                pass

            # Färg från .mv-cellrow style
            color = ""
            try:
                color = await cell.locator(".mv-cellrow[style*='color']").first.get_attribute(
                    "style", timeout=1000
                )
                cm = re.search(r"color\s*:\s*([^;\"]+)", color or "")
                color = cm.group(1).strip().upper() if cm else ""
            except Exception:
                pass

            # Parsa datum
            date_match = re.match(r"(\d{1,2})/(\d{1,2})", header)
            if not date_match:
                continue

            day = int(date_match.group(1))
            month = int(date_match.group(2))
            date_str = f"{year}-{month:02d}-{day:02d}"

            # Parsa tider
            time_match = re.match(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", time_text)
            start_time = time_match.group(1) if time_match else time_text
            end_time = time_match.group(2) if time_match else ""

            # Klassificera eget pass
            type_desc = TYPE_DESCRIPTIONS.get(shift_type, shift_type)
            if color in COLOR_ABSENT or shift_type in ABSENT_TYPES:
                type_desc = TYPE_DESCRIPTIONS.get(shift_type, "Frånvaro")
            elif color in COLOR_EXTRA:
                type_desc = "Extratid"

            shifts.append({
                "date": date_str,
                "start_time": start_time,
                "end_time": end_time,
                "shift_type": shift_type,
                "shift_description": type_desc,
                "location": "",  # Sätts i steg 2 via Översikt planering
                "coworkers": [],
                "covering": [],
                "absent": [],
                "kan_arbeta": [],
                "sjuka": [],
                "rapport_from": [],
                "rapport_to": [],
                "raw_text": f"{header} {time_text} {shift_type}".strip(),
                "scraped_at": datetime.now().isoformat(),
            })

        except Exception as e:
            logger.debug("Kunde inte parsa cell %d: %s", i, e)
            continue

    logger.info("Extraherade %d pass", len(shifts))
    return shifts


# ── Rapport-scraping (kväll ~21:00 + morgon ~07:00) ──────────────────

async def _scrape_rapport(page: Page, shifts: list[dict]) -> None:
    """Hämta dagpersonal för rapport: kväll (slutar ~21) och morgon (börjar ~07).

    Navigerar via Välj organisation till rätt avdelning och extraherar:
    - rapport_from: vem som slutar ~21:00 samma kväll (lämnar rapport till dig)
    - rapport_to: vem som börjar ~07:00 morgonen efter (du lämnar rapport till)
    """
    from datetime import timedelta

    DEPT_KEYWORDS = {
        "Rådmansholmen": "dmansholmen",
        "Kaninholmen & Kiaholmen": ["Kaninholmen", "Kiaholmen"],
    }

    # Beräkna morgondatum per pass (nattpass 20:45→07:15 = nästa dag)
    next_day_map = {}
    for shift in shifts:
        d = datetime.strptime(shift["date"], "%Y-%m-%d")
        next_day_map[shift["date"]] = (d + timedelta(days=1)).strftime("%Y-%m-%d")

    locations_needed = set()
    for shift in shifts:
        locations_needed.add(shift.get("location", "Rådmansholmen"))

    try:
        for location in locations_needed:
            keywords = DEPT_KEYWORDS.get(location, "dmansholmen")
            if isinstance(keywords, str):
                keywords = [keywords]

            # Om flera avdelningar (Kanin + Kia), tagga med avdelningsnamn
            tag_dept = len(keywords) > 1

            for keyword in keywords:
                evening_map, morning_map, ka_map, sjuka_map = (
                    await _select_dept_and_extract(page, keyword)
                )

                # Kort avdelningsnamn för taggning
                dept_label = keyword.split(",")[0].strip() if tag_dept else ""

                for shift in shifts:
                    if shift.get("location") != location:
                        continue

                    morning_date = next_day_map[shift["date"]]

                    # Kväll: slutar 21:00 samma dag
                    new_from = evening_map.get(shift["date"], [])
                    if dept_label and new_from:
                        new_from = [f"{n} ({dept_label})" for n in new_from]
                    shift["rapport_from"] = shift.get("rapport_from", []) + new_from

                    # Morgon: börjar 07:00 dagen efter
                    new_to = morning_map.get(morning_date, [])
                    if dept_label and new_to:
                        new_to = [f"{n} ({dept_label})" for n in new_to]
                    shift["rapport_to"] = shift.get("rapport_to", []) + new_to

                    # Kan arbeta
                    new_ka = ka_map.get(shift["date"], [])
                    shift["kan_arbeta"] = shift.get("kan_arbeta", []) + new_ka

                    # Sjuka — kväll (samma dag) + morgon (nästa dag)
                    sjuka_kväll = sjuka_map.get(shift["date"], [])
                    sjuka_morgon = sjuka_map.get(morning_date, [])
                    all_sjuka = list(set(sjuka_kväll + sjuka_morgon))
                    if dept_label and all_sjuka:
                        all_sjuka = [f"{n} ({dept_label})" for n in all_sjuka]
                    shift["sjuka"] = shift.get("sjuka", []) + all_sjuka

        # Byt tillbaka till Natt-vyn
        await _select_dept_via_org(page, "Natt")

    except Exception as e:
        logger.error("Kunde inte hämta rapport-info: %s", e, exc_info=True)

    for shift in shifts:
        shift.setdefault("rapport_from", [])
        shift.setdefault("rapport_to", [])
        shift.setdefault("sjuka", [])


async def _select_dept_via_org(page: Page, keyword: str) -> bool:
    """Öppna Välj organisation, expandera trädet, och välj en avdelning."""
    try:
        # Klicka dropdown-knappen (texten ändras beroende på aktuell vy)
        dept_btn = page.locator(
            'a.x-btn.x-btn-mv-button-large.x-badge'
        ).first
        await dept_btn.click()
        await human_delay(1000, 2000)

        # Välj organisation
        await page.wait_for_selector('.x-menu-item:visible', timeout=5000)
        org_item = page.locator(
            '.x-menu-item:has-text("lj organisation"):visible'
        ).first
        await org_item.click()
        await human_delay(3000, 5000)

        # Expandera rotnoden (Äldreomsorg...)
        expanders = page.locator('.x-tree-expander')
        await expanders.first.click()
        await human_delay(1000, 2000)

        # Expandera 8521 Avdelningar
        avd_count = await expanders.count()
        if avd_count > 1:
            await expanders.nth(1).click()
            await human_delay(1000, 2000)

        # Klicka på rätt avdelning
        target = page.locator(f'.x-tree-node-text:has-text("{keyword}")').first
        await target.click()
        await human_delay(500, 1000)

        # Klicka Välj-knappen
        valj_btn = page.locator('.x-window a.x-btn:has-text("lj")').first
        await valj_btn.click()
        await human_delay(3000, 5000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await human_delay(1000, 2000)

        return True

    except Exception as e:
        logger.error("Kunde inte välja avdelning '%s': %s", keyword, e)
        # Stäng eventuell öppen dialog
        try:
            await page.locator('.x-window a.x-btn:has-text("ng")').first.click()
            await human_delay(500, 1000)
        except Exception:
            pass
        return False


async def _select_dept_and_extract(
    page: Page, keyword: str
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Välj avdelning och extrahera kväll-, morgon-, Ka- och sjukpersonal."""
    if not await _select_dept_via_org(page, keyword):
        return {}, {}, {}, {}

    evening_map, morning_map, ka_map, sjuka_map = await _extract_dag_workers(page)
    logger.info("Rapport %s — kväll: %d, morgon: %d, ka: %d, sjuka: %d",
                keyword, len(evening_map), len(morning_map), len(ka_map), len(sjuka_map))
    return evening_map, morning_map, ka_map, sjuka_map


async def _extract_dag_workers(
    page: Page,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Extrahera dagpersonal: kväll 21, morgon 07, kan arbeta, sjuka."""
    year = datetime.now().year
    try:
        period_text = await page.locator("text=/\\d{4}-\\d{2}-\\d{2}/").first.inner_text(timeout=3000)
        year_match = re.search(r"(\d{4})", period_text)
        if year_match:
            year = int(year_match.group(1))
    except Exception:
        pass

    raw = await page.evaluate("""() => {
        const views = document.querySelectorAll('.x-grid-view');
        if (views.length < 2) return null;

        const nameView = views[0];
        const shiftView = views[1];
        const nameTbls = nameView.querySelectorAll('table');
        const shiftTbls = shiftView.querySelectorAll('table');

        const headerEls = document.querySelectorAll('.x-column-header-text');
        const headers = [];
        for (const h of headerEls) {
            const t = h.innerText.trim();
            if (t.match(/(\\d{1,2})\\/(\\d{1,2})/)) headers.push(t);
        }

        function parseName(raw) {
            const lines = raw.split('\\n').map(l => l.trim()).filter(l => l);
            const person = lines.filter(l => !l.match(/Avdelningar/));
            return person.length > 0 ? person[person.length - 1] : '';
        }

        const rows = [];
        const count = Math.min(nameTbls.length, shiftTbls.length);
        for (let i = 0; i < count; i++) {
            const name = parseName(nameTbls[i].innerText.trim());
            if (!name) continue;

            const cells = shiftTbls[i].querySelectorAll('td');
            const cellData = [];
            for (const c of cells) {
                cellData.push(c.innerText.trim());
            }
            rows.push({name, cells: cellData});
        }

        return {headers, rows};
    }""")

    if not raw or not raw.get("rows"):
        logger.warning("Kunde inte extrahera dagpersonal")
        return {}, {}, {}, {}

    headers = raw["headers"]
    evening: dict[str, list[str]] = {}     # Slutar 21:00
    morning: dict[str, list[str]] = {}     # Börjar 07:00
    kan_arbeta: dict[str, list[str]] = {}  # Ka (kan jobba)
    sjuka: dict[str, list[str]] = {}       # Fr/Sj (sjuk/frånvaro)

    for row in raw["rows"]:
        name = row["name"]
        if "jonny" in name.lower() and "nilsen" in name.lower():
            continue

        for col_idx, text in enumerate(row["cells"]):
            if col_idx >= len(headers):
                break

            if not text or text == "Ledig":
                continue

            date_str = _header_to_date(headers[col_idx], year)
            if not date_str:
                continue

            # Ka (Kan arbeta) — samla separat
            if re.search(r"\d[:]\d{2}Ka", text):
                kan_arbeta.setdefault(date_str, []).append(name)
                continue

            # Sjuk/Frånvaro (Fr, Sj, FL, Se, Tj)
            if re.search(r"\d[:]\d{2}(Fr|Sj|FL|Se|Tj)", text):
                sjuka.setdefault(date_str, []).append(name)
                continue

            # Matcha alla tidsintervall i cellen
            for m in re.finditer(r'(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})', text):
                start_h, start_m = int(m.group(1)), int(m.group(2))
                end_h, end_m = int(m.group(3)), int(m.group(4))

                # Kväll: slutar exakt 21:00
                if end_h == 21 and end_m == 0:
                    evening.setdefault(date_str, []).append(name)

                # Morgon: börjar exakt 07:00
                if start_h == 7 and start_m == 0:
                    morning.setdefault(date_str, []).append(name)

    logger.info("Kväll: %d, Morgon: %d, Ka: %d, Sjuka: %d dagar",
                len(evening), len(morning), len(kan_arbeta), len(sjuka))
    return evening, morning, kan_arbeta, sjuka


def _save_shifts(shifts: list[dict]):
    SHIFTS_FILE.parent.mkdir(exist_ok=True)
    SHIFTS_FILE.write_text(
        json.dumps(shifts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Sparade %d pass till %s", len(shifts), SHIFTS_FILE)
