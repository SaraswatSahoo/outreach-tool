"""
Small helpers shared by the route modules.
"""

import itertools
import threading
from datetime import UTC, datetime

from flask import jsonify, request

from constants import FOLLOWUP_INELIGIBLE_STATUSES, GROQ_KEY_FIELDS

# SQL fragment for `reply_status NOT IN (...)`; values are constants, not user input
FOLLOWUP_INELIGIBLE_SQL = ", ".join(f"'{s}'" for s in FOLLOWUP_INELIGIBLE_STATUSES)


def utc_now_iso():
    """Timezone-aware UTC timestamp (ISO 8601 with +00:00 offset)."""
    return datetime.now(UTC).isoformat()


def json_error(message, status=400):
    return jsonify({"error": message}), status


class BadRequest(Exception):
    """Raised by request helpers; converted to a 400 JSON response by the app."""


def arg_int(name, default, minimum=None, maximum=None):
    """Read an integer query-string argument, validating and clamping it."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be an integer") from None
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# ─── Groq key rotation ────────────────────────────────────────────────────────

_groq_key_cycle_lock = threading.Lock()


def configured_groq_keys(config):
    """All non-empty Groq keys in config (possibly empty)."""
    return [k for k in (config.get(field, "").strip() for field in GROQ_KEY_FIELDS) if k]


def get_groq_keys(config):
    """All non-empty Groq keys in config. Raises if none are configured."""
    keys = configured_groq_keys(config)
    if not keys:
        raise ValueError("No Groq API key configured. Go to Settings.")
    return keys


def get_groq_key_cycle(config):
    """Round-robin iterator over all configured Groq keys."""
    return itertools.cycle(get_groq_keys(config))


def get_next_groq_key(cycle):
    """Thread-safe: get next key from the round-robin cycle."""
    with _groq_key_cycle_lock:
        return next(cycle)
