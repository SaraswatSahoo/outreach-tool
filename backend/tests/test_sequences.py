from datetime import UTC, datetime, timedelta

import automation
import db
from tests.helpers import create_campaign, generate_and_send, wait_for

NOW = datetime.now(UTC)


def _campaign_with_sends(client, emails="a@acme.com\nb@beta.io"):
    cid = create_campaign(client, emails)
    generate_and_send(client, cid)
    return cid


def _enable(client, cid, auto_send=False, steps=None):
    res = client.put(
        f"/api/campaign/{cid}/sequence",
        json={"enabled": True, "auto_send": auto_send, "steps": steps or [{"delay_days": 3}, {"delay_days": 5}]},
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _drafts(client, cid):
    return client.get(f"/api/campaign/{cid}/followup/preview").get_json()["followups"]


def test_sequence_validation(client, fake_ai, fake_smtp):
    cid = _campaign_with_sends(client)
    assert client.put(f"/api/campaign/{cid}/sequence", json={"steps": []}).status_code == 400
    assert client.put(f"/api/campaign/{cid}/sequence", json={"steps": [{"delay_days": 1}] * 4}).status_code == 400
    body = _enable(client, cid, steps=[{"delay_days": 0}, {"delay_days": 999}])
    assert body["sequence"] == [{"delay_days": 1}, {"delay_days": 60}]  # clamped


def test_sequence_drafts_when_due_and_only_once(client, fake_ai, fake_smtp):
    cid = _campaign_with_sends(client)
    _enable(client, cid)

    assert automation.run_sequences(now=NOW + timedelta(days=1))["drafted"] == 0  # too early
    assert automation.run_sequences(now=NOW + timedelta(days=4))["drafted"] == 2
    assert automation.run_sequences(now=NOW + timedelta(days=4))["drafted"] == 0  # drafts already waiting
    drafts = _drafts(client, cid)
    assert {d["step"] for d in drafts} == {1} and {d["source"] for d in drafts} == {"sequence"}

    dash = client.get("/api/dashboard").get_json()
    assert dash["pending_sequence_drafts"][0]["count"] == 2  # waiting for review (auto-send off)


def test_reply_stops_sequence(client, fake_ai, fake_smtp):
    cid = _campaign_with_sends(client)
    _enable(client, cid)
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    conn = db.get_db()
    conn.execute("UPDATE recipients SET replied_at = ? WHERE id = ?", (NOW.isoformat(), rid))
    conn.commit()
    conn.close()
    assert automation.run_sequences(now=NOW + timedelta(days=4))["drafted"] == 1


def test_auto_send_threads_steps_and_respects_delays(client, fake_ai, fake_smtp):
    cid = _campaign_with_sends(client, "a@acme.com")
    _enable(client, cid, auto_send=True)
    original_id = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["message_id"]

    fake_smtp.clear()
    s = automation.run_sequences(now=NOW + timedelta(days=4))
    assert s["drafted"] == 1 and s["sends_started"] == [cid]
    wait_for(client, f"/api/campaign/{cid}/followup/progress", "sending")
    first = fake_smtp[-1]
    assert first["references"] == [original_id] and first["reply_to"] == original_id

    # Step 2 is due 5 days after step 1 was sent (sent "now"), not after the original
    assert automation.run_sequences(now=NOW + timedelta(days=4))["drafted"] == 0
    s = automation.run_sequences(now=NOW + timedelta(days=6))
    assert s["drafted"] == 1
    wait_for(client, f"/api/campaign/{cid}/followup/progress", "sending")
    second = fake_smtp[-1]
    assert second["body"] == "Following up #2"
    assert len(second["references"]) == 2 and second["references"][0] == original_id
    assert second["reply_to"] == second["references"][1]

    # Sequence exhausted (2 steps)
    assert automation.run_sequences(now=NOW + timedelta(days=60))["drafted"] == 0
    r = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]
    assert r["follow_up_sent"] == 2

    # The second follow-up prompt saw the first one so it won't repeat it
    assert fake_ai["followup"][-1]["previous_followups"] == ["Following up #1"]
    assert fake_ai["followup"][-1]["step"] == 2


def test_reply_between_draft_and_send_cancels_sequence_followup(client, fake_ai, fake_smtp):
    cid = _campaign_with_sends(client, "a@acme.com")
    _enable(client, cid, auto_send=False)
    automation.run_sequences(now=NOW + timedelta(days=4))
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "interested"})

    fake_smtp.clear()
    client.post(f"/api/campaign/{cid}/followup/send")
    done = wait_for(client, f"/api/campaign/{cid}/followup/progress", "sending")
    assert fake_smtp == []
    assert done["log"][0]["status"] == "skipped"
    conn = db.get_db()
    assert conn.execute("SELECT status FROM followups").fetchone()["status"] == "cancelled"
    conn.close()


def test_manual_followups_allow_multiple_steps(client, fake_ai, fake_smtp):
    cid = _campaign_with_sends(client, "a@acme.com")
    for expected_step in (1, 2, 3):
        client.post(f"/api/campaign/{cid}/generate-followups", json={})
        wait_for(client, f"/api/campaign/{cid}/generate-followup-progress", "generating")
        assert [d["step"] for d in _drafts(client, cid)] == [expected_step]
        client.post(f"/api/campaign/{cid}/followup/send")
        wait_for(client, f"/api/campaign/{cid}/followup/progress", "sending")
    # MAX_FOLLOWUPS reached
    assert client.post(f"/api/campaign/{cid}/generate-followups", json={}).status_code == 400
    assert len(fake_smtp[-1]["references"]) == 3
