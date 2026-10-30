from utils import parse_email_list, resolve_company_name


def test_parse_mixed_formats_and_dedup():
    raw = """john@company.com
    jane.doe@startup.io, mark@techcorp.com
    Alice <alice@acme.com>
    Bob Smith - bob@smith.com
    JOHN@company.com"""
    parsed = parse_email_list(raw)
    assert [p["email"] for p in parsed] == [
        "john@company.com",
        "jane.doe@startup.io",
        "mark@techcorp.com",
        "alice@acme.com",
        "bob@smith.com",
    ]
    assert parsed[3]["name"] == "Alice"
    assert parsed[4]["name"] == "Bob Smith"


def test_parse_empty():
    assert parse_email_list("") == []
    assert parse_email_list("no emails here") == []


def test_company_names():
    assert resolve_company_name("stripe.com") == "Stripe"
    assert resolve_company_name("mail.stripe.com") == "Stripe"
    assert resolve_company_name("acme-labs.co.uk") == "Acme Labs"
    assert resolve_company_name("google.com") == "Google"
    assert resolve_company_name("gmail.com") == ""
    assert resolve_company_name("") == ""


def test_dotenv_loader(tmp_path, monkeypatch):
    import os

    from constants import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        '# comment\nexport OUTREACH_T1="quoted value"\nOUTREACH_T2=plain\nOUTREACH_T3=from-file\n\nnot a line\n'
    )
    monkeypatch.setenv("OUTREACH_T3", "from-env")
    monkeypatch.delenv("OUTREACH_T1", raising=False)
    monkeypatch.delenv("OUTREACH_T2", raising=False)
    load_dotenv(str(env))
    assert os.environ["OUTREACH_T1"] == "quoted value"
    assert os.environ["OUTREACH_T2"] == "plain"
    assert os.environ["OUTREACH_T3"] == "from-env"  # real environment wins
    monkeypatch.delenv("OUTREACH_T1")
    monkeypatch.delenv("OUTREACH_T2")
