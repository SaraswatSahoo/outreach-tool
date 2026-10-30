from tests.helpers import create_campaign, generate_and_send, wait_for


def test_create_from_structured_rows_passes_fields_to_ai(client, fake_ai, fake_smtp):
    res = client.post(
        "/api/campaign/new",
        json={
            "name": "CSV",
            "goal": "Internship",
            "recipients": [
                {
                    "email": "Jane <jane@acme.com>",
                    "name": "Jane",
                    "company": "Acme Robotics",
                    "role": "CTO",
                    "notes": "Loved her talk on SLAM",
                },
                {"email": "jane@acme.com", "name": "dup"},  # duplicate (case-insensitive)
                {"email": "not-an-email", "name": "x"},  # invalid
                {"email": "bob@beta.io"},
            ],
        },
    )
    body = res.get_json()
    assert res.status_code == 200 and body["recipients_count"] == 2
    cid = body["campaign_id"]

    client.post(f"/api/campaign/{cid}/generate")
    wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")
    jane = next(c["recipient"] for c in fake_ai["email"] if c["recipient"]["email"] == "jane@acme.com")
    assert jane == {
        "email": "jane@acme.com",
        "name": "Jane",
        "company": "Acme Robotics",
        "role": "CTO",
        "notes": "Loved her talk on SLAM",
    }


def test_pipeline_lists_sent_contacts(client, fake_ai, fake_smtp):
    cid = create_campaign(client, "a@acme.com\nb@beta.io")
    generate_and_send(client, cid)
    create_campaign(client, "c@gamma.dev")  # drafts only — not in the pipeline
    rid = client.get(f"/api/campaign/{cid}").get_json()["recipients"][0]["id"]
    client.patch(f"/api/recipient/{rid}/status", json={"reply_status": "interested"})

    cards = client.get("/api/pipeline").get_json()["recipients"]
    assert {c["email"] for c in cards} == {"a@acme.com", "b@beta.io"}
    assert {c["email"]: c["reply_status"] for c in cards}["a@acme.com"] == "interested"
    assert len(client.get(f"/api/pipeline?campaign_id={cid + 999}").get_json()["recipients"]) == 0


def _xlsx_bytes(rows):
    import io

    from openpyxl import Workbook

    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_xlsx_import_returns_rows(client):
    import io

    data = _xlsx_bytes(
        [
            ["Email", "Name", "Company", "Employees"],
            ["jane@acme.com", "Jane", "Acme", 250.0],
            [None, None, None, None],
            ["bob@beta.io", "Bob"],
        ]
    )
    res = client.post("/api/import/xlsx", data={"file": (data, "contacts.xlsx")}, content_type="multipart/form-data")
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["rows"] == [
        ["Email", "Name", "Company", "Employees"],
        ["jane@acme.com", "Jane", "Acme", "250"],
        ["bob@beta.io", "Bob", "", ""],
    ]

    bad = client.post(
        "/api/import/xlsx", data={"file": (io.BytesIO(b"not a zip"), "x.xlsx")}, content_type="multipart/form-data"
    )
    assert bad.status_code == 400
    wrong = client.post(
        "/api/import/xlsx", data={"file": (io.BytesIO(b"a,b"), "x.csv")}, content_type="multipart/form-data"
    )
    assert wrong.status_code == 400
