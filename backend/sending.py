"""
Starting send jobs for initial emails and follow-ups.

Shared by the HTTP routes and the background scheduler (scheduled campaigns,
automatic follow-up sequences), so both go through the same checks:
credentials, resume, blocked domains, duplicate-job protection, send window.
"""

import os

import jobs
from common import utc_now_iso
from config import is_settings_complete, load_config
from constants import RESUME_PATH
from db import get_db, query_db
from email_sender import send_with_config

# SQL fragment: recipient's domain is not on the block list
NOT_BLOCKED_SQL = """LOWER(SUBSTR({col}, INSTR({col}, '@') + 1)) NOT IN (SELECT domain FROM blocked_domains)"""


class SendError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _check_prerequisites(config):
    if not is_settings_complete(config):
        raise SendError("Gmail and Groq credentials not configured. Go to Settings.")
    if not os.path.exists(RESUME_PATH):
        raise SendError("Resume PDF not found. Upload it in Profile.")


def start_campaign_send(campaign_id, config=None):
    """Start sending a campaign's generated drafts. Returns the number queued; raises SendError."""
    config = config or load_config()
    _check_prerequisites(config)

    def prepare():
        recipients = query_db(
            f"""
            SELECT * FROM recipients
            WHERE campaign_id = ?
              AND status = 'draft'
              AND subject IS NOT NULL
              AND {NOT_BLOCKED_SQL.format(col='email')}
            ORDER BY id
        """,
            (campaign_id,),
        )
        return (recipients, None) if recipients else (None, "No emails ready to send")

    delay = config.get("send_delay_seconds", 60)
    job, recipients, err = jobs.try_start_job(
        jobs.job_key("send", campaign_id),
        "send",
        jobs.new_send_data(delay),
        prepare,
    )
    if err == "running":
        raise SendError("This campaign is already being sent", 409)
    if err:
        raise SendError(err)

    def send_one(r):
        return send_with_config(config, r["email"], r["subject"], r["email_body"], RESUME_PATH)

    def on_sent(conn, r, message_id):
        conn.execute(
            "UPDATE recipients SET status = 'sent', sent_at = ?, message_id = ?, error_message = NULL WHERE id = ?",
            (utc_now_iso(), message_id, r["id"]),
        )

    def on_failed(conn, r, error):
        conn.execute("UPDATE recipients SET status = 'failed', error_message = ? WHERE id = ?", (error, r["id"]))

    def should_skip(conn, r):
        # The draft may have been sent, removed or blocked since the job was queued
        row = conn.execute("SELECT status FROM recipients WHERE id = ?", (r["id"],)).fetchone()
        if not row or row["status"] != "draft":
            return "no longer a draft"
        return None

    jobs.start_in_thread(
        jobs.run_send_job, job, recipients, "email", send_one, on_sent, on_failed, delay, config, should_skip
    )
    return len(recipients)


def thread_message_ids(conn, recipient_id):
    """Message-IDs already in this recipient's thread, oldest first (original, then follow-ups)."""
    ids = []
    row = conn.execute("SELECT message_id FROM recipients WHERE id = ?", (recipient_id,)).fetchone()
    if row and row["message_id"]:
        ids.append(row["message_id"])
    for f in conn.execute(
        "SELECT message_id FROM followups WHERE recipient_id = ? AND status = 'sent' AND message_id IS NOT NULL "
        "ORDER BY step, id",
        (recipient_id,),
    ):
        ids.append(f["message_id"])
    return ids


def start_followup_send(campaign_id, config=None, source=None):
    """
    Start sending a campaign's follow-up drafts (optionally only those from
    `source` 'manual'/'sequence'). Returns the number queued; raises SendError.
    """
    config = config or load_config()
    _check_prerequisites(config)

    def prepare():
        sql = f"""
            SELECT f.*, r.email as recipient_email, r.name as recipient_name, r.id as rid
            FROM followups f
            JOIN recipients r ON r.id = f.recipient_id
            WHERE r.campaign_id = ? AND f.status = 'draft'
              AND f.step = r.follow_up_sent + 1
              AND {NOT_BLOCKED_SQL.format(col='r.email')}
        """
        params = [campaign_id]
        if source:
            sql += " AND f.source = ?"
            params.append(source)
        followups = query_db(sql + " ORDER BY f.id", params)
        return (followups, None) if followups else (None, "No follow-ups ready to send")

    delay = config.get("send_delay_seconds", 60)
    job, followups, err = jobs.try_start_job(
        jobs.job_key("send_followup", campaign_id),
        "send_followup",
        jobs.new_send_data(delay),
        prepare,
    )
    if err == "running":
        raise SendError("Follow-ups for this campaign are already being sent", 409)
    if err:
        raise SendError(err)

    def send_one(f):
        conn = get_db()
        try:
            refs = thread_message_ids(conn, f["rid"])
        finally:
            conn.close()
        return send_with_config(
            config,
            f["recipient_email"],
            f["subject"],
            f["email_body"],
            RESUME_PATH,
            reply_to_message_id=refs[-1] if refs else None,
            references=refs,
        )

    def on_sent(conn, f, message_id):
        conn.execute(
            "UPDATE followups SET status = 'sent', sent_at = ?, message_id = ?, error_message = NULL WHERE id = ?",
            (utc_now_iso(), message_id, f["id"]),
        )
        conn.execute("UPDATE recipients SET follow_up_sent = follow_up_sent + 1 WHERE id = ?", (f["rid"],))

    def on_failed(conn, f, error):
        conn.execute("UPDATE followups SET status = 'failed', error_message = ? WHERE id = ?", (error, f["id"]))

    def should_skip(conn, f):
        """Re-check right before sending: the person may have replied or been excluded meanwhile."""
        r = conn.execute(
            "SELECT reply_status, replied_at, exclude_followup, follow_up_sent FROM recipients WHERE id = ?",
            (f["rid"],),
        ).fetchone()
        cur = conn.execute("SELECT status FROM followups WHERE id = ?", (f["id"],)).fetchone()
        reason = None
        if not r or not cur or cur["status"] != "draft":
            return "draft no longer exists"
        if r["follow_up_sent"] + 1 != f["step"]:
            reason = "an earlier follow-up is still unsent"
        elif r["exclude_followup"]:
            reason = "excluded from follow-ups"
        elif f["source"] == "sequence" and (r["replied_at"] or (r["reply_status"] or "no_reply") != "no_reply"):
            reason = "they replied — automatic follow-up cancelled"
        if reason and f["source"] == "sequence":
            conn.execute("UPDATE followups SET status = 'cancelled', error_message = ? WHERE id = ?", (reason, f["id"]))
        return reason

    jobs.start_in_thread(
        jobs.run_send_job, job, followups, "recipient_email", send_one, on_sent, on_failed, delay, config, should_skip
    )
    return len(followups)
