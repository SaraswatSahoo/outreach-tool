import config
import email_templates as T
from tests.helpers import wait_for

PROFILE = {
    "name": "Sam Lee",
    "college": "NIT Durgapur",
    "branch": "Electronics",
    "year": "3rd Year",
    "skills": "Python, React, SQL, Docker",
    "github": "https://github.com/sam",
    "linkedin": "https://www.linkedin.com/in/sam?utm_source=share_via&utm_medium=member_ios",
}


def test_every_template_fills_completely():
    for t in T.TEMPLATES:
        assert T.unknown_placeholders(t["subject"], t["body"]) == []
        out = T.fill(t["subject"], t["body"], {"email": "jane.doe@stripe.com"}, PROFILE)
        assert "{" not in out["subject"] + out["body"], t["id"]
        assert out["body"].startswith("Dear Jane,")
        assert len(out["body"].split()) < 130, t["id"]  # short enough to read on a phone


def test_company_and_name_sources():
    assert T.values_for({"email": "x@mail.stripe.com"}, {})["company"] == "Stripe"
    assert T.values_for({"email": "x@acme.io", "company": "Acme Robotics"}, {})["company"] == "Acme Robotics"
    assert T.values_for({"email": "x@gmail.com"}, {})["company"] == T.COMPANY_FALLBACK
    assert T.first_name({"email": "a@b.com", "name": "priya sharma"}) == "Priya"
    assert T.first_name({"email": "sprabakar@b.com"}) == ""  # can't tell first from last name
    assert T.first_name({"email": "hr.team@b.com"}) == ""  # role address, not a person


def test_profile_values_read_naturally():
    out = T.fill(
        "s", "I'm {my_name}, a {my_year} {my_branch} student.\n{my_skills}\n{my_links}", {"email": "a@b.com"}, PROFILE
    )["body"]
    assert "a 3rd-year Electronics student" in out
    assert "Python, React and SQL" in out  # top three
    assert "https://www.linkedin.com/in/sam | https://github.com/sam" in out  # tracking stripped


def test_missing_profile_fields_leave_no_gaps():
    body = (
        "I'm {my_name}, a {my_year} {my_branch} student ({my_branch}, {my_college}).\n\nThanks,\n{my_name}\n{my_links}"
    )
    out = T.fill("s", body, {"email": "a@b.com"}, {"name": "Sam", "branch": "Electronics"})["body"]
    assert out == "I'm Sam, an Electronics student (Electronics).\n\nThanks,\nSam"


def test_template_campaign_needs_no_ai_or_groq_key(client, fake_ai):
    cfg = config.load_config()
    cfg.update(groq_api_key="")
    config.save_config(cfg)
    t = T.TEMPLATES_BY_ID["internship_parttime"]
    res = client.post(
        "/api/campaign/new",
        json={
            "name": "Templated",
            "template_subject": t["subject"],
            "template_body": t["body"],
            "recipients": [{"email": "jane@sciohealth.com", "company": "Scio Health"}, {"email": "bob.k@stripe.com"}],
        },
    )
    cid = res.get_json()["campaign_id"]
    assert client.post(f"/api/campaign/{cid}/generate").status_code == 200
    assert wait_for(client, f"/api/campaign/{cid}/generate-progress", "generating")["status"] == "complete"
    assert fake_ai["email"] == []

    recipients = {r["email"]: r for r in client.get(f"/api/campaign/{cid}").get_json()["recipients"]}
    assert recipients["jane@sciohealth.com"]["subject"] == "Internship / Part-Time Opportunity at Scio Health"
    assert recipients["bob.k@stripe.com"]["email_body"].startswith("Dear Bob,")

    rid = recipients["bob.k@stripe.com"]["id"]
    regen = client.post(f"/api/campaign/{cid}/recipient/{rid}/regenerate").get_json()
    assert regen["subject"] == "Internship / Part-Time Opportunity at Stripe"

    campaign = client.get(f"/api/campaign/{cid}").get_json()
    assert campaign["goal"]  # follow-ups still have something to work from


def test_template_validation(client):
    base = {"name": "T", "email_list": "a@acme.com"}
    res = client.post("/api/campaign/new", json={**base, "template_subject": "Hi", "template_body": "Hi {compnay}"})
    assert res.status_code == 400 and "{compnay}" in res.get_json()["error"]
    res = client.post("/api/campaign/new", json={**base, "template_subject": "Hi", "template_body": ""})
    assert res.status_code == 400


def test_template_endpoints(client):
    data = client.get("/api/templates").get_json()
    assert {t["id"] for t in data["templates"]} >= {"internship_parttime", "fulltime_formal"}
    assert "company" in data["placeholders"]

    preview = client.post(
        "/api/templates/preview",
        json={"subject": "At {company}", "body": "Hi {first_name}, {oops}", "recipient": {"email": "jo@acme.com"}},
    ).get_json()
    assert preview["subject"] == "At Acme" and preview["unknown"] == ["oops"]

    companies = client.post("/api/templates/companies", json={"emails": ["a@stripe.com", "b@gmail.com"]}).get_json()
    assert companies["companies"] == {"gmail.com": "", "stripe.com": "Stripe"}
    assert client.get("/api/campaigns").get_json() == []  # nothing saved


def test_company_overrides_apply_per_domain(client):
    t = T.TEMPLATES_BY_ID["fulltime_formal"]
    res = client.post(
        "/api/campaign/new",
        json={
            "name": "T",
            "template_subject": t["subject"],
            "template_body": t["body"],
            "email_list": "Asha <asha@sciohealthanalytics.com>\nraj@SCIOHEALTHANALYTICS.com\nx@stripe.com",
            "company_overrides": {"sciohealthanalytics.com": "Scio Health", "stripe.com": "  "},
        },
    )
    cid = res.get_json()["campaign_id"]
    companies = {r["email"]: r["company"] for r in client.get(f"/api/campaign/{cid}").get_json()["recipients"]}
    assert companies == {
        "asha@sciohealthanalytics.com": "Scio Health",
        "raj@SCIOHEALTHANALYTICS.com": "Scio Health",
        "x@stripe.com": None,  # blank override ignored; the domain guess is used when filling
    }


def test_greeting_is_formal_with_or_without_a_name():
    assert T.values_for({"email": "jane.doe@acme.com"}, {})["greeting"] == "Dear Jane,"
    assert T.values_for({"email": "x@acme.com", "name": "Ravi Kumar"}, {})["greeting"] == "Dear Ravi,"
    assert T.values_for({"email": "sprabakar@acme.com"}, {})["greeting"] == "Dear Hiring Manager,"
