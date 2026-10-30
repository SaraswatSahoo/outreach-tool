import time


def wait_for(client, url, running_status, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(url).get_json()
        if data["status"] != running_status:
            return data
        time.sleep(0.02)
    raise AssertionError(f"{url} still {running_status} after {timeout}s")


def create_campaign(client, emails="a@acme.com\nb@beta.io\nc@gamma.dev"):
    res = client.post("/api/campaign/new", json={"name": "Test", "goal": "Internship", "email_list": emails})
    assert res.status_code == 200, res.get_json()
    return res.get_json()["campaign_id"]


def generate_and_send(client, cid):
    assert client.post(f"/api/campaign/{cid}/generate").status_code == 200
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")
    assert client.post(f"/api/campaign/{cid}/send").status_code == 200
    return wait_for(client, f"/api/campaign/{cid}/progress", "sending")
