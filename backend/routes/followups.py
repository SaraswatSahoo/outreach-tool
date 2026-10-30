"""Follow-up generation, editing and sending (manual flow; automatic sequences live in automation.py)."""

import random
import time

from flask import Blueprint, jsonify, request

import jobs
from common import FOLLOWUP_INELIGIBLE_SQL, get_groq_key_cycle, get_next_groq_key, json_error
from config import load_config
from constants import MAX_FOLLOWUPS
from db import get_db, query_db
from followup_service import campaign_sequence, generate_followup, previous_followup_bodies, save_followup_draft
from sending import SendError, start_followup_send

bp = Blueprint("followups", __name__, url_prefix="/api")


@bp.route("/campaign/<int:campaign_id>/generate-followups", methods=["POST"])
def generate_followups(campaign_id):
    config = load_config()
    try:
        key_cycle = get_groq_key_cycle(config)
    except ValueError as e:
        return json_error(str(e))

    campaign = query_db("SELECT * FROM campaigns WHERE id = ?", (campaign_id,), one=True)
    if not campaign:
        return json_error("Campaign not found", 404)
    max_steps = max(len(campaign_sequence(campaign)), 1)

    data = request.json or {}
    global_context = data.get("global_context", "")
    recipient_ids = data.get("recipient_ids")

    def prepare():
        recipients = query_db(
            f"""
            SELECT * FROM recipients
            WHERE campaign_id = ? AND status = 'sent' AND follow_up_sent < ?
            AND exclude_followup = 0
            AND COALESCE(reply_status, 'no_reply') NOT IN ({FOLLOWUP_INELIGIBLE_SQL})
        """,
            (campaign_id, MAX_FOLLOWUPS),
        )
        if recipient_ids:
            recipients = [r for r in recipients if r["id"] in recipient_ids]
        return (recipients, None) if recipients else (None, "No recipients eligible for follow-up")

    job, recipients, err = jobs.try_start_job(
        jobs.job_key("generate_followup", campaign_id),
        "generate_followup",
        jobs.new_generation_data(),
        prepare,
    )
    if err == "running":
        return json_error("Follow-up generation is already running for this campaign", 409)
    if err:
        return json_error(err)

    def generate_one(r):
        time.sleep(random.uniform(0.1, 0.5))
        conn = get_db()
        try:
            previous = previous_followup_bodies(conn, r["id"])
        finally:
            conn.close()
        return generate_followup(config, r, get_next_groq_key(key_cycle), previous, max_steps, global_context)

    def save_one(conn, r, result):
        # Regenerating replaces any existing unsent draft, so a recipient
        # never ends up with two follow-ups queued.
        save_followup_draft(conn, r, result, source="manual")

    jobs.start_in_thread(jobs.run_generation_job, job, recipients, generate_one, save_one, 3)
    return jsonify({"message": "Follow-up generation started", "total": len(recipients)})


@bp.route("/campaign/<int:campaign_id>/generate-followup-progress", methods=["GET"])
def get_followup_generate_progress(campaign_id):
    return jsonify(
        jobs.get_job_data(jobs.job_key("generate_followup", campaign_id))
        or {
            "status": "idle",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "errors": [],
        }
    )


@bp.route("/campaign/<int:campaign_id>/followup/preview", methods=["GET"])
def preview_followups(campaign_id):
    followups = query_db(
        """
        SELECT f.*, r.email as recipient_email, r.name as recipient_name,
               r.message_id as original_message_id, r.reply_status, r.company, r.role
        FROM followups f
        JOIN recipients r ON r.id = f.recipient_id
        WHERE r.campaign_id = ? AND f.status = 'draft'
        ORDER BY f.id
    """,
        (campaign_id,),
    )
    return jsonify({"followups": followups, "campaign_id": campaign_id})


@bp.route("/followup/<int:followup_id>", methods=["PUT"])
def update_followup(followup_id):
    data = request.json
    if not data:
        return json_error("No data provided")

    conn = get_db()
    for field in ("subject", "email_body"):
        if field in data:
            conn.execute(
                f"UPDATE followups SET {field} = ? WHERE id = ? AND status != 'sent'",
                (data[field], followup_id),
            )
    conn.commit()
    conn.close()
    return jsonify({"message": "Follow-up updated"})


@bp.route("/followup/<int:followup_id>", methods=["DELETE"])
def delete_followup(followup_id):
    conn = get_db()
    conn.execute("DELETE FROM followups WHERE id = ? AND status != 'sent'", (followup_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Follow-up deleted"})


@bp.route("/campaign/<int:campaign_id>/followup/send", methods=["POST"])
def send_followups(campaign_id):
    try:
        total = start_followup_send(campaign_id)
    except SendError as e:
        return json_error(str(e), e.status)
    return jsonify({"message": "Follow-up sending started", "total": total})


@bp.route("/campaign/<int:campaign_id>/followup/send/stop", methods=["POST"])
def stop_followup_send(campaign_id):
    if not jobs.request_stop(jobs.job_key("send_followup", campaign_id)):
        return json_error("No follow-up send in progress for this campaign", 404)
    return jsonify({"message": "Stopping after the current email"})


@bp.route("/campaign/<int:campaign_id>/followup/progress", methods=["GET"])
def get_followup_send_progress(campaign_id):
    return jsonify(
        jobs.get_job_data(jobs.job_key("send_followup", campaign_id))
        or {
            "status": "idle",
            "current": 0,
            "total": 0,
            "log": [],
        }
    )
