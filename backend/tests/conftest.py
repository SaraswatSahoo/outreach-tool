"""
Test setup: point the app at a throwaway DB/config/upload dir *before* any
backend module is imported, and stub out network calls (Groq, SMTP).
"""

import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="outreach-test-")
os.environ["OUTREACH_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["OUTREACH_CONFIG_PATH"] = os.path.join(_tmp, "config.json")
os.environ["OUTREACH_UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
from constants import RESUME_PATH  # noqa: E402


@pytest.fixture
def app():
    from app import create_app

    return create_app()


@pytest.fixture
def client(app, monkeypatch):
    # Fresh data for every test
    conn = db.get_db()
    try:
        # campaigns cascades to recipients, follow-ups and replies
        for table in ("campaigns", "blocked_domains", "jobs", "processed_inbox_messages"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    jobs._jobs.clear()

    cfg = config.load_config()
    cfg.update(
        gmail_address="me@gmail.com",
        gmail_app_password="app-pass",
        groq_api_key="gsk_test_key_1234",
        groq_api_key_2="",
        groq_api_key_3="",
        send_delay_seconds=20,
        send_window_enabled=False,
        send_window_start=9,
        send_window_end=18,
        send_window_weekdays_only=True,
        daily_send_cap=0,
        warmup_enabled=False,
        warmup_started_on=None,
        auto_resume_sends=True,
    )
    cfg["profile"].update(name="Test User", college="Uni", branch="CS", year="3", skills="python", bio="bio")
    config.save_config(cfg)
    os.makedirs(os.path.dirname(RESUME_PATH), exist_ok=True)
    with open(RESUME_PATH, "wb") as f:
        f.write(b"%PDF-1.4 test")

    # Never sleep between sends in tests
    monkeypatch.setattr(jobs, "_sleep", lambda s: None)
    return app.test_client()


@pytest.fixture
def fake_ai(monkeypatch):
    """Deterministic AI output; records the kwargs each generator was called with."""
    calls = {"email": [], "followup": []}
    import followup_service
    import routes.campaigns as rc

    def fake_email(**kw):
        calls["email"].append(kw)
        return {"subject": f"Hi {kw['recipient']['email']}", "body": "Body"}

    def fake_followup(**kw):
        calls["followup"].append(kw)
        return {"subject": "Re: hi", "body": f"Following up #{kw.get('step', 1)}"}

    monkeypatch.setattr(rc, "generate_email", fake_email)
    monkeypatch.setattr(followup_service, "generate_contextual_followup", fake_followup)
    return calls


@pytest.fixture
def fake_smtp(monkeypatch):
    sent = []

    def fake_send(config, recipient_email, subject, body, resume_path, reply_to_message_id=None, references=None):
        sent.append(
            {
                "to": recipient_email,
                "subject": subject,
                "body": body,
                "reply_to": reply_to_message_id,
                "references": references,
            }
        )
        return f"<msg{len(sent)}@gmail.com>"

    import sending

    monkeypatch.setattr(sending, "send_with_config", fake_send)
    return sent
