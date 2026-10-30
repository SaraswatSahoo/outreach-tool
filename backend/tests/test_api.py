import smtplib

import config
import jobs
from tests.helpers import create_campaign, generate_and_send, wait_for


def test_full_send_flow_and_double_send_guard(client, fake_ai, fake_smtp, monkeypatch):
    cid = create_campaign(client)
    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")

    # Hold the first send open so the second request races it
    import threading

    gate = threading.Event()
    real_run = jobs.run_send_job

    def slow_run(*args, **kwargs):
        gate.wait(2)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(jobs, "run_send_job", slow_run)
    first = client.post(f"/api/campaign/{cid}/send")
    second = client.post(f"/api/campaign/{cid}/send")
    gate.set()
    assert first.status_code == 200
    assert second.status_code == 409

    done = wait_for(client, f"/api/campaign/{cid}/progress", "sending")
    assert done["status"] == "complete"
    assert sorted(s["to"] for s in fake_smtp) == ["a@acme.com", "b@beta.io", "c@gamma.dev"]

    # Nothing left to send afterwards
    assert client.post(f"/api/campaign/{cid}/send").status_code == 400


def test_generate_only_fills_missing_drafts(client, fake_ai):
    cid = create_campaign(client)
    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    client.put(f"/api/campaign/{cid}/recipient/{rid}", json={"subject": "Edited by hand"})

    res = client.post(f"/api/campaign/{cid}/generate")
    assert res.status_code == 400  # nothing missing; the edit is not overwritten
    assert client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["subject"] == "Edited by hand"


def test_status_patch_is_partial(client, fake_ai, fake_smtp):
    cid = create_campaign(client)
    generate_and_send(client, cid)
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]

    client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "check_back"})
    client.patch(
        f"/api/recipient/{rid}/status", json={"reply_content": "Ping me in May", "check_back_date": "2026-05-01"}
    )
    r = client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "interested"}).get_json()["recipient"]
    assert r["reply_status"] == "interested"
    assert r["reply_content"] == "Ping me in May"
    assert r["check_back_date"] == "2026-05-01"

    r = client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "final_rejection"}).get_json()["recipient"]
    assert r["exclude_followup"] == 1


def test_followups_regenerate_replaces_drafts_and_skip_interested(client, fake_ai, fake_smtp):
    cid = create_campaign(client)
    generate_and_send(client, cid)
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "interested"})

    for _ in range(2):
        assert client.post(f"/api/campaign/{cid}/generate-followups", json={}).status_code == 200
        wait_for(client, f"/api/campaign/{cid}/generate-followup-progress", "generating")

    drafts = client.get(f"/api/campaign/{cid}/followup/preview").get_json()["followups"]
    assert len(drafts) == 2  # 3 recipients, 1 interested; regenerating didn't duplicate

    fake_smtp.clear()
    client.post(f"/api/campaign/{cid}/followup/send")
    wait_for(client, f"/api/campaign/{cid}/followup/progress", "sending")
    assert len(fake_smtp) == 2
    assert all(s["reply_to"] for s in fake_smtp)  # threaded replies


def test_auth_failure_aborts_and_retry_failed(client, fake_ai, monkeypatch):
    import sending

    calls = []

    def bad_login(*args, **kwargs):
        calls.append(1)
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(sending, "send_with_config", bad_login)
    cid = create_campaign(client)
    done = generate_and_send(client, cid)
    assert done["status"] == "error"
    assert len(calls) == 1
    statuses = {r["status"] for r in client.get(f"/api/campaign/{cid}").get_json()["recipients"]}
    assert statuses == {"draft"}  # nothing marked failed; all can be resent


def test_retry_failed_resets_to_draft(client, fake_ai, monkeypatch):
    import sending

    monkeypatch.setattr(
        sending, "send_with_config", lambda *a, **k: (_ for _ in ()).throw(smtplib.SMTPRecipientsRefused({}))
    )
    cid = create_campaign(client, "a@acme.com")
    generate_and_send(client, cid)
    assert client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["status"] == "failed"

    res = client.post(f"/api/campaign/{cid}/retry-failed").get_json()
    assert res["recipients_reset"] == 1
    assert client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["status"] == "draft"


def test_blocked_domains_skipped_at_creation(client):
    client.post("/api/blocked-domains", json={"domain": "beta.io"})
    res = client.post("/api/campaign/new", json={"name": "n", "goal": "g", "email_list": "a@acme.com\nb@beta.io"})
    body = res.get_json()
    assert body["recipients_count"] == 1
    assert body["skipped_blocked"] == ["b@beta.io"]
    assert client.post("/api/blocked-domains", json={"domain": "beta.io"}).status_code == 409


def test_duplicates_case_insensitive_and_only_sent(client, fake_ai, fake_smtp):
    cid = create_campaign(client, "Jane@Acme.com")
    dup = client.post("/api/campaign/check-duplicates", json={"emails": ["jane@acme.com"]}).get_json()
    assert dup["duplicate_count"] == 0  # drafts don't count as contacted

    generate_and_send(client, cid)
    dup = client.post("/api/campaign/check-duplicates", json={"emails": ["JANE@acme.com", "new@x.com"]}).get_json()
    assert [d["email"] for d in dup["duplicates"]] == ["jane@acme.com"]
    assert dup["new_emails"] == ["new@x.com"]


def test_settings_never_return_secrets(client):
    data = client.get("/api/settings").get_json()
    assert "gsk_test_key_1234" not in str(data)
    assert data["groq_keys"][0] == {"field": "groq_api_key", "configured": True, "hint": "…1234"}

    client.post("/api/settings", json={"groq_api_key_2": "gsk_second_key_9999"})
    assert config.load_config()["groq_api_key_2"] == "gsk_second_key_9999"
    client.post("/api/settings", json={"groq_api_key_2": ""})  # blank keeps it
    assert config.load_config()["groq_api_key_2"] == "gsk_second_key_9999"
    client.post("/api/settings", json={"clear": ["groq_api_key_2"]})
    assert config.load_config()["groq_api_key_2"] == ""
    assert client.post("/api/settings", json={"send_delay_seconds": "abc"}).status_code == 400


def test_search_validation_and_filter_only(client, fake_ai, fake_smtp):
    cid = create_campaign(client)
    generate_and_send(client, cid)
    assert client.get("/api/search?q=acme&page=abc").status_code == 400
    res = client.get("/api/search?status=no_reply&limit=1000").get_json()
    assert res["total"] == 3 and res["limit"] == 100


def test_interrupted_jobs_reported_after_restart(client):
    job, _, _ = jobs.try_start_job("send:999", "send", jobs.new_send_data(20, total=5))
    jobs._jobs.clear()  # simulate a process restart
    jobs.mark_interrupted_jobs()
    assert client.get("/api/campaign/999/progress").get_json()["status"] == "interrupted"


def test_dashboard_reminders(client, fake_ai, fake_smtp):
    cid = create_campaign(client, "a@acme.com")
    generate_and_send(client, cid)
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "check_back", "check_back_date": "2020-01-01"})
    reminders = client.get("/api/dashboard").get_json()["reminders"]
    assert [r["email"] for r in reminders] == ["a@acme.com"]


def test_unknown_route_returns_json_404(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404 and "error" in res.get_json()


def test_settings_models_round_trip(client):
    defaults = client.get("/api/settings").get_json()
    assert defaults["ai_model"] == "" and defaults["default_models"]["ai_model"] == "openai/gpt-oss-20b"
    assert client.post("/api/settings", json={"ai_model": "qwen/qwen3.8-27b"}).status_code == 200
    assert client.get("/api/settings").get_json()["ai_model"] == "qwen/qwen3.8-27b"
    assert client.post("/api/settings", json={"ai_model": "bad model id"}).status_code == 400


def test_generation_stops_when_no_model_available(client, monkeypatch):
    import llm
    import routes.campaigns as rc

    calls = []

    def no_models(**kw):
        calls.append(1)
        raise llm.NoModelAvailable("None of these Groq models are available")

    monkeypatch.setattr(rc, "generate_email", no_models)
    cid = create_campaign(client, "\n".join(f"u{i}@acme.com" for i in range(8)))
    client.post(f"/api/campaign/{cid}/generate")
    done = wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")
    assert done["status"] == "error"
    assert "available" in done["errors"][0]["error"]
    assert len(calls) < 8  # stopped early instead of failing every recipient
