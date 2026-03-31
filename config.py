import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Medvind
MEDVIND_URL = "https://nhc.medvindwfm.se/MvWeb/"

# SSO
SSO_EMAIL = os.getenv("SSO_EMAIL", "")
SSO_PASSWORD = os.getenv("SSO_PASSWORD", "")

# E-post (valfritt)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", "")
EMAIL_ENABLED = all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, NOTIFY_EMAIL_TO])

# Schemaläggning
CHECK_INTERVAL_HOURS = 1
REMINDER_HOURS = [48, 24, 12, 6]  # Påminnelser innan pass (timmar)

# Sökvägar
DATA_DIR = Path(__file__).parent / "data"
SESSION_STATE_FILE = DATA_DIR / "session_state.json"
SHIFTS_FILE = DATA_DIR / "shifts.json"
NOTIFICATION_LOG = DATA_DIR / "notification_log.json"
LOG_FILE = DATA_DIR / "medvind_scraper.log"


def validate_config():
    if not SSO_EMAIL:
        raise ValueError("SSO_EMAIL saknas i .env")
    if not SSO_PASSWORD or SSO_PASSWORD == "DITT_LÖSENORD_HÄR":
        raise ValueError("SSO_PASSWORD måste sättas i .env")
    DATA_DIR.mkdir(exist_ok=True)
