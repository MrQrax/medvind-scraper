"""Läser delad inställningsfil från medvind-web."""

import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent.parent / "medvind-web" / "server" / "settings.json"

DEFAULTS = {
    "scrape_interval_hours": 24,
    "check_interval_minutes": 60,
    "look_ahead_days": 30,
    "auto_klarmarkera": True,
    "klarmarkera_day": "sunday",
    "klarmarkera_time": "21:00",
    "notifications_enabled": True,
    "reminder_hours": [48, 24, 12, 6],
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULTS.copy()
