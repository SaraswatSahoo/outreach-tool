"""
Sending schedule rules: an optional daily send window and daily send cap.

Times are evaluated in the local timezone of the machine running the backend
(the same clock the user sees). All functions here are pure apart from
sent_today_count(), so they're easy to test.
"""

from datetime import UTC, date, datetime, time, timedelta


def now_local():
    return datetime.now().astimezone()


def warmup_cap(config, today):
    """Today's warm-up limit (start + increase × days since the ramp began), or None if warm-up is off."""
    if not config.get("warmup_enabled"):
        return None
    try:
        started = date.fromisoformat(config.get("warmup_started_on") or "")
    except ValueError:
        started = today
    days = max(0, (today - started).days)
    start = max(1, int(config.get("warmup_start_per_day") or 20))
    step = max(0, int(config.get("warmup_increase_per_day") or 0))
    return start + step * days


def schedule_settings(config, now=None):
    """Window/cap settings in effect at `now` (the cap includes any warm-up limit)."""
    now = now or now_local()
    cap = max(0, int(config.get("daily_send_cap") or 0))
    ramp = warmup_cap(config, now.date())
    if ramp is not None:
        cap = min(cap, ramp) if cap else ramp
    return {
        "enabled": bool(config.get("send_window_enabled")),
        "start": int(config.get("send_window_start", 9)),
        "end": int(config.get("send_window_end", 18)),
        "weekdays_only": bool(config.get("send_window_weekdays_only", True)),
        "daily_cap": cap,
    }


def _day_allowed(d, s):
    return not (s["weekdays_only"] and d.weekday() >= 5)


def in_window(now, s):
    if not s["enabled"]:
        return True
    return _day_allowed(now, s) and s["start"] <= now.hour < s["end"]


def next_window_start(now, s):
    """The next moment at or after `now` that falls inside the send window."""
    if in_window(now, s):
        return now
    day = now
    for _ in range(8):
        start = datetime.combine(day.date(), time(s["start"]), tzinfo=now.tzinfo)
        if _day_allowed(day, s) and start >= now:
            return start
        day = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now  # misconfigured window (e.g. start >= end); don't block forever


def next_send_time(now, s, sent_today):
    """None if an email may be sent right now, otherwise the local datetime to wait until."""
    if s["daily_cap"] and sent_today >= s["daily_cap"]:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return next_window_start(tomorrow, s) if s["enabled"] else tomorrow
    if s["enabled"] and s["start"] < s["end"] and not in_window(now, s):
        return next_window_start(now, s)
    return None


def sent_today_count(conn, now):
    """Initial emails + follow-ups sent since local midnight."""
    midnight_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    since = midnight_utc.strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM recipients WHERE status = 'sent' AND datetime(sent_at) >= datetime(?)) +
          (SELECT COUNT(*) FROM followups WHERE status = 'sent' AND datetime(sent_at) >= datetime(?)) AS n
    """,
        (since, since),
    ).fetchone()
    return row["n"] or 0
