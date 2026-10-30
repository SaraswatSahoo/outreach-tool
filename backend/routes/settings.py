"""Credentials and sending settings."""

from flask import Blueprint, jsonify, request

import llm
import scheduling
from common import configured_groq_keys, json_error
from config import is_settings_complete, load_config, save_config
from constants import GROQ_KEY_FIELDS, SEND_DELAY_MAX, SEND_DELAY_MIN

bp = Blueprint("settings", __name__, url_prefix="/api")

SECRET_FIELDS = ("gmail_app_password",) + GROQ_KEY_FIELDS
MODEL_FIELDS = ("ai_model", "ai_fallback_model", "ai_resume_model")
SCHEDULE_FIELDS = (
    "send_window_enabled",
    "send_window_start",
    "send_window_end",
    "send_window_weekdays_only",
    "daily_send_cap",
    "warmup_enabled",
    "warmup_start_per_day",
    "warmup_increase_per_day",
    "auto_resume_sends",
)


def _hint(secret):
    """Last four characters, so the user can tell which key is stored without exposing it."""
    return f"…{secret[-4:]}" if secret and len(secret) > 8 else ""


@bp.route("/settings", methods=["GET"])
def get_settings():
    config = load_config()
    # Secrets are never sent back to the browser — only whether they are set
    return jsonify(
        {
            "gmail_address": config.get("gmail_address", ""),
            "has_gmail_password": bool(config.get("gmail_app_password", "")),
            "has_groq_key": bool(config.get("groq_api_key", "")),
            "groq_keys": [
                {"field": field, "configured": bool(config.get(field, "")), "hint": _hint(config.get(field, ""))}
                for field in GROQ_KEY_FIELDS
            ],
            "send_delay_seconds": config.get("send_delay_seconds", 60),
            **{field: config.get(field) for field in SCHEDULE_FIELDS},
            "send_delay_min": SEND_DELAY_MIN,
            "send_delay_max": SEND_DELAY_MAX,
            "warmup_cap_today": scheduling.warmup_cap(config, scheduling.now_local().date()),
            **{field: config.get(field) or "" for field in MODEL_FIELDS},
            "default_models": {
                "ai_model": llm.DEFAULT_TEXT_MODELS[0],
                "ai_fallback_model": llm.DEFAULT_TEXT_MODELS[1],
                "ai_resume_model": llm.DEFAULT_RESUME_MODEL,
            },
            "is_complete": is_settings_complete(config),
        }
    )


@bp.route("/settings", methods=["POST"])
def save_settings():
    data = request.json
    if not data:
        return json_error("No data provided")

    config = load_config()

    if "gmail_address" in data:
        config["gmail_address"] = (data["gmail_address"] or "").strip()

    # Blank secret fields mean "keep the stored value"; clearing is explicit.
    for field in SECRET_FIELDS:
        value = (data.get(field) or "").strip()
        if value:
            config[field] = value
    for field in data.get("clear", []):
        if field in SECRET_FIELDS:
            config[field] = ""

    if "send_delay_seconds" in data:
        try:
            delay = int(data["send_delay_seconds"])
        except (TypeError, ValueError):
            return json_error("send_delay_seconds must be a number")
        config["send_delay_seconds"] = max(SEND_DELAY_MIN, min(SEND_DELAY_MAX, delay))

    for field in MODEL_FIELDS:
        if field in data:
            value = (data[field] or "").strip()
            if len(value) > 100 or any(c.isspace() for c in value):
                return json_error(f"{field}: enter a model ID like openai/gpt-oss-20b")
            config[field] = value

    try:
        _apply_schedule_settings(config, data)
    except ValueError as e:
        return json_error(str(e))

    save_config(config)
    return jsonify({"message": "Settings saved successfully"})


def _apply_schedule_settings(config, data):
    for field in ("send_window_enabled", "send_window_weekdays_only", "auto_resume_sends"):
        if field in data:
            config[field] = bool(data[field])
    if "warmup_enabled" in data:
        enabling = bool(data["warmup_enabled"]) and not config.get("warmup_enabled")
        config["warmup_enabled"] = bool(data["warmup_enabled"])
        if enabling:  # the ramp starts over from today each time warm-up is switched on
            config["warmup_started_on"] = scheduling.now_local().date().isoformat()
    for field, lo, hi in (
        ("send_window_start", 0, 23),
        ("send_window_end", 1, 24),
        ("daily_send_cap", 0, 2000),
        ("warmup_start_per_day", 1, 500),
        ("warmup_increase_per_day", 0, 200),
    ):
        if field in data:
            try:
                value = int(data[field])
            except (TypeError, ValueError):
                raise ValueError(f"{field} must be a number") from None
            config[field] = max(lo, min(hi, value))
    if config["send_window_start"] >= config["send_window_end"]:
        raise ValueError("Sending hours: the start hour must be before the end hour")


@bp.route("/ai/models", methods=["GET"])
def available_models():
    """Chat models the configured Groq key can use right now (for the Settings picker)."""
    keys = configured_groq_keys(load_config())
    if not keys:
        return json_error("Add a Groq API key first")
    try:
        return jsonify({"models": llm.list_text_models(keys[0])})
    except Exception as e:
        return json_error(f"Couldn't fetch models from Groq: {e}", 502)
