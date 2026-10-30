"""Inbox monitor (bounces, out-of-office, replies) routes and the background scheduler."""

import atexit
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Blueprint, jsonify

import automation
from common import json_error, utc_now_iso
from config import load_config
from db import get_db, query_db
from inbox_monitor import run_inbox_monitor

bp = Blueprint("inbox", __name__, url_prefix="/api")

MONITOR_INTERVAL_MINUTES = 10

_last_monitor_result = {}
_monitor_lock = threading.Lock()


def _run_monitor(triggered_by):
    """Run one monitor pass; caller must hold _monitor_lock."""
    global _last_monitor_result
    conn = get_db()
    try:
        result = run_inbox_monitor(load_config(), conn)
        result["status"] = "error" if result.get("error") else "complete"
        result["triggered_by"] = triggered_by
        _last_monitor_result = result
        print(
            f"[Inbox Monitor] Bounces: {result.get('bounces_detected', 0)}, "
            f"OOO: {result.get('ooo_detected', 0)}, Updated: {result.get('updated', 0)}"
        )
    except Exception as e:
        _last_monitor_result = {
            "status": "error",
            "error": str(e),
            "last_run": utc_now_iso(),
            "triggered_by": triggered_by,
        }
        print(f"[Inbox Monitor] Error: {e}")
    finally:
        conn.close()


def scheduled_inbox_check():
    if not _monitor_lock.acquire(blocking=False):
        return  # a manual check is already running
    try:
        _run_monitor("scheduler")
    finally:
        _monitor_lock.release()


def _safe(fn, label):
    def run():
        try:
            fn()
        except Exception as e:
            print(f"[{label}] Error: {e}")

    return run


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_inbox_check, "interval", minutes=MONITOR_INTERVAL_MINUTES, id="inbox_monitor")
    # Scheduled campaign sends ("send at")
    scheduler.add_job(
        _safe(automation.run_scheduled_sends, "Scheduled sends"), "interval", minutes=1, id="scheduled_sends"
    )
    # Automatic follow-up sequences. Runs after the inbox monitor's cadence so
    # fresh replies stop a sequence before its next step is drafted.
    scheduler.add_job(_safe(automation.run_sequences, "Sequences"), "interval", minutes=15, id="sequences")
    # One-off, shortly after startup: finish sends that a restart cut off
    scheduler.add_job(
        _safe(automation.resume_interrupted_sends, "Resume"),
        "date",
        run_date=datetime.now() + timedelta(seconds=5),
        id="resume_sends",
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))


@bp.route("/inbox/check", methods=["POST"])
def manual_inbox_check():
    global _last_monitor_result
    if not _monitor_lock.acquire(blocking=False):
        return jsonify({"error": "Monitor is already running. Please wait.", "status": "running"}), 429

    _last_monitor_result = {"status": "running", "last_run": utc_now_iso(), "triggered_by": "manual"}

    def background():
        try:
            _run_monitor("manual")
        finally:
            _monitor_lock.release()

    threading.Thread(target=background, daemon=True).start()
    return jsonify({"message": "Inbox check started", "status": "running"})


@bp.route("/inbox/status", methods=["GET"])
def inbox_status():
    result = (
        dict(_last_monitor_result)
        if _last_monitor_result
        else {
            "last_run": None,
            "bounces_detected": 0,
            "ooo_detected": 0,
            "updated": 0,
            "message": "Monitor has not run yet since app start.",
        }
    )
    result["interval_minutes"] = MONITOR_INTERVAL_MINUTES
    return jsonify(result)


@bp.route("/campaign/<int:campaign_id>/bounces", methods=["GET"])
def campaign_bounces(campaign_id):
    if not query_db("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,), one=True):
        return json_error("Campaign not found", 404)
    return jsonify(
        query_db(
            """
        SELECT id, email, name, reply_status, error_message, status_updated_at
        FROM recipients
        WHERE campaign_id = ? AND reply_status = 'invalid_email'
    """,
            (campaign_id,),
        )
    )
