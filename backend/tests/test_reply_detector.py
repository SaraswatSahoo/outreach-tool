import db
import inbox_monitor
import reply_detector as RD
from tests.fake_imap import FakeIMAP, make_message
from tests.helpers import create_campaign, generate_and_send

KNOWN = {"<abc@gmail.com>": 1}
SENT = {"jane@acme.com": 1}


def headers(**kw):
    import email

    return email.message_from_bytes(make_message(**kw))


def test_match_by_thread_header_even_from_other_address():
    h = headers(
        frm="Hiring <hr@acme.com>",
        to="me@gmail.com",
        subject="Re: hi",
        body="x",
        message_id="<r1@acme.com>",
        in_reply_to="<ABC@gmail.com>",
    )
    assert RD.match_reply(h, KNOWN, SENT, "me@gmail.com") == (1, "thread")


def test_match_by_sender_address():
    h = headers(frm="Jane <Jane@Acme.com>", to="me@gmail.com", subject="hello", body="x", message_id="<r2@acme.com>")
    assert RD.match_reply(h, KNOWN, SENT, "me@gmail.com") == (1, "sender")


def test_ignores_own_messages_strangers_and_auto_replies():
    own = headers(
        frm="me@gmail.com", to="jane@acme.com", subject="x", body="x", message_id="<a@x>", in_reply_to="<abc@gmail.com>"
    )
    stranger = headers(frm="bob@other.com", to="me@gmail.com", subject="x", body="x", message_id="<b@x>")
    auto = headers(
        frm="jane@acme.com",
        to="me@gmail.com",
        subject="Re: hi",
        body="x",
        message_id="<c@x>",
        extra_headers={"Auto-Submitted": "auto-replied"},
    )
    for h in (own, stranger, auto):
        assert RD.match_reply(h, KNOWN, SENT, "me@gmail.com") == (None, None)


def test_strip_quoted():
    text = "Thanks! Let's talk Thursday.\n\nOn Mon, Oct 5, 2026 at 10:00 AM Me <me@gmail.com> wrote:\n> Hi Jane,\n> ..."
    assert RD.strip_quoted(text) == "Thanks! Let's talk Thursday."
    assert RD.strip_quoted("> only quoted\nnew line") == "new line"


def _sent_recipient(client):
    cid = create_campaign(client, "jane@acme.com")
    generate_and_send(client, cid)
    return cid, client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]


def test_record_reply_updates_auto_status_once(client, fake_ai, fake_smtp):
    _, r = _sent_recipient(client)
    conn = db.get_db()
    cls = {"status": "check_back", "check_back_date": "2026-11-01", "summary": "Ping in November"}
    assert (
        RD.record_reply(
            conn, r["id"], "<r1@acme.com>", "jane@acme.com", "Re", "Try November", "2026-10-05T10:00:00+00:00", cls
        )
        == "new_status"
    )
    assert RD.record_reply(conn, r["id"], "<r1@acme.com>", "jane@acme.com", "Re", "Try November", None, cls) is None
    row = conn.execute("SELECT * FROM recipients WHERE id = ?", (r["id"],)).fetchone()
    assert (row["reply_status"], row["check_back_date"], row["reply_content"]) == (
        "check_back",
        "2026-11-01",
        "Try November",
    )
    assert row["replied_at"] == "2026-10-05T10:00:00+00:00"
    conn.close()


def test_record_reply_never_overrides_manual_status(client, fake_ai, fake_smtp):
    _, r = _sent_recipient(client)
    client.patch(f"/api/recipient/{r['id']}/status", json={"reply_status": "interested", "reply_content": "my notes"})
    conn = db.get_db()
    out = RD.record_reply(
        conn,
        r["id"],
        "<r9@acme.com>",
        "jane@acme.com",
        "Re",
        "No thanks",
        None,
        {"status": "final_rejection", "summary": "no"},
    )
    row = conn.execute(
        "SELECT reply_status, reply_content, replied_at FROM recipients WHERE id = ?", (r["id"],)
    ).fetchone()
    assert out == "logged"
    assert (row["reply_status"], row["reply_content"]) == ("interested", "my notes")
    assert row["replied_at"]  # still recorded, so sequences stop
    assert conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0] == 1
    conn.close()


def test_inbox_monitor_detects_reply_end_to_end(client, fake_ai, fake_smtp, monkeypatch):
    import config

    cid, r = _sent_recipient(client)
    our_id = r["message_id"]
    messages = [
        make_message(
            frm="Jane <jane@acme.com>",
            to="me@gmail.com",
            subject="Re: Hi",
            body="Sounds great, can you send your availability?\n\nOn Mon Me wrote:\n> old",
            message_id="<reply1@acme.com>",
            in_reply_to=our_id,
            references=our_id,
        ),
        make_message(frm="news@spam.com", to="me@gmail.com", subject="Deals", body="buy", message_id="<n@spam>"),
    ]
    fake = FakeIMAP(messages)
    monkeypatch.setattr(inbox_monitor, "get_imap_connection", lambda *a: fake)
    monkeypatch.setattr(
        RD,
        "classify_reply",
        lambda key, text, today=None, models=None: {
            "status": "interested",
            "check_back_date": None,
            "summary": "Asked for availability",
        },
    )

    conn = db.get_db()
    summary = inbox_monitor.run_inbox_monitor(config.load_config(), conn)
    assert summary["replies_detected"] == 1 and fake.logged_out
    again = inbox_monitor.run_inbox_monitor(config.load_config(), conn)
    assert again["replies_detected"] == 0  # processed once only
    conn.close()

    detail = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]
    assert detail["reply_status"] == "interested"
    assert detail["reply_content"] == "Sounds great, can you send your availability?"
    assert [rp["summary"] for rp in detail["replies"]] == ["Asked for availability"]
