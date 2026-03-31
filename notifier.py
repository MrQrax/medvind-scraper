import json
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from winotify import Notification, audio

from config import (
    NOTIFICATION_LOG,
    SHIFTS_FILE,
    REMINDER_HOURS,
    EMAIL_ENABLED,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    NOTIFY_EMAIL_TO,
)

logger = logging.getLogger("medvind.notifier")

# Fil för att spara förra scrapens data (för ändringsdetektering)
_PREV_SHIFTS_FILE = SHIFTS_FILE.parent / "shifts_prev.json"


# ── Sändning ────────────────────────────────────────────────────────

def send_toast(title: str, message: str):
    try:
        toast = Notification(app_id="Medvind Schema", title=title, msg=message)
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        logger.info("Toast: %s", title)
    except Exception as e:
        logger.error("Toast misslyckades: %s", e)


def send_email(subject: str, body: str):
    if not EMAIL_ENABLED:
        return
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = NOTIFY_EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("E-post: %s", subject)
    except Exception as e:
        logger.error("E-post misslyckades: %s", e)


def _notify(title: str, message: str):
    """Skicka notis via alla aktiva kanaler."""
    send_toast(title, message)
    send_email(f"Medvind: {title}", message)


# ── Huvudfunktion ───────────────────────────────────────────────────

def check_and_notify(shifts: list[dict]):
    """Kör alla notiskontroller: ändringar + påminnelser.

    Anropas varje timme med färska skiftdata.
    """
    _detect_changes(shifts)
    _check_reminders(shifts)

    # Spara nuvarande data som "förra" för nästa jämförelse
    _save_prev(shifts)


# ── Ändringsdetektering ────────────────────────────────────────────

def _detect_changes(shifts: list[dict]):
    """Jämför nya pass med förra scrapen. Meddela om ändringar."""
    prev = _load_prev()
    if not prev:
        # Första körningen — inget att jämföra med
        logger.info("Första körningen, sparar basdata")
        return

    prev_map = {s["date"]: s for s in prev}

    for shift in shifts:
        date = shift["date"]
        old = prev_map.get(date)
        if not old:
            # Nytt pass (fanns inte förra gången)
            desc = shift.get("shift_description", shift.get("shift_type", ""))
            _notify(
                f"Nytt pass: {date}",
                _format_shift_message(shift, f"Nytt pass upptäckt ({desc})"),
            )
            continue

        # Jämför kollegor, täckare, frånvarande
        changes = []

        new_absent = set(shift.get("absent", []))
        old_absent = set(old.get("absent", []))
        added_absent = new_absent - old_absent
        removed_absent = old_absent - new_absent

        new_covering = set(shift.get("covering", []))
        old_covering = set(old.get("covering", []))
        added_covering = new_covering - old_covering

        new_coworkers = set(shift.get("coworkers", []))
        old_coworkers = set(old.get("coworkers", []))
        added_coworkers = new_coworkers - old_coworkers
        removed_coworkers = old_coworkers - new_coworkers

        if added_absent:
            changes.append(f"Ny frånvaro: {', '.join(added_absent)}")
        if removed_absent:
            changes.append(f"Tillbaka: {', '.join(removed_absent)}")
        if added_covering:
            changes.append(f"Hoppar in: {', '.join(added_covering)}")
        if added_coworkers:
            changes.append(f"Ny kollega: {', '.join(added_coworkers)}")
        if removed_coworkers:
            changes.append(f"Borta: {', '.join(removed_coworkers)}")

        # Kolla om passets tid ändrats
        if old.get("start_time") != shift.get("start_time") or old.get("end_time") != shift.get("end_time"):
            changes.append(
                f"Tid ändrad: {old.get('start_time')}-{old.get('end_time')} → "
                f"{shift.get('start_time')}-{shift.get('end_time')}"
            )

        if changes:
            _notify(
                f"Ändring {date}",
                _format_shift_message(shift, "\n".join(changes)),
            )


# ── Påminnelser (48h, 24h, 12h, 6h) ──────────────────────────────

def _check_reminders(shifts: list[dict]):
    """Skicka påminnelser vid 48h, 24h, 12h och 6h före passstart."""
    log = _load_log()
    now = datetime.now()

    for shift in shifts:
        if not shift.get("start_time"):
            continue
        try:
            start = datetime.strptime(
                f"{shift['date']} {shift['start_time']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            continue

        hours_until = (start - now).total_seconds() / 3600
        if hours_until < 0:
            continue  # Redan passerat

        for threshold in REMINDER_HOURS:
            key = f"reminder_{shift['date']}_{shift['start_time']}_{threshold}h"
            if key in log:
                continue
            if hours_until <= threshold:
                label = _hours_label(threshold)
                _notify(
                    f"Pass om {label}",
                    _format_shift_message(shift, f"Ditt pass börjar om {label}"),
                )
                log[key] = now.isoformat()
                break  # Bara en påminnelse per pass per check

    _cleanup_old_entries(log)
    _save_log(log)


def _hours_label(h: int) -> str:
    if h >= 48:
        return f"{h // 24} dagar"
    return f"{h} timmar"


# ── Formatering ─────────────────────────────────────────────────────

def _format_shift_message(shift: dict, header: str) -> str:
    """Formatera ett komplett passmeddelande."""
    time_str = ""
    if shift.get("start_time") and shift.get("end_time"):
        time_str = f"kl {shift['start_time']}-{shift['end_time']}"

    desc = shift.get("shift_description", "")
    desc_str = f" ({desc})" if desc else ""

    lines = [f"{header}", f"{shift['date']} {time_str}{desc_str}"]

    coworkers = shift.get("coworkers", [])
    covering = shift.get("covering", [])
    absent = shift.get("absent", [])

    if coworkers:
        lines.append(f"Jobbar med: {', '.join(coworkers)}")
    if covering:
        lines.append(f"Hoppar in: {', '.join(covering)}")
    if absent:
        lines.append(f"Frånvarande: {', '.join(absent)}")

    return "\n".join(lines)


# ── Filhantering ────────────────────────────────────────────────────

def _load_prev() -> list[dict]:
    if not _PREV_SHIFTS_FILE.exists():
        return []
    try:
        return json.loads(_PREV_SHIFTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_prev(shifts: list[dict]):
    _PREV_SHIFTS_FILE.parent.mkdir(exist_ok=True)
    _PREV_SHIFTS_FILE.write_text(
        json.dumps(shifts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_log() -> dict:
    if not NOTIFICATION_LOG.exists():
        return {}
    try:
        return json.loads(NOTIFICATION_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_log(log: dict):
    NOTIFICATION_LOG.parent.mkdir(exist_ok=True)
    NOTIFICATION_LOG.write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _cleanup_old_entries(log: dict):
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    old_keys = [k for k, v in log.items() if v < cutoff]
    for k in old_keys:
        del log[k]
