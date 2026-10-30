"""
Which Groq models to use, and small helpers shared by every AI call.

Groq retires models regularly (all Llama models were shut down for free and
developer accounts on 2026-08-16), so model IDs are configurable in Settings
and every call falls back through a list instead of depending on one name.
"""

import json

from groq import Groq

# Tried in order. gpt-oss-20b replaced llama-3.1-8b-instant; gpt-oss-120b replaced
# llama-3.3-70b-versatile. qwen3.8-27b is a Groq "preview" model, so it's last.
DEFAULT_TEXT_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
# Resume parsing is a one-off extraction task where accuracy matters more than speed
DEFAULT_RESUME_MODEL = "openai/gpt-oss-120b"

# Model families that aren't chat/text models (speech, TTS, guard classifiers)
_NON_TEXT_MARKERS = ("whisper", "orpheus", "prompt-guard", "safeguard", "allam")


def _dedupe(models):
    seen, result = set(), []
    for m in models:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def text_models(config):
    """Models for writing emails and classifying messages: configured first, then defaults."""
    return _dedupe([config.get("ai_model"), config.get("ai_fallback_model"), *DEFAULT_TEXT_MODELS])


def resume_models(config):
    return _dedupe([config.get("ai_resume_model") or DEFAULT_RESUME_MODEL, *text_models(config)])


def model_kwargs(model):
    """Extra request parameters per model family."""
    if "gpt-oss" in model:
        # Reasoning models: "low" keeps latency and token usage (= free-tier quota) down
        return {"reasoning_effort": "low"}
    return {}


def is_model_unavailable(err_str):
    """True for errors meaning the model is retired or not available to this key."""
    upper = err_str.upper()
    return any(k in upper for k in ("MODEL_NOT_FOUND", "DOES NOT EXIST", "DECOMMISSIONED", "MODEL_DECOMMISSIONED"))


class NoModelAvailable(Exception):
    """None of the configured models can be used with this API key."""


def unavailable_message(models):
    return (
        f"None of these Groq models are available to your API key: {', '.join(models)}. "
        "Groq may have retired them — pick a current model in Settings → AI Models."
    )


def json_chat(api_key, models, messages, temperature=0.1):
    """
    One JSON-mode chat completion, trying `models` in order and skipping any
    that are retired/unavailable. Returns the parsed JSON object.
    Other errors (auth, rate limit, bad JSON) are raised to the caller.
    """
    client = Groq(api_key=api_key)
    for model in models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
                **model_kwargs(model),
            )
        except Exception as e:
            if is_model_unavailable(str(e)):
                print(f"[AI] Model {model} unavailable, trying the next one")
                continue
            raise
        return json.loads(response.choices[0].message.content)
    raise NoModelAvailable(unavailable_message(models))


def list_text_models(api_key):
    """Active chat models this key can use, per Groq's /models endpoint."""
    models = Groq(api_key=api_key).models.list().data
    return sorted(
        m.id for m in models if getattr(m, "active", True) and not any(marker in m.id for marker in _NON_TEXT_MARKERS)
    )
