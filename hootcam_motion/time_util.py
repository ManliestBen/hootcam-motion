"""
All timestamps in the app use US Central time (America/Chicago, CST/CDT).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

US_CENTRAL = ZoneInfo("America/Chicago")


def now_central() -> datetime:
    """Return current time in US Central (America/Chicago). Use for all event/file timestamps."""
    return datetime.now(US_CENTRAL)
