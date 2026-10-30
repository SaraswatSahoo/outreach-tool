import pytest

import ai_generator as G
import llm

PRIMARY = llm.DEFAULT_TEXT_MODELS[0]


def test_validate_email_output():
    ok = {"subject": "Quick question", "body": "Hi Sam,\n\nShort note.\n\nBest,\nTest"}
    assert G.validate_email_output(ok, max_words=200) is None
    assert "placeholder" in G.validate_email_output({"subject": "Hi", "body": "Hi [Name], hello"}, 200)
    assert "placeholder" in G.validate_email_output({"subject": "Role at [Company Name]", "body": "x"}, 200)
    assert "banned" in G.validate_email_output({"subject": "s", "body": "I hope this email finds you well"}, 200)
    assert "too long" in G.validate_email_output({"subject": "s", "body": "word " * 300}, 200)
    assert "empty" in G.validate_email_output({"subject": "", "body": "x"}, 200)


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


def _fake_groq(responses):
    calls = []

    class FakeGroq:
        def __init__(self, api_key):
            self.chat = self
            self.completions = self

        def create(self, model, **kwargs):
            calls.append(model)
            r = responses.pop(0) if len(responses) > 1 else responses[0]
            if isinstance(r, Exception):
                raise r
            return _Resp(r)

    return FakeGroq, calls


def test_rate_limit_falls_through_models_then_raises(monkeypatch):
    fake, calls = _fake_groq([Exception("Error code: 429 rate_limit_exceeded")])
    monkeypatch.setattr(G, "Groq", fake)
    G._cooldowns.clear()
    with pytest.raises(G.RateLimitExhausted):
        G._call_groq("sys", "user", "key")
    assert calls == llm.DEFAULT_TEXT_MODELS
    G._cooldowns.clear()


def test_invalid_draft_is_regenerated(monkeypatch):
    bad = '{"subject": "Hi", "body": "Hi [Name], I hope this email finds you well"}'
    good = '{"subject": "Hi", "body": "Hi Sam, a short note."}'
    fake, calls = _fake_groq([bad, good])
    monkeypatch.setattr(G, "Groq", fake)
    result = G._call_groq("sys", "user", "key", validator=lambda r: G.validate_email_output(r, 200))
    assert result["body"] == "Hi Sam, a short note."
    assert len(calls) == 2


def test_invalid_draft_accepted_on_last_attempt(monkeypatch):
    bad = '{"subject": "Hi", "body": "Hi [Name]"}'
    fake, calls = _fake_groq([bad])
    monkeypatch.setattr(G, "Groq", fake)
    result = G._call_groq("sys", "user", "key", retries=2, validator=lambda r: G.validate_email_output(r, 200))
    assert result["body"] == "Hi [Name]"
    assert len(calls) == 2


def test_rate_limited_key_falls_back_to_other_key_same_model(monkeypatch):
    calls = []

    class KeyedGroq:
        def __init__(self, api_key):
            self.key = api_key
            self.chat = self
            self.completions = self

        def create(self, model, **kwargs):
            calls.append((self.key, model))
            if self.key == "key-limited":
                raise Exception("Error code: 429 - rate_limit_exceeded: tokens per minute")
            return _Resp('{"subject": "Hi", "body": "Hi Sam"}')

    monkeypatch.setattr(G, "Groq", KeyedGroq)
    G._cooldowns.clear()
    result = G._call_groq("sys", "user", "key-limited", fallback_keys=["key-limited", "key-ok"])
    assert result["body"] == "Hi Sam"
    assert calls == [("key-limited", PRIMARY), ("key-ok", PRIMARY)]  # same model, other key

    # The limited key is now cooling down: the next call goes straight to the good key
    calls.clear()
    G._call_groq("sys", "user", "key-limited", fallback_keys=["key-ok"])
    assert calls == [("key-ok", PRIMARY)]
    G._cooldowns.clear()


class _ModelAwareGroq:
    """Fake client: models in `retired` raise Groq's real 404 error text."""

    retired = set()
    calls = []

    def __init__(self, api_key):
        self.chat = self
        self.completions = self

    def create(self, model, **kwargs):
        type(self).calls.append((model, kwargs.get("reasoning_effort")))
        if model in self.retired:
            raise Exception(
                f"Error code: 404 - {{'error': {{'message': 'The model `{model}` does not exist or you do not have "
                "access to it.', 'type': 'invalid_request_error', 'code': 'model_not_found'}}"
            )
        return _Resp('{"subject": "Hi", "body": "Hi Sam", "type": "irrelevant"}')


def test_retired_model_falls_through_to_next(monkeypatch):
    _ModelAwareGroq.retired = {"llama-3.3-70b-versatile"}
    _ModelAwareGroq.calls = []
    monkeypatch.setattr(G, "Groq", _ModelAwareGroq)
    result = G._call_groq("sys", "user", "k", models=["llama-3.3-70b-versatile", "openai/gpt-oss-20b"])
    assert result["body"] == "Hi Sam"
    # gpt-oss gets low reasoning effort; other models get no extra parameter
    assert _ModelAwareGroq.calls == [("llama-3.3-70b-versatile", None), ("openai/gpt-oss-20b", "low")]


def test_all_models_retired_raises_clear_error(monkeypatch):
    _ModelAwareGroq.retired = {"old-a", "old-b"}
    monkeypatch.setattr(G, "Groq", _ModelAwareGroq)
    with pytest.raises(llm.NoModelAvailable, match="Settings"):
        G._call_groq("sys", "user", "k", models=["old-a", "old-b"])


def test_json_chat_skips_retired_models(monkeypatch):
    _ModelAwareGroq.retired = {"old-a"}
    _ModelAwareGroq.calls = []
    monkeypatch.setattr(llm, "Groq", _ModelAwareGroq)
    assert llm.json_chat("k", ["old-a", "openai/gpt-oss-120b"], [])["type"] == "irrelevant"
    with pytest.raises(llm.NoModelAvailable):
        llm.json_chat("k", ["old-a"], [])


def test_model_lists_put_configured_first_and_dedupe():
    cfg = {"ai_model": "qwen/qwen3.8-27b", "ai_fallback_model": "openai/gpt-oss-20b", "ai_resume_model": ""}
    assert llm.text_models(cfg) == ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    assert llm.resume_models(cfg)[0] == llm.DEFAULT_RESUME_MODEL
    assert llm.text_models({}) == llm.DEFAULT_TEXT_MODELS


def test_generate_brief_uses_answers_and_rejects_empty(monkeypatch):
    seen = {}

    def fake_json_chat(api_key, models, messages, temperature=0.1):
        seen["prompt"] = messages[1]["content"]
        seen["system"] = messages[0]["content"]
        return {"goal": "  Ask founders for an ML internship.  ", "additional_context": "Available June"}

    monkeypatch.setattr(llm, "json_chat", fake_json_chat)
    brief = G.generate_brief(
        {"name": "Sam", "skills": "python"},
        {"projects": [{"title": "Coeur AI"}]},
        {"purpose": "Internship", "field": "ML", "availability": "", "tone": "Friendly"},
        "key",
    )
    assert brief == {"goal": "Ask founders for an ML internship.", "additional_context": "Available June"}
    assert "Purpose: Internship" in seen["prompt"] and "Coeur AI" in seen["prompt"]
    assert "Availability" not in seen["prompt"]  # empty answers aren't sent
    assert "Never invent" in seen["system"]

    monkeypatch.setattr(llm, "json_chat", lambda *a, **k: {"goal": "", "additional_context": ""})
    with pytest.raises(ValueError):
        G.generate_brief({}, {}, {"purpose": "Internship"}, "key")


def test_email_prompt_forbids_invented_availability():
    assert "Only mention availability" in G.SYSTEM_PROMPT
