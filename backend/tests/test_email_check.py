import config
import db
import email_check as EC
from tests.helpers import create_campaign, generate_and_send, wait_for

DNS = {
    "acme.com": EC.DOMAIN_OK,
    "gmail.com": EC.DOMAIN_OK,
    "gmial.com": EC.DOMAIN_OK,  # typo domains often exist (squatters) — must still be caught
    "gmaill.co": EC.DOMAIN_OK,
    "nosuchcompany-xyz.com": EC.DOMAIN_MISSING,
    "parked.com": EC.DOMAIN_NO_MAIL,
    "flaky.com": EC.DOMAIN_UNKNOWN,
}


def fake_dns(domain):
    return DNS.get(domain, EC.DOMAIN_OK)


def check(conn, *emails):
    return {r.email: r for r in EC.verify_emails(list(emails), {}, conn, domain_status=fake_dns)}


def test_format_problems():
    assert EC.format_problem("jane@acme.com") is None
    assert EC.format_problem("first.last+tag@sub.acme.co.uk") is None
    for bad in (
        "jane",
        "jane@",
        "@acme.com",
        "ja ne@acme.com",
        "jane..doe@acme.com",
        "jane@acme",
        "jane@-acme.com",
        "jane@acme.c0m",
        "a@b@acme.com",
    ):
        assert EC.format_problem(bad), bad


def test_typo_suggestions():
    assert EC.typo_suggestion("gmial.com") == ("gmail.com", True)
    assert EC.typo_suggestion("acme.con") == ("acme.com", True)
    assert EC.typo_suggestion("gmail.com") == (None, False)
    suggestion, certain = EC.typo_suggestion("gmaill.co")  # fuzzy: suggest but don't remove
    assert suggestion and not certain
    assert EC.typo_suggestion("stripe.com") == (None, False)
    assert EC.typo_suggestion("hotmail.co.uk") == (None, False)  # a real domain, not a typo


def test_builtin_verdicts(client):
    conn = db.get_db()
    r = check(
        conn,
        "jane@acme.com",
        "jane@gmial.com",
        "bob@nosuchcompany-xyz.com",
        "info@parked.com",
        "x@flaky.com",
        "hr@acme.com",
        "temp@mailinator.com",
        "noreply@acme.com",
        "not-an-email",
        "maybe@gmaill.co",
    )
    conn.close()
    assert r["jane@acme.com"].verdict == EC.VALID
    assert r["jane@gmial.com"].verdict == EC.INVALID and r["jane@gmial.com"].suggestion == "jane@gmail.com"
    assert (
        r["bob@nosuchcompany-xyz.com"].verdict == EC.INVALID
        and "doesn't exist" in r["bob@nosuchcompany-xyz.com"].reason
    )
    assert r["info@parked.com"].verdict == EC.INVALID and "can't receive" in r["info@parked.com"].reason
    assert r["x@flaky.com"].verdict == EC.RISKY  # DNS trouble never removes an address
    assert r["hr@acme.com"].verdict == EC.RISKY  # shared inbox: kept
    assert r["temp@mailinator.com"].verdict == EC.INVALID and "disposable" in r["temp@mailinator.com"].reason
    assert r["noreply@acme.com"].verdict == EC.INVALID
    assert r["not-an-email"].verdict == EC.INVALID
    assert r["maybe@gmaill.co"].verdict == EC.RISKY and r["maybe@gmaill.co"].suggestion


def test_history_and_block_list(client, fake_ai, fake_smtp):
    cid = create_campaign(client, "gone@acme.com")
    generate_and_send(client, cid)
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "invalid_email"})
    client.post("/api/blocked-domains", json={"domain": "blocked.io"})

    conn = db.get_db()
    r = check(conn, "Gone@acme.com", "x@blocked.io")
    conn.close()
    assert r["Gone@acme.com"].verdict == EC.INVALID and "bounced" in r["Gone@acme.com"].reason
    assert r["x@blocked.io"].verdict == EC.INVALID and "block list" in r["x@blocked.io"].reason


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses  # url substring -> (status, json)
        self.calls = []

    def __call__(self, url, params, timeout=60):
        self.calls.append((url, params.get("email")))
        for fragment, response in self.responses.items():
            if fragment in url:
                return response(params) if callable(response) else response
        raise AssertionError(f"unexpected URL {url}")


def test_provider_mappings():
    reoon = EC.PROVIDERS["reoon"]
    for status, verdict in [
        ("safe", EC.VALID),
        ("invalid", EC.INVALID),
        ("disabled", EC.INVALID),
        ("catch_all", EC.RISKY),
        ("unknown", EC.RISKY),
    ]:
        http = FakeHttp({"reoon.com/api/v1/verify": (200, {"status": status})})
        assert reoon.verify("a@acme.com", "k", http)[0] == verdict
        assert http.calls[0][0].endswith("/verify")

    hunter = EC.PROVIDERS["hunter"]
    pending = iter([(202, None), (200, {"data": {"status": "valid"}})])
    assert hunter.verify("a@acme.com", "k", lambda *a, **k: next(pending), wait=0)[0] == EC.VALID
    assert hunter.verify("a@acme.com", "k", lambda *a, **k: (200, {"data": {"status": "accept_all"}}))[0] == EC.RISKY

    zb = EC.PROVIDERS["zerobounce"]
    assert (
        zb.verify("a@acme.com", "k", lambda *a, **k: (200, {"status": "Invalid"}))[0] == EC.INVALID
    )  # case-insensitive
    assert zb.verify("a@acme.com", "k", lambda *a, **k: (200, {"status": "catch-all"}))[0] == EC.RISKY

    ab = EC.PROVIDERS["abstract"]
    assert ab.verify("a@acme.com", "k", lambda *a, **k: (200, {"deliverability": "UNDELIVERABLE"}))[0] == EC.INVALID


def test_provider_errors_are_risky_and_bad_key_stops_calls(client):
    cfg = {"verify_provider": "reoon", "verify_api_key": "k", "verify_with_service": True}
    http = FakeHttp({"reoon.com/api/v1/verify": (401, {"reason": "invalid key"})})
    conn = db.get_db()
    results = EC.verify_emails(["a@acme.com", "b@acme.com", "c@acme.com"], cfg, conn, domain_status=fake_dns, http=http)
    conn.close()
    assert all(r.verdict == EC.RISKY and "not checked" in r.reason for r in results)
    assert len(http.calls) == 1  # stopped after the key was rejected


def test_service_only_checks_survivors_and_caches(client):
    cfg = {"verify_provider": "reoon", "verify_api_key": "k", "verify_with_service": True}
    answers = {"jane@acme.com": "safe", "gone@acme.com": "invalid"}
    http = FakeHttp({"reoon.com/api/v1/verify": lambda p: (200, {"status": answers[p["email"]]})})
    conn = db.get_db()
    emails = ["jane@acme.com", "gone@acme.com", "typo@gmial.com"]
    first = {r.email: r for r in EC.verify_emails(emails, cfg, conn, domain_status=fake_dns, http=http)}
    assert first["jane@acme.com"].verdict == EC.VALID and first["jane@acme.com"].source == "reoon"
    assert first["gone@acme.com"].verdict == EC.INVALID
    assert [email for _, email in http.calls] == ["jane@acme.com", "gone@acme.com"]  # typo never sent to the service

    http.calls.clear()
    again = {r.email: r for r in EC.verify_emails(emails, cfg, conn, domain_status=fake_dns, http=http)}
    conn.close()
    assert http.calls == []  # served from the 30-day cache — no credits spent
    assert again["gone@acme.com"].verdict == EC.INVALID


def test_verify_endpoint_job(client, monkeypatch):
    monkeypatch.setattr(EC, "dns_domain_status", fake_dns)  # used inside the background job
    res = client.post("/api/emails/verify", json={"emails": ["jane@acme.com", "bad@gmial.com", "jane@acme.com"]})
    assert res.status_code == 200
    job = res.get_json()["job_id"]
    done = wait_for(client, f"/api/emails/verify/{job}", "checking")
    assert done["status"] == "complete"
    verdicts = {r["email"]: r["verdict"] for r in done["results"]}
    assert verdicts == {"jane@acme.com": "valid", "bad@gmial.com": "invalid"}  # duplicates collapsed
    assert client.post("/api/emails/verify", json={"emails": []}).status_code == 400
    assert client.get("/api/emails/verify/nope").status_code == 404


def test_verify_settings_and_credits(client, monkeypatch):
    assert client.post("/api/settings", json={"verify_provider": "nope"}).status_code == 400
    client.post("/api/settings", json={"verify_provider": "reoon", "verify_api_key": "reoon_secret_key_9876"})
    data = client.get("/api/settings").get_json()
    assert "reoon_secret_key_9876" not in str(data)
    assert data["verify_provider"] == "reoon" and data["has_verify_key"] and data["verify_key_hint"] == "…9876"
    assert config.load_config()["verify_api_key"] == "reoon_secret_key_9876"

    monkeypatch.setattr(
        EC, "http_get_json", lambda *a, **k: (200, {"remaining_daily_credits": 20, "remaining_instant_credits": 80})
    )
    assert client.get("/api/emails/credits").get_json() == {"provider": "Reoon", "credits": 100}

    client.post("/api/settings", json={"clear": ["verify_api_key"]})
    assert client.get("/api/emails/credits").get_json() == {"provider": None, "credits": None}


def test_brief_and_sample_endpoints(client, monkeypatch, fake_ai):
    import routes.campaigns as rc

    seen = {}

    def fake_brief(profile, resume_parsed, answers, api_key, models=None):
        seen["answers"] = answers
        return {
            "goal": f"Ask for a {answers['field']} {answers['purpose']}",
            "additional_context": "Available May-July",
        }

    monkeypatch.setattr(rc, "generate_brief", fake_brief)
    res = client.post("/api/campaign/brief", json={"purpose": "internship", "field": "ML", "evil": "ignored"})
    assert res.get_json() == {"goal": "Ask for a ML internship", "additional_context": "Available May-July"}
    assert "evil" not in seen["answers"]
    assert client.post("/api/campaign/brief", json={}).status_code == 400

    sample = client.post(
        "/api/campaign/sample-email",
        json={"goal": "Ask for an ML internship", "recipient": {"email": "jane@acme.com", "company": "Acme"}},
    ).get_json()
    assert sample["subject"] == "Hi jane@acme.com" and sample["email"] == "jane@acme.com"
    assert fake_ai["email"][-1]["recipient"]["company"] == "Acme"
    assert fake_ai["email"][-1]["campaign_goal"] == "Ask for an ML internship"
    assert client.post("/api/campaign/sample-email", json={"goal": ""}).status_code == 400

    # nothing was saved
    assert client.get("/api/campaigns").get_json() == []
