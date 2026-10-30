"""
Utility functions for the outreach tool.
Includes robust email list parsing.
"""

import re

# Personal/free mailbox providers: the domain says nothing about the employer
FREE_MAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "zoho.com",
    "yandex.com",
    "gmx.com",
    "mail.com",
    "rediffmail.com",
}

KNOWN_COMPANIES = {
    "google.com": "Google",
    "microsoft.com": "Microsoft",
    "amazon.com": "Amazon",
    "apple.com": "Apple",
    "meta.com": "Meta",
    "fb.com": "Meta",
}

# Second-level suffixes where the company label sits one level further left
_MULTI_PART_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "co.in",
    "org.in",
    "ac.in",
    "net.in",
    "com.au",
    "net.au",
    "co.jp",
    "co.nz",
    "com.br",
    "com.sg",
    "com.cn",
    "co.za",
    "com.mx",
    "co.kr",
}

# Mail-server style subdomains that aren't part of the company name
_GENERIC_SUBDOMAINS = {"mail", "email", "smtp", "mx", "corp", "hr", "careers", "jobs", "team", "www"}

_company_cache = {}


def resolve_company_name(domain: str, groq_api_key: str = None) -> str:
    """
    Best-effort company name from an email domain, resolved locally (no API calls).
      mail.stripe.com   -> Stripe
      acme-labs.co.uk   -> Acme Labs
      gmail.com         -> "" (personal address; employer unknown)
    """
    domain_clean = (domain or "").lower().strip().lstrip("@")
    if not domain_clean:
        return ""
    if domain_clean in _company_cache:
        return _company_cache[domain_clean]

    if domain_clean in FREE_MAIL_DOMAINS:
        name = ""
    elif domain_clean in KNOWN_COMPANIES:
        name = KNOWN_COMPANIES[domain_clean]
    else:
        labels = domain_clean.split(".")
        suffix_len = 2 if ".".join(labels[-2:]) in _MULTI_PART_SUFFIXES else 1
        candidates = [part for part in labels[:-suffix_len] if part not in _GENERIC_SUBDOMAINS]
        label = candidates[-1] if candidates else labels[0]
        name = re.sub(r"[-_]+", " ", label).title()
        if domain_clean.split(".")[-1] == "edu" or ".ac." in f".{domain_clean}":
            name = name.upper() if len(name) <= 5 else name

    _company_cache[domain_clean] = name
    return name


EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Pattern: Name <email>
ANGLE_BRACKET_PATTERN = re.compile(r"(.+?)\s*<\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\s*>")

# Pattern: Name - email
DASH_PATTERN = re.compile(r"(.+?)\s*-\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")


def parse_email_list(raw_text):
    """
    Parse a raw text block containing emails in various formats.

    Supported formats:
    - One per line
    - Comma-separated
    - Space-separated
    - Name <email>
    - Name - email
    - Mixed formats

    Returns a deduplicated list of {"email": str, "name": str|None} dicts.
    """
    if not raw_text or not raw_text.strip():
        return []

    results = []
    seen_emails = set()

    # Split on newlines first to process line by line
    lines = raw_text.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try angle bracket pattern: Name <email>
        angle_matches = ANGLE_BRACKET_PATTERN.findall(line)
        if angle_matches:
            for name, email in angle_matches:
                _add_entry(results, seen_emails, email.strip(), name.strip())
            continue

        # Try dash pattern: Name - email
        dash_matches = DASH_PATTERN.findall(line)
        if dash_matches:
            for name, email in dash_matches:
                _add_entry(results, seen_emails, email.strip(), name.strip())
            continue

        # Split on commas and spaces, then extract bare emails
        # Split on commas first
        segments = line.split(",")
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # Find all emails in this segment
            emails_found = EMAIL_REGEX.findall(segment)
            for email in emails_found:
                _add_entry(results, seen_emails, email, None)

    return results


def _add_entry(results, seen_emails, email, name):
    """Add an email entry if not already seen (case-insensitive dedup)."""
    email_lower = email.lower()
    if email_lower in seen_emails:
        return
    seen_emails.add(email_lower)

    # Clean up the name
    if name:
        name = name.strip().strip('"').strip("'").strip()
        if not name:
            name = None

    results.append({"email": email, "name": name})


# def extract_company_from_email(email):
#     """
#     Extract a company name from an email domain.
#     e.g., john@stripe.com → Stripe
#           hr@big-tech.io  → Big Tech
#     """
#     if not email or "@" not in email:
#         return "the company"

#     domain = email.split("@")[1]
#     # Remove TLD
#     company_part = domain.split(".")[0]
#     # Replace hyphens/underscores with spaces and title-case
#     company_name = company_part.replace("-", " ").replace("_", " ").title()
#     return company_name
