"""
Background automation, run periodically by the scheduler (see routes/inbox.py):

- run_scheduled_sends(): start campaigns whose "send at" time has arrived.
- resume_interrupted_sends(): at startup, finish sends cut off by a restart.
- run_sequences(): for campaigns with an automatic follow-up sequence, draft
  the next follow-up for everyone who is due and hasn't replied, and send the
  drafts automatically if the campaign allows it.

Both are idempotent: running them twice in a row does nothing extra.
"""

from datetime import UTC, datetime, timedelta

from common import get_groq_keys, utc_now_iso
from config import load_config
from db import get_db
from followup_service import campaign_sequence, generate_followup, previous_followup_bodies, save_followup_draft
from sending import NOT_BLOCKED_SQL, SendError, start_campaign_send, start_followup_send


def _parse_utc(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ─── Scheduled sends ──────────────────────────────────────────────────────────


def run_scheduled_sends(now=None):
    now = now or datetime.now(UTC)
    conn = get_db()
    try:
        due = [
            row["id"]
            for row in conn.execute("SELECT id, scheduled_at FROM campaigns WHERE scheduled_at IS NOT NULL")
            if (_parse_utc(row["scheduled_at"]) or now) <= now
        ]
        for cid in due:
            conn.execute("UPDATE campaigns SET scheduled_at = NULL WHERE id = ?", (cid,))
        conn.commit()
    finally:
        conn.close()

    started = []
    config = load_config()
    for cid in due:
        try:
            start_campaign_send(cid, config)
            started.append(cid)
        except SendError as e:
            print(f"[Scheduler] Campaign {cid}: scheduled send not started: {e}")
    return started


# ─── Resuming interrupted sends ───────────────────────────────────────────────

RESUME_WITHIN = timedelta(hours=24)


def resume_interrupted_sends(now=None):
    """
    Called once when the scheduler starts: restart send jobs that were cut off
    by a backend restart (if they made progress within the last 24h and
    auto-resume is on). Unsent items are still drafts, so this just sends the rest.
    """
    import json

    config = load_config()
    if not config.get("auto_resume_sends", True):
        return []
    now = now or datetime.now(UTC)

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT key, kind, data FROM jobs WHERE status = 'interrupted' AND kind IN ('send', 'send_followup')"
        ).fetchall()
    finally:
        conn.close()

    resumed = []
    for row in rows:
        data = json.loads(row["data"])
        last = _parse_utc(data.get("last_activity_at"))
        if data.get("cancel_requested") or data.get("resume_attempted") or not last or now - last > RESUME_WITHIN:
            continue
        campaign_id = int(row["key"].split(":", 1)[1])
        try:
            if row["kind"] == "send":
                start_campaign_send(campaign_id, config)
            else:
                start_followup_send(campaign_id, config)
            resumed.append(row["key"])
            print(f"[Resume] Restarted interrupted job {row['key']}")
        except SendError as e:
            # Nothing left to send, credentials missing, ... — don't retry on every restart
            data["resume_attempted"] = True
            conn = get_db()
            try:
                conn.execute("UPDATE jobs SET data = ? WHERE key = ?", (json.dumps(data), row["key"]))
                conn.commit()
            finally:
                conn.close()
            print(f"[Resume] Not resuming {row['key']}: {e}")
    return resumed


# ─── Follow-up sequences ──────────────────────────────────────────────────────


def due_sequence_recipients(conn, now=None):
    """
    [(campaign, recipient, steps)] for recipients whose next automatic follow-up is due:
    sent, never replied, still "no reply", not excluded, steps remaining, no draft
    already waiting, and the step's delay has passed since the last email.
    """
    now = now or datetime.now(UTC)
    campaigns = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM campaigns WHERE sequence_enabled = 1")}
    if not campaigns:
        return []

    placeholders = ",".join("?" * len(campaigns))
    rows = conn.execute(
        f"""
        SELECT r.*,
               (SELECT MAX(f.sent_at) FROM followups f WHERE f.recipient_id = r.id AND f.status = 'sent') AS last_followup_at,
               (SELECT COUNT(*) FROM followups f WHERE f.recipient_id = r.id AND f.status = 'draft') AS pending_drafts
        FROM recipients r
        WHERE r.campaign_id IN ({placeholders})
          AND r.status = 'sent'
          AND r.replied_at IS NULL
          AND COALESCE(r.reply_status, 'no_reply') = 'no_reply'
          AND r.exclude_followup = 0
          AND {NOT_BLOCKED_SQL.format(col='r.email')}
    """,
        list(campaigns),
    ).fetchall()

    due = []
    for r in rows:
        r = dict(r)
        campaign = campaigns[r["campaign_id"]]
        steps = campaign_sequence(campaign)
        sent_count = r["follow_up_sent"] or 0
        if sent_count >= len(steps) or r["pending_drafts"]:
            continue
        last = _parse_utc(r["last_followup_at"]) or _parse_utc(r["sent_at"])
        if last and now - last >= timedelta(days=steps[sent_count]["delay_days"]):
            due.append((campaign, r, steps))
    return due


def run_sequences(now=None, generate=generate_followup):
    """Draft (and optionally send) due sequence follow-ups. Returns a summary."""
    config = load_config()
    summary = {"drafted": 0, "failed": 0, "sends_started": []}
    try:
        keys = get_groq_keys(config)
    except ValueError:
        return summary

    conn = get_db()
    try:
        due = due_sequence_recipients(conn, now)
        for i, (_campaign, recipient, steps) in enumerate(due):
            try:
                result = generate(
                    config, recipient, keys[i % len(keys)], previous_followup_bodies(conn, recipient["id"]), len(steps)
                )
            except Exception as e:
                summary["failed"] += 1
                print(f"[Sequences] Could not draft follow-up for {recipient['email']}: {e}")
                continue
            save_followup_draft(conn, recipient, result, source="sequence")
            conn.commit()
            summary["drafted"] += 1

        # Includes drafts left over from an earlier tick (e.g. a send job was busy then)
        auto_send = [
            row["id"]
            for row in conn.execute("""
            SELECT DISTINCT c.id FROM campaigns c
            JOIN recipients r ON r.campaign_id = c.id
            JOIN followups f ON f.recipient_id = r.id
            WHERE c.sequence_enabled = 1 AND c.auto_send_followups = 1
              AND f.status = 'draft' AND f.source = 'sequence'
        """)
        ]
    finally:
        conn.close()

    for cid in auto_send:
        try:
            start_followup_send(cid, config, source="sequence")
            summary["sends_started"].append(cid)
        except SendError as e:
            if e.status != 409:  # 409 = a follow-up send is already running; next tick retries
                print(f"[Sequences] Campaign {cid}: {e}")

    if summary["drafted"] or summary["failed"]:
        print(f"[Sequences] {utc_now_iso()} drafted={summary['drafted']} failed={summary['failed']}")
    return summary
