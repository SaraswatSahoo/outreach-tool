"""Email address checking before a campaign is created (see email_check.py)."""

import uuid

from flask import Blueprint, jsonify, request

import email_check
import jobs
from common import json_error
from config import load_config
from db import get_db

bp = Blueprint("emails", __name__, url_prefix="/api")

MAX_EMAILS = 2000


@bp.route("/emails/verify", methods=["POST"])
def start_verify():
    """Check a list of addresses in the background. Returns a job id to poll."""
    data = request.json or {}
    emails = [str(e).strip() for e in data.get("emails", []) if str(e).strip()]
    if not emails:
        return json_error("No email addresses to check")
    if len(emails) > MAX_EMAILS:
        return json_error(f"Check at most {MAX_EMAILS} addresses at a time")

    _prune_old_checks()
    key = jobs.job_key("verify", uuid.uuid4().hex)
    job, _, _ = jobs.try_start_job(
        key, "verify", {"status": "checking", "total": len(emails), "done": 0, "results": []}
    )
    jobs.start_in_thread(_run_verify, job, emails, load_config())
    return jsonify({"job_id": key.split(":", 1)[1], "total": len(emails)})


def _run_verify(job, emails, config):
    def progress(done, total):
        job.data.update(done=done, total=total)
        if done == total or done % 5 == 0:
            job.save()

    conn = get_db()
    try:
        results = email_check.verify_emails(emails, config, conn, progress=progress)
        job.data["results"] = [r.to_dict() for r in results]
        job.data["status"] = "complete"
    except Exception as e:
        job.data["status"] = "error"
        job.data["error"] = f"Checking failed: {e}"
    finally:
        conn.close()
        job.save()


def _prune_old_checks():
    """Verification jobs are throwaway; don't let them pile up in the jobs table."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM jobs WHERE kind = 'verify' AND datetime(updated_at) < datetime('now', '-1 day')")
        conn.commit()
    finally:
        conn.close()


@bp.route("/emails/verify/<job_id>", methods=["GET"])
def verify_progress(job_id):
    data = jobs.get_job_data(jobs.job_key("verify", job_id))
    if data is None:
        return json_error("Unknown check", 404)
    return jsonify(data)


@bp.route("/emails/credits", methods=["GET"])
def credits():
    """Remaining credits on the configured verification service (None if it doesn't report them)."""
    config = load_config()
    provider = email_check.PROVIDERS.get(config.get("verify_provider") or "")
    key = (config.get("verify_api_key") or "").strip()
    if not provider or not key:
        return jsonify({"provider": None, "credits": None})
    try:
        remaining = provider.credits(key, email_check.http_get_json)
    except email_check.ServiceError as e:
        return jsonify({"provider": provider.label, "credits": None, "error": str(e)})
    return jsonify({"provider": provider.label, "credits": remaining})
