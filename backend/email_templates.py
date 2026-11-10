"""
Fixed cold-email templates. Placeholders are filled per recipient with no AI call:
the company comes from the recipient's company field or their email domain, the
sender details from the Profile page.
"""

import re

from email_check import ROLE_PREFIXES
from utils import resolve_company_name

# Placeholder -> what it becomes (shown in the editor)
PLACEHOLDERS = {
    "company": "Company name (from the email domain, e.g. stripe.com -> Stripe)",
    "greeting": "\"Dear Jane,\" or \"Dear Hiring Manager,\" when the name is unknown",
    "first_name": "Recipient's first name, or \"there\" when unknown",
    "my_name": "Your name",
    "my_college": "Your college",
    "my_branch": "Your branch / major",
    "my_year": "Your year of study",
    "my_skills": "Your top 3 skills",
    "my_links": "Your GitHub and LinkedIn links",
}

COMPANY_FALLBACK = "your company"
NAME_FALLBACK = "there"
GREETING_FALLBACK = "Dear Hiring Manager,"

TEMPLATES = [
    {
        "id": "internship_parttime",
        "label": "Internship / Part-Time",
        "hint": "Formal. For internships and part-time roles.",
        "goal": "Formally ask about internship or part-time opportunities at the company, "
        "or to be directed to the appropriate person.",
        "subject": "Internship / Part-Time Opportunity at {company}",
        "body": (
            "{greeting}\n\n"
            "I am {my_name}, a {my_year} student at {my_college}. I am writing to express my interest in an "
            "internship or part-time opportunity at {company}.\n\n"
            "I have hands-on experience with {my_skills}, and I am eager to apply these skills to real projects "
            "while learning from your team. I have attached my resume for your consideration.\n\n"
            "I would be grateful for the opportunity to discuss how I could contribute to {company}. "
            "If this does not fall under your purview, I would appreciate it if you could direct me to "
            "the appropriate person.\n\n"
            "Thank you for your time and consideration.\n\n"
            "Sincerely,\n{my_name}"
        ),
    },
    {
        "id": "fulltime_formal",
        "label": "Full-Time",
        "hint": "Formal. For full-time roles.",
        "goal": "Formally ask about full-time opportunities at the company, "
        "or for the application to be forwarded to the person who handles hiring.",
        "subject": "Full-Time Opportunity at {company}",
        "body": (
            "{greeting}\n\n"
            "I am {my_name}, a {my_year} student at {my_college}, and I am writing to express my interest in "
            "full-time opportunities at {company}.\n\n"
            "I bring hands-on experience with {my_skills}, and I am confident in my ability to contribute "
            "effectively from the outset. My resume is attached for your review.\n\n"
            "I would welcome the opportunity to discuss any suitable openings on your team. If hiring is handled "
            "by a colleague, I would be grateful if you could forward my application to them.\n\n"
            "Thank you for your time and consideration.\n\n"
            "Yours sincerely,\n{my_name}"
        ),
    },
]

TEMPLATES_BY_ID = {t["id"]: t for t in TEMPLATES}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_ARTICLE_BEFORE_PLACEHOLDERS = re.compile(r"\b([Aa]n?) ((?:\{[a-z_]+\} ?)+)")


def unknown_placeholders(*texts):
    """Placeholders in the texts that this module doesn't know how to fill."""
    return sorted({m for text in texts for m in _PLACEHOLDER.findall(text or "") if m not in PLACEHOLDERS})


def first_name(recipient):
    """First name from the recipient's name, or from an address like jane.doe@ (not jdoe@ or hr@)."""
    name = (recipient.get("name") or "").strip()
    if name:
        token = name.split()[0].strip(",")
        return token.capitalize() if token.islower() else token
    local = (recipient.get("email") or "").split("@")[0].lower()
    parts = re.split(r"[._-]", local)
    if len(parts) >= 2 and parts[0].isalpha() and len(parts[0]) >= 2 and parts[0] not in ROLE_PREFIXES:
        return parts[0].capitalize()
    return ""


def _skills(text):
    skills = [s.strip() for s in re.split(r"[,;\n]", text or "") if s.strip()][:3]
    if len(skills) <= 1:
        return "".join(skills)
    return f"{', '.join(skills[:-1])} and {skills[-1]}"


def _year(text):
    text = (text or "").strip()
    # "3rd Year" -> "3rd-year" so it reads "a 3rd-year CSE student"
    return re.sub(r"\s+year$", "-year", text, flags=re.I).lower() if re.search(r"\byear$", text, re.I) else text


def values_for(recipient, profile):
    profile = profile or {}
    domain = (recipient.get("email") or "@").split("@")[-1]
    # Share links often carry tracking parameters (?utm_source=...) that look spammy
    links = [(profile.get(k) or "").strip().split("?")[0].rstrip("/") for k in ("linkedin", "github")]
    return {
        "company": (recipient.get("company") or "").strip() or resolve_company_name(domain) or COMPANY_FALLBACK,
        "greeting": f"Dear {name}," if (name := first_name(recipient)) else GREETING_FALLBACK,
        "first_name": first_name(recipient) or NAME_FALLBACK,
        "my_name": (profile.get("name") or "").strip(),
        "my_college": (profile.get("college") or "").strip(),
        "my_branch": (profile.get("branch") or "").strip(),
        "my_year": _year(profile.get("year")),
        "my_skills": _skills(profile.get("skills")),
        "my_links": " | ".join(link for link in links if link),
    }


def _tidy(text):
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.!?)])", r"\1", text)
    text = re.sub(r"\(\s*,\s*", "(", text)
    text = re.sub(r"\s*,\s*\)", ")", text).replace(" ()", "").replace("()", "")
    return text


def fill(subject, body, recipient, profile):
    """The finished subject and body for one recipient."""
    values = values_for(recipient, profile)

    def fill_placeholders(text):
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    def fix_article(m):
        # "a {my_year} {my_branch}" -> "an Electronics" when the year is blank
        filled = fill_placeholders(m.group(2))
        article = "an" if filled.strip()[:1].lower() in tuple("aeiou") else "a"
        return f"{article.capitalize() if m.group(1)[0].isupper() else article} {filled}"

    def sub(text):
        return fill_placeholders(_ARTICLE_BEFORE_PLACEHOLDERS.sub(fix_article, text or ""))

    lines = []
    for line in (body or "").split("\n"):
        filled = sub(line)
        # A line that was only placeholders with nothing to fill (e.g. no links) is dropped
        if line.strip() and not filled.strip():
            continue
        lines.append(_tidy(filled).rstrip())
    body_out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return {"subject": _tidy(sub(subject)).strip(), "body": body_out}
