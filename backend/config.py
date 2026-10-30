"""
Configuration management for the outreach tool.
Loads/saves config.json with defaults for profile, credentials, and settings.
"""

import copy
import json
import os
import tempfile

from constants import CONFIG_PATH, GROQ_KEY_FIELDS

DEFAULT_CONFIG = {
    "gmail_address": "",
    "gmail_app_password": "",
    "groq_api_key": "",
    "groq_api_key_2": "",
    "groq_api_key_3": "",
    "send_delay_seconds": 60,
    # Groq model IDs; empty = built-in defaults (see llm.py). Groq retires models
    # regularly, so these can be changed in Settings without touching code.
    "ai_model": "",
    "ai_fallback_model": "",
    "ai_resume_model": "",
    # Sending schedule (local time of the machine running the backend)
    "send_window_enabled": False,
    "send_window_start": 9,  # hour, inclusive
    "send_window_end": 18,  # hour, exclusive
    "send_window_weekdays_only": True,
    "daily_send_cap": 0,  # 0 = no cap (Gmail allows ~500/day)
    # Warm-up: start low and raise the daily limit each day, to build sender reputation
    "warmup_enabled": False,
    "warmup_start_per_day": 20,
    "warmup_increase_per_day": 10,
    "warmup_started_on": None,  # local date (YYYY-MM-DD) the ramp started
    # Restart sends that were cut off by a backend restart (within 24h)
    "auto_resume_sends": True,
    "profile": {
        "name": "",
        "college": "",
        "branch": "",
        "year": "",
        "cgpa": "",
        "skills": "",
        "github": "",
        "linkedin": "",
        "bio": "",
    },
    "resume_parsed": {},
}


def load_config():
    """Load config from disk. Create with defaults if missing."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    # Merge with defaults to handle missing keys after upgrades
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(config)
    if "profile" in config:
        merged_profile = DEFAULT_CONFIG["profile"].copy()
        merged_profile.update(config["profile"])
        merged["profile"] = merged_profile

    # Drop keys written by older versions
    merged.pop("resume_path", None)
    if isinstance(merged.get("resume_parsed"), dict):
        merged["resume_parsed"].pop("raw_text", None)

    return merged


def save_config(config):
    """Persist config to disk atomically (write to a temp file, then rename)."""
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except BaseException:
        os.unlink(tmp_path)
        raise


def is_profile_complete(config):
    """Check if the minimum profile fields are filled."""
    profile = config.get("profile", {})
    required = ["name", "college", "branch", "year", "skills", "bio"]
    return all(profile.get(field, "").strip() for field in required)


def is_settings_complete(config):
    """Check if Gmail + Groq credentials are configured."""
    return bool(
        config.get("gmail_address", "").strip()
        and config.get("gmail_app_password", "").strip()
        and any(config.get(f, "").strip() for f in GROQ_KEY_FIELDS)
    )
