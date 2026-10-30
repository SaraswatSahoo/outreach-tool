import db
import inbox_monitor as IM

DB_EMAILS = {"jane@acme.com"}


def test_classify_bounce():
    body = "Delivery to the following recipient failed permanently:\n\n jane@acme.com\n\n550 5.1.1 user unknown"
    r = IM.classify_email_fast(
        "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
        "Delivery Status Notification (Failure)",
        body,
        DB_EMAILS,
    )
    assert r["type"] == "hard_bounce" and r["failed_email"] == "jane@acme.com"


def test_classify_bounce_for_unknown_address_is_irrelevant():
    r = IM.classify_email_fast(
        "mailer-daemon@googlemail.com", "Undeliverable", "stranger@x.com does not exist", DB_EMAILS
    )
    assert r["type"] == "irrelevant"


def test_classify_ooo():
    r = IM.classify_email_fast("Jane <jane@acme.com>", "Out of Office: back Monday", "I'm away", DB_EMAILS)
    assert r["type"] == "out_of_office" and r["ooo_sender_email"] == "jane@acme.com"


def _recipient(conn, status="sent", reply_status="no_reply"):
    cid = conn.execute("INSERT INTO campaigns (name, goal) VALUES ('c', 'g')").lastrowid
    rid = conn.execute(
        "INSERT INTO recipients (campaign_id, email, status, reply_status) VALUES (?, 'jane@acme.com', ?, ?)",
        (cid, status, reply_status),
    ).lastrowid
    conn.commit()
    return rid


def test_bounce_never_overrides_manual_status(client):
    conn = db.get_db()
    rid = _recipient(conn, reply_status="interested")
    assert IM.apply_bounce_update(conn, rid) is False
    assert conn.execute("SELECT reply_status FROM recipients WHERE id = ?", (rid,)).fetchone()[0] == "interested"
    conn.close()


def test_bounce_applies_once(client):
    conn = db.get_db()
    rid = _recipient(conn)
    assert IM.apply_bounce_update(conn, rid) is True
    assert IM.apply_bounce_update(conn, rid) is False
    conn.close()


def test_processed_messages_are_remembered(client):
    conn = db.get_db()
    IM.mark_message_processed(conn, "<abc@x>", "hard_bounce")
    IM.mark_message_processed(conn, "<abc@x>", "hard_bounce")
    assert IM.get_processed_message_ids(conn) == {"<abc@x>"}
    conn.close()
