from collections import defaultdict
from datetime import datetime, timedelta
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

# Rate limit: max 30 messages per 10 minutes per sender
_rate_buckets: dict[str, list[datetime]] = defaultdict(list)
MAX_MESSAGES = 30
WINDOW_MINUTES = 10


def _redact_number(number: str) -> str:
    """Redact phone number for safe logging: +3725100500 -> +372***0500"""
    if len(number) > 7:
        return number[:4] + "***" + number[-4:]
    return "***"


def is_authorized_sender(sender: str) -> bool:
    """Only the owner's number may send commands."""
    # Normalize: strip whitespace, ensure + prefix
    normalized_sender = sender.strip()
    normalized_owner = settings.signal_owner_number.strip()
    authorized = normalized_sender == normalized_owner
    if not authorized:
        logger.warning(f"Blocked unauthorized sender: {_redact_number(normalized_sender)}")
    return authorized


def is_within_rate_limit(sender: str) -> bool:
    """Prevent runaway loops or abuse."""
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=WINDOW_MINUTES)
    bucket = [t for t in _rate_buckets[sender] if t > cutoff]
    _rate_buckets[sender] = bucket
    if len(bucket) >= MAX_MESSAGES:
        logger.warning("Rate limit exceeded")
        return False
    _rate_buckets[sender].append(now)
    return True
