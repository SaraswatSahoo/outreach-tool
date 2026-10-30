import smtplib

import pytest

import email_sender as E
from tests.helpers import create_campaign, generate_and_send, wait_for


@pytest.fixture(autouse=True)
def fresh_network_state(monkeypatch):
    monkeypatch.setattr(E, "_net", {"port": E.STARTTLS_PORT, "ipv4_only": False, "dead_until": {}})
    monkeypatch.setattr(E, "_pause", lambda s: None)


class FakeServer:
    def __init__(self, port, ipv4_only, login_error=None, send_error=None):
        self.port, self.ipv4_only = port, ipv4_only
        self.login_error, self.send_error = login_error, send_error
        self.sent = []

    def login(self, user, password):
        if self.login_error:
            raise self.login_error

    def sendmail(self, frm, to, msg):
        if self.send_error:
            raise self.send_error
        self.sent.append(to)

    def quit(self):
        pass

    def close(self):
        pass


def scripted_open(monkeypatch, outcomes):
    """outcomes: function (port, ipv4_only, call_number) -> FakeServer or raises."""
    calls = []

    def fake_open(port, ipv4_only, timeout=E.CONNECT_TIMEOUT):
        calls.append((port, ipv4_only))
        return outcomes(port, ipv4_only, len(calls))

    monkeypatch.setattr(E, "_open", fake_open)
    return calls


def timeout_error():
    return TimeoutError("[WinError 10060] A connection attempt failed because the connected party did not respond")


def test_retries_then_succeeds_on_same_port(monkeypatch):
    def outcomes(port, ipv4, n):
        if n < 3:
            raise timeout_error()
        return FakeServer(port, ipv4)

    calls = scripted_open(monkeypatch, outcomes)
    server = E.connect_and_login("me@gmail.com", "pw")
    assert server.port == 587
    assert calls == [(587, False), (587, True), (587, True)]  # retries switch to IPv4
    assert E._net["ipv4_only"] is True  # learned: use IPv4 from now on


def test_falls_back_to_port_465_and_remembers(monkeypatch):
    def outcomes(port, ipv4, n):
        if port == 587:
            raise timeout_error()
        return FakeServer(port, ipv4)

    calls = scripted_open(monkeypatch, outcomes)
    assert E.connect_and_login("me@gmail.com", "pw").port == 465
    assert [c[0] for c in calls] == [587, 587, 587, 465]
    calls.clear()
    assert E.connect_and_login("me@gmail.com", "pw").port == 465
    assert calls == [(465, False)]  # 465 first now; blocked 587 skipped


def test_wrong_password_is_not_retried(monkeypatch):
    auth = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    calls = scripted_open(monkeypatch, lambda port, ipv4, n: FakeServer(port, ipv4, login_error=auth))
    with pytest.raises(smtplib.SMTPAuthenticationError):
        E.connect_and_login("me@gmail.com", "pw")
    assert len(calls) == 1


def test_unreachable_raises_plain_english_error(monkeypatch):
    def outcomes(port, ipv4, n):
        raise timeout_error()

    calls = scripted_open(monkeypatch, outcomes)
    with pytest.raises(E.GmailUnreachable, match="connection timed out.*Nothing was sent"):
        E.connect_and_login("me@gmail.com", "pw")
    assert len(calls) == 2 * E.ATTEMPTS_PER_PORT


def test_transmission_is_never_retried(monkeypatch):
    """Once sendmail starts, a failure must not trigger a resend (it could deliver twice)."""
    server = FakeServer(587, False, send_error=TimeoutError("timed out mid-send"))
    calls = scripted_open(monkeypatch, lambda port, ipv4, n: server)
    with pytest.raises(TimeoutError):
        E.send_email("me@gmail.com", "Me", "pw", "you@acme.com", "Hi", "Body", None)
    assert len(calls) == 1


def test_describe_connection_error():
    assert (
        E.describe_connection_error(OSError("[WinError 10060] A connection attempt failed")) == "connection timed out"
    )
    assert E.describe_connection_error(OSError("[WinError 10061] refused")) == "connection refused"
    assert "no internet" in E.describe_connection_error(OSError("[Errno -2] Name or service not known getaddrinfo"))


def test_job_keeps_unreachable_emails_as_drafts_and_stops(client, fake_ai, monkeypatch):
    import sending

    def unreachable(*a, **k):
        raise E.GmailUnreachable("Couldn't reach Gmail's mail server: connection timed out. Nothing was sent.")

    monkeypatch.setattr(sending, "send_with_config", unreachable)
    cid = create_campaign(client, "\n".join(f"u{i}@acme.com" for i in range(5)))
    done = generate_and_send(client, cid)

    assert done["status"] == "error"
    assert "3 times in a row" in done["stop_reason"]
    assert [entry["status"] for entry in done["log"]] == ["not_sent"] * 3  # stopped, didn't try the rest
    recipients = client.get(f"/api/campaign/{cid}").get_json()["recipients"]
    assert {r["status"] for r in recipients} == {"draft"}  # nothing marked failed — all can be sent later


def test_job_streak_resets_after_a_success(client, fake_ai, monkeypatch):
    import sending

    outcomes = iter(["down", "down", "ok", "down", "ok"])

    def flaky(config, recipient_email, *a, **k):
        if next(outcomes) == "down":
            raise E.GmailUnreachable("connection timed out. Nothing was sent.")
        return f"<{recipient_email}>"

    monkeypatch.setattr(sending, "send_with_config", flaky)
    cid = create_campaign(client, "\n".join(f"u{i}@acme.com" for i in range(5)))
    done = generate_and_send(client, cid)
    assert done["status"] == "complete"
    assert [e["status"] for e in done["log"]] == ["not_sent", "not_sent", "sent", "not_sent", "sent"]
    statuses = sorted(r["status"] for r in client.get(f"/api/campaign/{cid}").get_json()["recipients"])
    assert statuses == ["draft", "draft", "draft", "sent", "sent"]
    # the unsent ones are simply sent again
    monkeypatch.setattr(sending, "send_with_config", lambda config, to, *a, **k: f"<{to}>")
    client.post(f"/api/campaign/{cid}/send")
    wait_for(client, f"/api/campaign/{cid}/progress", "sending")
    assert {r["status"] for r in client.get(f"/api/campaign/{cid}").get_json()["recipients"]} == {"sent"}


def test_gmail_connection_test_endpoint(client, monkeypatch):
    import routes.settings as rs

    monkeypatch.setattr(
        rs.email_sender,
        "test_gmail_connection",
        lambda a, p: [
            {
                "port": 587,
                "connected": True,
                "logged_in": True,
                "attempts": 2,
                "seconds": 7.1,
                "message": "Connected and logged in",
            },
            {
                "port": 465,
                "connected": False,
                "logged_in": False,
                "attempts": 3,
                "seconds": None,
                "message": "connection timed out",
            },
        ],
    )
    data = client.post("/api/settings/test-gmail").get_json()
    assert data["ok"] is True and "slow or unstable" in data["summary"]

    monkeypatch.setattr(
        rs.email_sender,
        "test_gmail_connection",
        lambda a, p: [
            {
                "port": port,
                "connected": False,
                "logged_in": False,
                "attempts": 3,
                "seconds": None,
                "message": "connection timed out",
            }
            for port in (587, 465)
        ],
    )
    data = client.post("/api/settings/test-gmail").get_json()
    assert data["ok"] is False and "Can't reach Gmail" in data["summary"]
