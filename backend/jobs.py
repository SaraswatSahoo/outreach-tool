"""
Background job tracking for email generation and sending.

Progress lives in memory while a job runs and is mirrored to the `jobs` table
after every step, so the UI shows the truth after a backend restart: a job
that was running when the process died is reported as "interrupted".
"""

import json
import smtplib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import scheduling
from ai_generator import RateLimitExhausted
from common import utc_now_iso
from db import get_db
from email_sender import GmailUnreachable
from llm import NoModelAvailable

RUNNING_STATUSES = ("generating", "sending", "checking")

# Stop a send after this many emails in a row couldn't reach Gmail at all
MAX_CONSECUTIVE_UNREACHABLE = 3


def _sleep(seconds):
    """Delay between sends (patched out in tests)."""
    time.sleep(seconds)


_jobs = {}
_start_lock = threading.Lock()


def job_key(kind, campaign_id):
    return f"{kind}:{campaign_id}"


class Job:
    def __init__(self, key, kind, data):
        self.key = key
        self.kind = kind
        self.data = data

    @property
    def status(self):
        return self.data.get("status")

    @status.setter
    def status(self, value):
        self.data["status"] = value

    def save(self):
        conn = get_db()
        try:
            conn.execute(
                """
                INSERT INTO jobs (key, kind, status, data, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    kind = excluded.kind, status = excluded.status,
                    data = excluded.data, updated_at = excluded.updated_at
                """,
                (self.key, self.kind, self.status, json.dumps(self.data), utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()


def mark_interrupted_jobs():
    """Called at startup: anything still 'running' in the DB died with the old process."""
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT key, data, updated_at FROM jobs WHERE status IN ({','.join('?' * len(RUNNING_STATUSES))})",
            RUNNING_STATUSES,
        ).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            data["status"] = "interrupted"
            data["current_email"] = ""
            data["last_activity_at"] = row["updated_at"]  # when the old process last made progress
            conn.execute(
                "UPDATE jobs SET status = 'interrupted', data = ?, updated_at = ? WHERE key = ?",
                (json.dumps(data), utc_now_iso(), row["key"]),
            )
        conn.commit()
    finally:
        conn.close()


def get_job_data(key):
    """Current progress for a job, from memory or (after a restart) from the DB."""
    job = _jobs.get(key)
    if job:
        return job.data
    conn = get_db()
    try:
        row = conn.execute("SELECT data FROM jobs WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return json.loads(row["data"]) if row else None


def try_start_job(key, kind, initial_data, prepare=None):
    """
    Atomically register a new job unless one with this key is already running.

    `prepare()` (optional) runs inside the lock and returns either
    (items, None) or (None, error_message); use it to select the work items so
    two concurrent requests can't both pick up the same drafts.

    Returns (job, items, error). `error` is "running" if a job is in progress.
    """
    with _start_lock:
        existing = _jobs.get(key)
        if existing and existing.status in RUNNING_STATUSES:
            return None, None, "running"
        items = None
        if prepare:
            items, err = prepare()
            if err:
                return None, None, err
            initial_data["total"] = len(items)
        job = Job(key, kind, initial_data)
        _jobs[key] = job
        job.save()
        return job, items, None


def start_in_thread(target, *args):
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


# ─── Generation ───────────────────────────────────────────────────────────────


def new_generation_data(total=0):
    return {"total": total, "completed": 0, "failed": 0, "status": "generating", "errors": []}


def run_generation_job(job, items, generate_one, save_one, max_workers=3):
    """
    Run generate_one(item) in parallel and persist each result with
    save_one(conn, item, result). Always leaves the job in a terminal state
    ("complete" or "error"), even if something unexpected blows up.
    A RateLimitExhausted error stops the remaining work.
    """
    progress = job.data
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(len(items), max_workers))) as executor:
            futures = {executor.submit(generate_one, item): item for item in items}

            conn = get_db()
            try:
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        result = future.result()
                    except Exception as e:
                        progress["failed"] += 1
                        progress["errors"].append({"email": item["email"], "error": str(e)})
                        # Every remaining item would fail the same way: stop now
                        if isinstance(e, (RateLimitExhausted, NoModelAvailable)):
                            progress["status"] = "error"
                            for f in futures:
                                f.cancel()
                            job.save()
                            break
                        job.save()
                        continue

                    save_one(conn, item, result)
                    conn.commit()
                    progress["completed"] += 1
                    job.save()
            finally:
                conn.close()
    except Exception as e:
        progress["errors"].append({"email": "-", "error": f"Generation job crashed: {e}"})
        progress["status"] = "error"

    if progress["status"] != "error":
        progress["status"] = "complete"
    job.save()


# ─── Sending ──────────────────────────────────────────────────────────────────


def new_send_data(delay_seconds, total=0):
    return {
        "current": 0,
        "total": total,
        "status": "sending",
        "log": [],
        "current_email": "",
        "delay_seconds": delay_seconds,
    }


def request_stop(key):
    """Ask a running send job to stop after the current email. Returns False if none is running."""
    job = _jobs.get(key)
    if not job or job.status not in RUNNING_STATUSES:
        return False
    job.data["cancel_requested"] = True
    job.save()
    return True


def _cancelled(job):
    return bool(job.data.get("cancel_requested"))


def _interruptible_sleep(job, seconds):
    """Sleep in short chunks so a stop request takes effect quickly. Returns False if stopped."""
    remaining = seconds
    while remaining > 0:
        if _cancelled(job):
            return False
        chunk = min(5, remaining)
        _sleep(chunk)
        remaining -= chunk
    return not _cancelled(job)


def _wait_for_send_slot(job, config):
    """Block until the send window / daily cap allow another email. Returns False if stopped."""
    while True:
        if _cancelled(job):
            return False
        now = scheduling.now_local()
        settings = scheduling.schedule_settings(config or {}, now)  # warm-up cap changes daily
        conn = get_db()
        try:
            sent_today = scheduling.sent_today_count(conn, now)
        finally:
            conn.close()
        until = scheduling.next_send_time(now, settings, sent_today)
        if until is None:
            if job.data.pop("waiting_until", None):
                job.data.pop("waiting_reason", None)
                job.save()
            return True
        reason = (
            "daily send limit reached"
            if settings["daily_cap"] and sent_today >= settings["daily_cap"]
            else "outside sending hours"
        )
        if job.data.get("waiting_until") != until.isoformat():
            job.data["waiting_until"] = until.isoformat()
            job.data["waiting_reason"] = reason
            job.save()
        if not _interruptible_sleep(job, min(60, max(1, (until - now).total_seconds()))):
            return False


def run_send_job(job, items, email_key, send_one, on_sent, on_failed, delay, config=None, should_skip=None):
    """
    Send items one by one with a delay between them.
    - Respects the configured send window and daily cap (waits, status stays "sending").
    - A stop request ends the job after the current email (status "stopped").
    - should_skip(conn, item) -> reason|None is re-checked right before each send.
    - A Gmail authentication failure aborts the whole job (it would fail for
      every recipient), leaving the remaining items untouched as drafts.
    - If Gmail can't be reached (network/firewall/antivirus), the email is left
      as a draft ("not_sent"), not marked failed; after
      MAX_CONSECUTIVE_UNREACHABLE in a row the job stops with a clear message.
    """
    progress = job.data
    unreachable_streak = 0
    try:
        for i, item in enumerate(items):
            if not _wait_for_send_slot(job, config):
                progress["status"] = "stopped"
                return

            email = item[email_key]
            progress["current"] = i + 1
            progress["current_email"] = email
            job.save()

            conn = get_db()
            try:
                skip_reason = should_skip(conn, item) if should_skip else None
                if skip_reason:
                    progress["log"].append(
                        {"email": email, "status": "skipped", "error": skip_reason, "time": utc_now_iso()}
                    )
                    continue
                message_id = send_one(item)
                on_sent(conn, item, message_id)
                unreachable_streak = 0
                progress["log"].append({"email": email, "status": "sent", "time": utc_now_iso()})
            except GmailUnreachable as e:
                # Nothing was transmitted: keep it as a draft so it can be sent later
                unreachable_streak += 1
                progress["log"].append({"email": email, "status": "not_sent", "error": str(e), "time": utc_now_iso()})
                if unreachable_streak >= MAX_CONSECUTIVE_UNREACHABLE:
                    progress["status"] = "error"
                    progress["stop_reason"] = (
                        f"Stopped: couldn't reach Gmail {unreachable_streak} times in a row. {e} "
                        "Unsent emails are still drafts — check your connection (Settings → Test Gmail connection), "
                        "then send again."
                    )
                    return
            except smtplib.SMTPAuthenticationError as e:
                progress["log"].append(
                    {
                        "email": email,
                        "status": "failed",
                        "error": f"Gmail login failed — check your App Password in Settings ({e})",
                        "time": utc_now_iso(),
                    }
                )
                progress["status"] = "error"
                progress["stop_reason"] = "Stopped: Gmail rejected the login. Check your App Password in Settings."
                return
            except Exception as e:
                on_failed(conn, item, str(e))
                progress["log"].append({"email": email, "status": "failed", "error": str(e), "time": utc_now_iso()})
            finally:
                conn.commit()
                conn.close()

            # Wait between emails (not after the last one; skipped items `continue` above)
            if i < len(items) - 1:
                job.save()
                if not _interruptible_sleep(job, delay):
                    progress["status"] = "stopped"
                    return
    except Exception as e:
        progress["log"].append(
            {"email": "-", "status": "failed", "error": f"Send job crashed: {e}", "time": utc_now_iso()}
        )
        progress["status"] = "error"
    finally:
        progress["current_email"] = ""
        progress.pop("waiting_until", None)
        progress.pop("waiting_reason", None)
        if progress["status"] == "sending":
            progress["status"] = "complete"
        job.save()
