"""Campaign, recipient, initial-email generation and sending routes."""

import json
import os
import random
import time
from datetime import UTC, datetime

from flask import Blueprint, jsonify, request

import jobs
from ai_generator import generate_brief, generate_email
from common import (
    FOLLOWUP_INELIGIBLE_SQL,
    configured_groq_keys,
    get_groq_key_cycle,
    get_groq_keys,
    get_next_groq_key,
    json_error,
    utc_now_iso,
)
from config import load_config
from constants import AUTO_EXCLUDED_STATUSES, MAX_FOLLOWUPS, RESUME_PATH
from db import execute_db, get_db, query_db
from followup_service import campaign_sequence
from llm import text_models
from sending import SendError, start_campaign_send
from utils import EMAIL_REGEX, parse_email_list

bp = Blueprint("campaigns", __name__, url_prefix="/api")

RECIPIENT_FIELDS = ("name", "company", "role", "notes")


def _blocked_domains():
    return {row["domain"] for row in query_db("SELECT domain FROM blocked_domains")}


# ─── Campaigns ────────────────────────────────────────────────────────────────


@bp.route("/campaigns", methods=["GET"])
def list_campaigns():
    campaigns = query_db("""
        SELECT c.*,
               COUNT(r.id) as total_recipients,
               SUM(CASE WHEN r.status = 'sent' THEN 1 ELSE 0 END) as sent_count,
               SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) as failed_count,
               SUM(CASE WHEN r.status = 'draft' AND r.subject IS NOT NULL THEN 1 ELSE 0 END) as draft_count,
               (
                   SELECT COUNT(*) FROM followups f
                   JOIN recipients r2 ON f.recipient_id = r2.id
                   WHERE r2.campaign_id = c.id AND f.status = 'sent'
               ) as followups_sent_count
        FROM campaigns c
        LEFT JOIN recipients r ON r.campaign_id = c.id
        GROUP BY c.id
        ORDER BY c.created_at DESC
    """)
    return jsonify(campaigns)


@bp.route("/campaign/<int:campaign_id>", methods=["GET"])
def get_campaign(campaign_id):
    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    if not campaign:
        return json_error("Campaign not found", 404)

    recipients = query_db("SELECT * FROM recipients WHERE campaign_id = ? ORDER BY id", (campaign_id,))

    followups_by_recipient = {}
    for f in query_db(
        """
        SELECT f.* FROM followups f
        JOIN recipients r ON r.id = f.recipient_id
        WHERE r.campaign_id = ?
        ORDER BY f.id
    """,
        (campaign_id,),
    ):
        followups_by_recipient.setdefault(f["recipient_id"], []).append(f)
    replies_by_recipient = {}
    for rep in query_db(
        """
        SELECT rp.* FROM replies rp
        JOIN recipients r ON r.id = rp.recipient_id
        WHERE r.campaign_id = ?
        ORDER BY rp.received_at
    """,
        (campaign_id,),
    ):
        replies_by_recipient.setdefault(rep["recipient_id"], []).append(rep)

    for r in recipients:
        r["followups"] = followups_by_recipient.get(r["id"], [])
        r["replies"] = replies_by_recipient.get(r["id"], [])

    campaign["recipients"] = recipients
    campaign["sequence"] = campaign_sequence(campaign)
    campaign["max_followups"] = MAX_FOLLOWUPS
    return jsonify(campaign)


@bp.route("/campaign/new", methods=["POST"])
def create_campaign():
    data = request.json
    if not data:
        return json_error("No data provided")

    name = (data.get("name") or "").strip()
    goal = (data.get("goal") or "").strip()
    email_list_raw = (data.get("email_list") or "").strip()
    rows = data.get("recipients")  # structured rows, e.g. from a CSV import
    additional_context = (data.get("additional_context") or "").strip()

    if not name:
        return json_error("Campaign name is required")
    if not goal:
        return json_error("Campaign goal is required")
    if not email_list_raw and not rows:
        return json_error("Email list is required")

    if not os.path.exists(RESUME_PATH):
        return json_error("Please upload your resume in Profile before creating a campaign")

    parsed = _clean_recipient_rows(rows) if rows else parse_email_list(email_list_raw)
    if not parsed:
        return json_error("No valid emails found in the list")

    # Blocked domains are dropped up front so no AI calls are wasted on them
    blocked = _blocked_domains()
    skipped_blocked = [e["email"] for e in parsed if e["email"].split("@")[-1].lower() in blocked]
    parsed = [e for e in parsed if e["email"] not in skipped_blocked]
    if not parsed:
        return json_error("Every email in the list is from a blocked domain")

    campaign_id = execute_db(
        "INSERT INTO campaigns (name, goal, additional_context) VALUES (?, ?, ?)",
        (name, goal, additional_context or None),
    )

    conn = get_db()
    conn.executemany(
        "INSERT INTO recipients (campaign_id, email, name, company, role, notes, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'draft')",
        [(campaign_id, e["email"], *(e.get(f) or None for f in RECIPIENT_FIELDS)) for e in parsed],
    )
    conn.commit()
    conn.close()

    return jsonify(
        {
            "campaign_id": campaign_id,
            "recipients_count": len(parsed),
            "skipped_blocked": skipped_blocked,
            "message": f"Campaign created with {len(parsed)} recipients",
        }
    )


def _clean_recipient_rows(rows):
    """Validate and de-duplicate structured recipient rows ({email, name, company, role, notes})."""
    seen, result = set(), []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        match = EMAIL_REGEX.search(str(row.get("email") or ""))
        if not match or match.group(0).lower() in seen:
            continue
        seen.add(match.group(0).lower())
        entry = {"email": match.group(0)}
        for f in RECIPIENT_FIELDS:
            value = str(row.get(f) or "").strip()
            entry[f] = value[:1000] if value else None
        result.append(entry)
    return result


@bp.route("/campaign/<int:campaign_id>", methods=["DELETE"])
def delete_campaign(campaign_id):
    # Recipients, follow-ups and replies go with it (ON DELETE CASCADE)
    conn = get_db()
    conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Campaign deleted"})


# ─── Generation ───────────────────────────────────────────────────────────────


def _generate_for_recipient(config, campaign, recipient, api_key):
    return generate_email(
        profile=config.get("profile", {}),
        recipient={k: recipient.get(k) for k in ("email",) + RECIPIENT_FIELDS},
        campaign_goal=campaign["goal"],
        additional_context=campaign.get("additional_context") or "",
        api_key=api_key,
        resume_parsed=config.get("resume_parsed", {}),
        fallback_keys=configured_groq_keys(config),
        models=text_models(config),
    )


# Brief-builder answers the AI may use (anything else in the request is ignored)
BRIEF_FIELDS = (
    "purpose",
    "field",
    "timing",
    "dates",
    "work_mode",
    "location",
    "audience",
    "project_to_highlight",
    "availability",
    "flexibility",
    "links",
    "tone",
    "anything_else",
)


@bp.route("/campaign/brief", methods=["POST"])
def write_brief():
    """Write the campaign goal + additional context from a few quick answers and the profile."""
    data = request.json or {}
    answers = {k: str(data.get(k) or "").strip()[:500] for k in BRIEF_FIELDS}
    if not answers["purpose"] and not answers["field"]:
        return json_error("Tell me at least what you're looking for, or which field")
    config = load_config()
    try:
        keys = get_groq_keys(config)
        brief = generate_brief(
            config.get("profile", {}),
            config.get("resume_parsed", {}),
            answers,
            random.choice(keys),
            models=text_models(config),
        )
    except Exception as e:
        return json_error(str(e), 502 if not isinstance(e, ValueError) else 400)
    return jsonify(brief)


@bp.route("/campaign/sample-email", methods=["POST"])
def sample_email():
    """Write one email for a single recipient from the current brief. Nothing is saved or sent."""
    data = request.json or {}
    goal = (data.get("goal") or "").strip()
    if not goal:
        return json_error("Write the campaign goal first")
    recipient = data.get("recipient") or {}
    email = (recipient.get("email") or "").strip() or "someone@example-company.com"
    config = load_config()
    try:
        keys = get_groq_keys(config)
        result = _generate_for_recipient(
            config,
            {"goal": goal, "additional_context": (data.get("additional_context") or "").strip()},
            {"email": email, **{f: (recipient.get(f) or None) for f in RECIPIENT_FIELDS}},
            random.choice(keys),
        )
    except Exception as e:
        return json_error(str(e), 400 if isinstance(e, ValueError) else 502)
    return jsonify({"email": email, "subject": result["subject"], "body": result["body"]})


@bp.route("/campaign/<int:campaign_id>/generate", methods=["POST"])
def generate_campaign_emails(campaign_id):
    """
    Generate emails for draft recipients that don't have one yet. Drafts that
    were already generated (and possibly hand-edited) are left alone; use the
    per-recipient regenerate endpoint to replace one.
    """
    config = load_config()
    try:
        key_cycle = get_groq_key_cycle(config)
    except ValueError as e:
        return json_error(str(e))

    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    if not campaign:
        return json_error("Campaign not found", 404)

    def prepare():
        recipients = query_db(
            "SELECT * FROM recipients WHERE campaign_id = ? AND status = 'draft' AND subject IS NULL",
            (campaign_id,),
        )
        return (recipients, None) if recipients else (None, "No draft recipients need an email generated")

    job, recipients, err = jobs.try_start_job(
        jobs.job_key("generate", campaign_id),
        "generate",
        jobs.new_generation_data(),
        prepare,
    )
    if err == "running":
        return json_error("Generation is already running for this campaign", 409)
    if err:
        return json_error(err)

    def generate_one(r):
        time.sleep(random.uniform(0.1, 0.5))
        return _generate_for_recipient(config, campaign, r, get_next_groq_key(key_cycle))

    def save_one(conn, r, result):
        conn.execute(
            "UPDATE recipients SET subject = ?, email_body = ? WHERE id = ?",
            (result["subject"], result["body"], r["id"]),
        )

    # Cap workers at 3 (matching max Groq API keys) to avoid TPM rate limit spikes
    jobs.start_in_thread(jobs.run_generation_job, job, recipients, generate_one, save_one, 3)
    return jsonify({"message": "Generation started", "total": len(recipients)})


@bp.route("/campaign/<int:campaign_id>/generate-progress", methods=["GET"])
def get_generation_progress(campaign_id):
    return jsonify(
        jobs.get_job_data(jobs.job_key("generate", campaign_id))
        or {
            "status": "idle",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "errors": [],
        }
    )


@bp.route("/campaign/<int:campaign_id>/recipient/<int:recipient_id>/regenerate", methods=["POST"])
def regenerate_recipient_email(campaign_id, recipient_id):
    config = load_config()
    try:
        keys = get_groq_keys(config)
    except ValueError as e:
        return json_error(str(e))

    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    recipient = query_db(
        "SELECT * FROM recipients WHERE id = ? AND campaign_id = ?",
        (recipient_id, campaign_id),
        one=True,
    )
    if not campaign or not recipient:
        return json_error("Campaign or recipient not found", 404)
    if recipient["status"] == "sent":
        return json_error("This email has already been sent")

    try:
        result = _generate_for_recipient(config, campaign, recipient, random.choice(keys))
    except Exception as e:
        return json_error(str(e), 500)

    conn = get_db()
    conn.execute(
        "UPDATE recipients SET subject = ?, email_body = ? WHERE id = ?",
        (result["subject"], result["body"], recipient_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"subject": result["subject"], "body": result["body"], "message": "Email regenerated"})


# ─── Recipients ───────────────────────────────────────────────────────────────


@bp.route("/campaign/<int:campaign_id>/preview", methods=["GET"])
def preview_campaign(campaign_id):
    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    if not campaign:
        return json_error("Campaign not found", 404)

    recipients = query_db("SELECT * FROM recipients WHERE campaign_id = ? ORDER BY id", (campaign_id,))
    return jsonify({"campaign": campaign, "recipients": recipients})


@bp.route("/campaign/<int:campaign_id>/recipient/<int:recipient_id>", methods=["PUT"])
def update_recipient(campaign_id, recipient_id):
    """Manual edit of a draft's subject/body."""
    data = request.json
    if not data:
        return json_error("No data provided")

    conn = get_db()
    for field in ("subject", "email_body"):
        if field in data:
            conn.execute(
                f"UPDATE recipients SET {field} = ? WHERE id = ? AND campaign_id = ? AND status != 'sent'",
                (data[field], recipient_id, campaign_id),
            )
    conn.commit()
    conn.close()
    return jsonify({"message": "Recipient updated"})


def apply_status_update(conn, recipient_id, data, campaign_id=None):
    """
    Partially update a recipient's reply-tracking fields. Only keys present in
    `data` are written, so saving a pasted reply never resets the status and
    changing the status never erases the pasted reply.
    """
    fields = {}
    for key in ("reply_status", "reply_content", "check_back_date"):
        if key in data:
            fields[key] = data[key] or None
    if "exclude_followup" in data:
        fields["exclude_followup"] = 1 if data["exclude_followup"] else 0
    if "reply_status" in fields and fields["reply_status"] is None:
        fields["reply_status"] = "no_reply"
    if fields.get("reply_status") in AUTO_EXCLUDED_STATUSES:
        fields["exclude_followup"] = 1
    if not fields:
        return
    fields["status_updated_at"] = utc_now_iso()

    sql = "UPDATE recipients SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?"
    params = list(fields.values()) + [recipient_id]
    if campaign_id is not None:
        sql += " AND campaign_id = ?"
        params.append(campaign_id)
    conn.execute(sql, params)


@bp.route("/recipient/<int:recipient_id>/status", methods=["PATCH"])
def update_recipient_status(recipient_id):
    data = request.json or {}
    conn = get_db()
    apply_status_update(conn, recipient_id, data)
    conn.commit()
    conn.close()

    recipient = query_db("SELECT * FROM recipients WHERE id = ?", (recipient_id,), one=True)
    if not recipient:
        return json_error("Recipient not found", 404)
    return jsonify({"message": "Status updated successfully", "recipient": recipient})


@bp.route("/campaign/<int:campaign_id>/recipients/status", methods=["PATCH"])
def bulk_update_recipient_statuses(campaign_id):
    data = request.json or {}
    conn = get_db()
    for update in data.get("updates", []):
        if "recipient_id" in update:
            apply_status_update(conn, update["recipient_id"], update, campaign_id=campaign_id)
    conn.commit()
    conn.close()
    return jsonify({"message": "Statuses updated successfully"})


@bp.route("/recipient/<int:recipient_id>", methods=["DELETE"])
def delete_recipient(recipient_id):
    conn = get_db()
    # Follow-ups and replies go with it (ON DELETE CASCADE)
    conn.execute("DELETE FROM recipients WHERE id = ?", (recipient_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Recipient deleted"})


@bp.route("/campaign/<int:campaign_id>/followup-eligible", methods=["GET"])
def get_followup_eligible(campaign_id):
    recipients = query_db(
        f"""
        SELECT * FROM recipients
        WHERE campaign_id = ?
        AND status = 'sent'
        AND follow_up_sent < ?
        AND exclude_followup = 0
        AND COALESCE(reply_status, 'no_reply') NOT IN ({FOLLOWUP_INELIGIBLE_SQL})
    """,
        (campaign_id, MAX_FOLLOWUPS),
    )
    return jsonify(recipients)


@bp.route("/campaign/<int:campaign_id>/retry-failed", methods=["POST"])
def retry_failed(campaign_id):
    """Move failed sends (initial emails and follow-ups) back to draft so they can be sent again."""
    conn = get_db()
    recipients = conn.execute(
        "UPDATE recipients SET status = 'draft', error_message = NULL WHERE campaign_id = ? AND status = 'failed'",
        (campaign_id,),
    ).rowcount
    followups = conn.execute(
        """
        UPDATE followups SET status = 'draft', error_message = NULL
        WHERE status = 'failed' AND recipient_id IN (
            SELECT id FROM recipients WHERE campaign_id = ? AND follow_up_sent = 0
        )
    """,
        (campaign_id,),
    ).rowcount
    conn.commit()
    conn.close()
    return jsonify({"recipients_reset": recipients, "followups_reset": followups})


# ─── Sending ──────────────────────────────────────────────────────────────────


@bp.route("/campaign/<int:campaign_id>/send", methods=["POST"])
def send_campaign(campaign_id):
    try:
        total = start_campaign_send(campaign_id)
    except SendError as e:
        return json_error(str(e), e.status)
    return jsonify({"message": "Sending started", "total": total})


@bp.route("/campaign/<int:campaign_id>/send/stop", methods=["POST"])
def stop_campaign_send(campaign_id):
    if not jobs.request_stop(jobs.job_key("send", campaign_id)):
        return json_error("No send in progress for this campaign", 404)
    return jsonify({"message": "Stopping after the current email"})


@bp.route("/campaign/<int:campaign_id>/progress", methods=["GET"])
def get_send_progress(campaign_id):
    return jsonify(
        jobs.get_job_data(jobs.job_key("send", campaign_id))
        or {
            "status": "idle",
            "current": 0,
            "total": 0,
            "log": [],
        }
    )


@bp.route("/campaign/<int:campaign_id>/schedule", methods=["POST"])
def schedule_campaign(campaign_id):
    """Set (or clear, with send_at=null) the time the campaign's drafts start sending."""
    data = request.json or {}
    send_at = data.get("send_at")
    value = None
    if send_at:
        try:
            dt = datetime.fromisoformat(str(send_at).replace("Z", "+00:00"))
        except ValueError:
            return json_error("send_at must be an ISO 8601 date-time")
        if dt.tzinfo is None:
            return json_error("send_at must include a timezone offset")
        dt = dt.astimezone(UTC)
        if dt <= datetime.now(UTC):
            return json_error("send_at must be in the future")
        value = dt.isoformat()

    conn = get_db()
    updated = conn.execute("UPDATE campaigns SET scheduled_at = ? WHERE id = ?", (value, campaign_id)).rowcount
    conn.commit()
    conn.close()
    if not updated:
        return json_error("Campaign not found", 404)
    return jsonify({"scheduled_at": value})


@bp.route("/campaign/<int:campaign_id>/sequence", methods=["PUT"])
def update_sequence(campaign_id):
    """Configure the automatic follow-up sequence: {enabled, auto_send, steps: [{delay_days}]}."""
    data = request.json or {}
    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    if not campaign:
        return json_error("Campaign not found", 404)

    steps = data.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not (1 <= len(steps) <= MAX_FOLLOWUPS):
            return json_error(f"Provide between 1 and {MAX_FOLLOWUPS} steps")
        campaign["sequence_json"] = json.dumps(steps)
        steps = campaign_sequence(campaign)  # validates/clamps

    fields = {}
    if "enabled" in data:
        fields["sequence_enabled"] = 1 if data["enabled"] else 0
    if "auto_send" in data:
        fields["auto_send_followups"] = 1 if data["auto_send"] else 0
    if steps is not None:
        fields["sequence_json"] = json.dumps(steps)
    if fields:
        conn = get_db()
        conn.execute(
            "UPDATE campaigns SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
            list(fields.values()) + [campaign_id],
        )
        conn.commit()
        conn.close()

    updated = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    return jsonify(
        {
            "sequence_enabled": updated["sequence_enabled"],
            "auto_send_followups": updated["auto_send_followups"],
            "sequence": campaign_sequence(updated),
        }
    )
