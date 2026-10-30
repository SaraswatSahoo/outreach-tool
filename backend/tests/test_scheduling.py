import time
from datetime import UTC, datetime, timedelta, timezone

import config
import jobs
import scheduling as S
from tests.helpers import create_campaign, wait_for

TZ = timezone(timedelta(hours=5, minutes=30))
WINDOW = {"enabled": True, "start": 9, "end": 18, "weekdays_only": True, "daily_cap": 0}


def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


def test_inside_window_sends_now():
    assert S.next_send_time(at(2026, 10, 7, 10), WINDOW, 0) is None  # Wednesday 10:00


def test_before_window_waits_until_start():
    assert S.next_send_time(at(2026, 10, 7, 7, 30), WINDOW, 0) == at(2026, 10, 7, 9)


def test_after_window_on_friday_waits_until_monday():
    assert S.next_send_time(at(2026, 10, 9, 19), WINDOW, 0) == at(2026, 10, 12, 9)  # Fri -> Mon


def test_weekends_allowed_when_not_weekdays_only():
    s = dict(WINDOW, weekdays_only=False)
    assert S.next_send_time(at(2026, 10, 10, 12), s, 0) is None  # Saturday


def test_daily_cap_waits_for_next_window():
    s = dict(WINDOW, daily_cap=10)
    assert S.next_send_time(at(2026, 10, 7, 10), s, 9) is None
    assert S.next_send_time(at(2026, 10, 7, 10), s, 10) == at(2026, 10, 8, 9)


def test_cap_without_window_waits_until_midnight():
    s = dict(WINDOW, enabled=False, daily_cap=5)
    assert S.next_send_time(at(2026, 10, 7, 22), s, 5) == at(2026, 10, 8, 0)


def test_disabled_window_never_blocks():
    assert S.next_send_time(at(2026, 10, 11, 3), dict(WINDOW, enabled=False), 999) is None


def test_send_job_waits_outside_window_and_can_be_stopped(client, fake_ai, fake_smtp, monkeypatch):
    cfg = config.load_config()
    cfg.update(send_window_enabled=True, send_window_start=9, send_window_end=10, send_window_weekdays_only=False)
    config.save_config(cfg)
    # Pretend it's 23:00 so the job has to wait for tomorrow's window
    monkeypatch.setattr(S, "now_local", lambda: datetime(2026, 10, 7, 23, 0, tzinfo=TZ))
    monkeypatch.setattr(jobs, "_sleep", lambda s: time.sleep(0.01))

    cid = create_campaign(client, "a@acme.com")
    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")
    assert client.post(f"/api/campaign/{cid}/send").status_code == 200

    deadline = time.time() + 3
    while time.time() < deadline:
        p = client.get(f"/api/campaign/{cid}/progress").get_json()
        if p.get("waiting_until"):
            break
        time.sleep(0.02)
    assert p["status"] == "sending" and p["waiting_reason"] == "outside sending hours"
    assert p["waiting_until"].startswith("2026-10-08T09:00")
    assert fake_smtp == []

    assert client.post(f"/api/campaign/{cid}/send/stop").status_code == 200
    done = wait_for(client, f"/api/campaign/{cid}/progress", "sending")
    assert done["status"] == "stopped" and fake_smtp == []
    assert client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["status"] == "draft"


def test_schedule_endpoint_and_scheduler(client, fake_ai, fake_smtp):
    import automation

    cid = create_campaign(client, "a@acme.com")
    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")

    assert (
        client.post(f"/api/campaign/{cid}/schedule", json={"send_at": "2020-01-01T00:00:00+00:00"}).status_code == 400
    )
    assert client.post(f"/api/campaign/{cid}/schedule", json={"send_at": "2099-01-01T09:00:00"}).status_code == 400
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert client.post(f"/api/campaign/{cid}/schedule", json={"send_at": future}).status_code == 200

    assert automation.run_scheduled_sends() == []  # not due yet
    started = automation.run_scheduled_sends(now=datetime.now(UTC) + timedelta(hours=2))
    assert started == [cid]
    wait_for(client, f"/api/campaign/{cid}/progress", "sending")
    assert [s["to"] for s in fake_smtp] == ["a@acme.com"]
    assert client.get(f"/api/campaign/{cid}").get_json()["scheduled_at"] is None


def test_schedule_settings_validation(client):
    assert client.post("/api/settings", json={"send_window_start": 18, "send_window_end": 9}).status_code == 400
    assert client.post("/api/settings", json={"send_window_enabled": True, "daily_send_cap": 50}).status_code == 200
    s = client.get("/api/settings").get_json()
    assert s["send_window_enabled"] is True and s["daily_send_cap"] == 50


def test_warmup_ramp_caps_daily_sends():
    from datetime import date

    cfg = {
        "warmup_enabled": True,
        "warmup_start_per_day": 20,
        "warmup_increase_per_day": 10,
        "warmup_started_on": "2026-10-01",
        "daily_send_cap": 0,
    }
    assert S.warmup_cap(cfg, date(2026, 10, 1)) == 20
    assert S.warmup_cap(cfg, date(2026, 10, 4)) == 50
    assert S.schedule_settings(cfg, at(2026, 10, 4, 12))["daily_cap"] == 50
    # An explicit lower daily cap still wins
    assert S.schedule_settings(dict(cfg, daily_send_cap=30), at(2026, 10, 4, 12))["daily_cap"] == 30
    assert S.warmup_cap(dict(cfg, warmup_enabled=False), date(2026, 10, 4)) is None


def test_enabling_warmup_starts_ramp_today(client):
    res = client.post(
        "/api/settings", json={"warmup_enabled": True, "warmup_start_per_day": 15, "warmup_increase_per_day": 5}
    )
    assert res.status_code == 200
    cfg = config.load_config()
    assert cfg["warmup_started_on"] == S.now_local().date().isoformat()
    assert client.get("/api/settings").get_json()["warmup_cap_today"] == 15


def test_interrupted_send_resumes_after_restart(client, fake_ai, fake_smtp):
    import automation

    cid = create_campaign(client, "a@acme.com\nb@beta.io")
    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")

    # Simulate a process that died mid-send: a "sending" job row, drafts untouched
    jobs.try_start_job(jobs.job_key("send", cid), "send", jobs.new_send_data(20, total=2))
    jobs._jobs.clear()
    jobs.mark_interrupted_jobs()
    assert client.get(f"/api/campaign/{cid}/progress").get_json()["status"] == "interrupted"

    assert automation.resume_interrupted_sends() == [f"send:{cid}"]
    done = wait_for(client, f"/api/campaign/{cid}/progress", "sending")
    assert done["status"] == "complete"
    assert sorted(s["to"] for s in fake_smtp) == ["a@acme.com", "b@beta.io"]
    assert automation.resume_interrupted_sends() == []  # nothing interrupted any more


def test_old_or_disabled_interruptions_are_not_resumed(client, fake_ai, fake_smtp):
    import automation

    cid = create_campaign(client, "a@acme.com")
    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")
    jobs.try_start_job(jobs.job_key("send", cid), "send", jobs.new_send_data(20, total=1))
    jobs._jobs.clear()
    jobs.mark_interrupted_jobs()

    assert automation.resume_interrupted_sends(now=datetime.now(UTC) + timedelta(days=2)) == []
    cfg = config.load_config()
    cfg["auto_resume_sends"] = False
    config.save_config(cfg)
    assert automation.resume_interrupted_sends() == []
    assert fake_smtp == []
