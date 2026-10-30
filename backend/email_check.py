"""
Email address checking before sending, to cut down failed sends and bounces.

1. Built-in checks (free, local, no limits). Only DNS lookups are made; the
   tool never talks to anyone's mail server, so there's no blacklisting risk.
     - format, known typo domains (gmial.com), domain exists / can receive mail,
       disposable domains, addresses that bounced for you before, blocked domains
2. Optional verification service (Reoon / Hunter / ZeroBounce / Abstract API)
   with the user's own API key, for addresses that pass step 1. The service
   checks whether the mailbox exists from its own servers. Results are cached
   for 30 days so free credits are never spent twice on the same address.

Verdicts: "valid" (keep), "risky" (keep, but flag), "invalid" (remove).
Only certain problems are "invalid"; anything uncertain is "risky".
"""

import difflib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from constants import BASE_DIR

VALID, RISKY, INVALID = "valid", "risky", "invalid"
SERVICE_CACHE_DAYS = 30


@dataclass
class CheckResult:
    email: str
    verdict: str
    reason: str
    suggestion: str | None = None
    source: str = "built-in"

    def to_dict(self):
        return asdict(self)


# ─── Format ───────────────────────────────────────────────────────────────────

_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def format_problem(email):
    """A human-readable reason the address is malformed, or None if it's well-formed."""
    if email.count("@") != 1:
        return "not a valid email address"
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or len(email) > 254:
        return "not a valid email address"
    if not _LOCAL_RE.match(local):
        return "invalid characters before the @"
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(label) for label in labels):
        return "invalid domain after the @"
    if not re.fullmatch(r"[A-Za-z]{2,63}", labels[-1]):
        return "invalid domain ending"
    return None


# ─── Typos ────────────────────────────────────────────────────────────────────

POPULAR_DOMAINS = [
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "yahoo.co.uk",
    "outlook.com",
    "outlook.in",
    "hotmail.com",
    "hotmail.co.uk",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "zoho.com",
    "gmx.com",
    "rediffmail.com",
    "yandex.com",
]

# Common misspellings: certain enough to remove (with a one-click fix)
KNOWN_TYPOS = {
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "gmail.cmo": "gmail.com",
    "gmail.om": "gmail.com",
    "gmail.comm": "gmail.com",
    "gmeil.com": "gmail.com",
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "yahoo.con": "yahoo.com",
    "yhaoo.com": "yahoo.com",
    "hotmial.com": "hotmail.com",
    "hotmal.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "hotmail.con": "hotmail.com",
    "outlok.com": "outlook.com",
    "outllook.com": "outlook.com",
    "outlook.con": "outlook.com",
    "iclod.com": "icloud.com",
    "icloud.con": "icloud.com",
}

_TLD_TYPOS = {"con": "com", "cmo": "com", "comm": "com", "ocm": "com", "vom": "com", "xom": "com"}


def typo_suggestion(domain):
    """(suggested_domain, certain) for a likely misspelt domain, else (None, False)."""
    domain = domain.lower()
    if domain in KNOWN_TYPOS:
        return KNOWN_TYPOS[domain], True
    base, _, tld = domain.rpartition(".")
    if tld in _TLD_TYPOS and base:
        return f"{base}.{_TLD_TYPOS[tld]}", True
    if domain in POPULAR_DOMAINS:
        return None, False
    close = difflib.get_close_matches(domain, POPULAR_DOMAINS, n=1, cutoff=0.88)
    return (close[0], False) if close else (None, False)  # fuzzy: suggest, never remove


# ─── Disposable domains and role addresses ───────────────────────────────────

_disposable = None


def disposable_domains():
    global _disposable
    if _disposable is None:
        path = os.path.join(BASE_DIR, "data", "disposable_domains.txt")
        try:
            with open(path, encoding="utf-8") as f:
                _disposable = {line.strip() for line in f if line.strip() and not line.startswith("#")}
        except OSError:
            _disposable = set()
    return _disposable


NO_REPLY_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster")
ROLE_PREFIXES = (
    "info",
    "hr",
    "careers",
    "career",
    "jobs",
    "job",
    "admin",
    "contact",
    "support",
    "sales",
    "hello",
    "team",
    "office",
    "recruiting",
    "recruitment",
    "talent",
    "hiring",
    "help",
    "enquiries",
    "inquiries",
)


# ─── DNS ──────────────────────────────────────────────────────────────────────

DOMAIN_OK, DOMAIN_MISSING, DOMAIN_NO_MAIL, DOMAIN_UNKNOWN = "ok", "missing", "no_mail", "unknown"


def dns_domain_status(domain, timeout=4.0):
    """Whether a domain exists and can receive email, using MX (or A/AAAA fallback) records."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    try:
        answer = resolver.resolve(domain, "MX")
        records = list(answer)
        # RFC 7505 "null MX": the domain explicitly accepts no email
        if len(records) == 1 and str(records[0].exchange) in (".", ""):
            return DOMAIN_NO_MAIL
        return DOMAIN_OK
    except dns.resolver.NXDOMAIN:
        return DOMAIN_MISSING
    except dns.resolver.NoAnswer:
        # No MX: mail falls back to the domain's own address record (RFC 5321)
        for rtype in ("A", "AAAA"):
            try:
                resolver.resolve(domain, rtype)
                return DOMAIN_OK
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                continue
            except dns.exception.DNSException:
                return DOMAIN_UNKNOWN
        return DOMAIN_NO_MAIL
    except dns.exception.DNSException:
        return DOMAIN_UNKNOWN


# ─── History (bounces, block list) ───────────────────────────────────────────


def _history(conn, emails):
    lowered = [e.lower() for e in emails]
    bounced, blocked = set(), set()
    if lowered:
        marks = ",".join("?" * len(lowered))
        bounced = {
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT LOWER(email) FROM recipients WHERE reply_status = 'invalid_email' "
                f"AND LOWER(email) IN ({marks})",
                lowered,
            )
        }
    blocked = {row[0] for row in conn.execute("SELECT domain FROM blocked_domains")}
    return bounced, blocked


# ─── Built-in checks ──────────────────────────────────────────────────────────


def builtin_checks(emails, conn, domain_status=None, max_workers=16):
    """Run every free local check. Returns {email: CheckResult} in input order."""
    domain_status = domain_status or dns_domain_status
    bounced, blocked = _history(conn, emails)
    results, need_dns = {}, {}

    for email in emails:
        problem = format_problem(email)
        if problem:
            results[email] = CheckResult(email, INVALID, problem)
            continue
        local, domain = email.rsplit("@", 1)
        domain_l, local_l = domain.lower(), local.lower()
        suggestion, certain = typo_suggestion(domain_l)
        if suggestion and certain:
            results[email] = CheckResult(email, INVALID, f"looks like a typo of {suggestion}", f"{local}@{suggestion}")
        elif email.lower() in bounced:
            results[email] = CheckResult(email, INVALID, "bounced when you emailed it before")
        elif domain_l in blocked:
            results[email] = CheckResult(email, INVALID, "domain is on your block list")
        elif domain_l in disposable_domains():
            results[email] = CheckResult(email, INVALID, "disposable (throwaway) email address")
        elif local_l.split("+")[0] in NO_REPLY_PREFIXES:
            results[email] = CheckResult(email, INVALID, "no-reply address — nobody reads it")
        else:
            results[email] = None  # decided after DNS
            need_dns.setdefault(domain_l, []).append(email)
            if suggestion:
                results[email] = CheckResult(email, RISKY, f"did you mean {suggestion}?", f"{local}@{suggestion}")

    # One lookup per domain, in parallel
    statuses = {}
    if need_dns:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(need_dns))) as pool:
            for domain, status in zip(need_dns, pool.map(domain_status, need_dns), strict=True):
                statuses[domain] = status

    for domain, addresses in need_dns.items():
        status = statuses[domain]
        for email in addresses:
            if status == DOMAIN_MISSING:
                results[email] = CheckResult(email, INVALID, f"the domain {domain} doesn't exist")
            elif status == DOMAIN_NO_MAIL:
                results[email] = CheckResult(email, INVALID, f"{domain} can't receive email")
            elif results[email] is not None:
                continue  # keep the fuzzy-typo "risky" result
            elif status == DOMAIN_UNKNOWN:
                results[email] = CheckResult(email, RISKY, "couldn't check the domain right now")
            elif email.rsplit("@", 1)[0].lower().split("+")[0] in ROLE_PREFIXES:
                results[email] = CheckResult(email, RISKY, "shared inbox (e.g. hr@, info@) — often fine for outreach")
            else:
                results[email] = CheckResult(email, VALID, "domain accepts email")
    return results


# ─── Verification services ────────────────────────────────────────────────────


class ServiceError(Exception):
    """A provider call failed. `fatal` errors (bad key, out of credits) stop further calls."""

    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


def http_get_json(url, params, timeout=60):
    """GET a JSON API. Returns (status_code, parsed_json_or_None)."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"Accept": "application/json", "User-Agent": "outreach-tool"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode("utf-8", "replace"), e.code
    except OSError as e:
        raise ServiceError(f"couldn't reach the service ({e})") from e
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, None


def _raise_for_http(status, data, message_paths):
    if status in (401, 403):
        raise ServiceError("API key rejected — check it in Settings", fatal=True)
    if status in (402, 429):
        raise ServiceError("out of free credits or rate-limited", fatal=True)
    if status >= 400 or data is None:
        detail = next((m for m in (_dig(data, path) for path in message_paths) if isinstance(m, str)), None)
        raise ServiceError(detail or f"service error (HTTP {status})")


def _dig(data, path):
    """Follow keys through nested dicts; a list along the way means 'its first item'."""
    value = data
    for key in path:
        if isinstance(value, list):
            value = value[0] if value else None
        value = value.get(key) if isinstance(value, dict) else None
    return value


class Reoon:
    name, label = "reoon", "Reoon"
    signup = "https://emailverifier.reoon.com/"

    def verify(self, email, key, http):
        status, data = http(
            "https://emailverifier.reoon.com/api/v1/verify", {"email": email, "key": key, "mode": "power"}
        )
        _raise_for_http(status, data, [("reason",), ("message",)])
        value = str(data.get("status", "")).lower()
        if value == "error" or not value:
            reason = str(data.get("reason") or data.get("message") or "service error")
            raise ServiceError(reason, fatal="credit" in reason.lower() or "key" in reason.lower())
        if value == "safe":
            return VALID, "mailbox exists"
        if value in ("invalid", "disabled", "disposable", "spamtrap"):
            return INVALID, {
                "invalid": "mailbox doesn't exist",
                "disabled": "mailbox is disabled",
                "disposable": "disposable address",
                "spamtrap": "spam trap address",
            }[value]
        return RISKY, {
            "catch_all": "domain accepts all addresses (can't confirm)",
            "role_account": "shared inbox",
            "inbox_full": "inbox is full",
            "unknown": "service couldn't tell",
        }.get(value, value)

    def credits(self, key, http):
        status, data = http("https://emailverifier.reoon.com/api/v1/check-account-balance/", {"key": key})
        _raise_for_http(status, data, [("reason",)])
        return (data.get("remaining_daily_credits") or 0) + (data.get("remaining_instant_credits") or 0)


class Hunter:
    name, label = "hunter", "Hunter"
    signup = "https://hunter.io/users/sign_up"

    def verify(self, email, key, http, polls=5, wait=2.0):
        for _ in range(polls):
            status, data = http("https://api.hunter.io/v2/email-verifier", {"email": email, "api_key": key})
            if status != 202:  # 202 = still verifying; asking again is free
                break
            time.sleep(wait)
        else:
            return RISKY, "service still verifying — try again later"
        _raise_for_http(status, data, [("errors", "details"), ("errors", "id")])
        value = str((data.get("data") or {}).get("status", "")).lower()
        if value == "valid":
            return VALID, "mailbox exists"
        if value in ("invalid", "disposable"):
            return INVALID, "mailbox doesn't exist" if value == "invalid" else "disposable address"
        return RISKY, {
            "accept_all": "domain accepts all addresses (can't confirm)",
            "webmail": "personal webmail address",
            "unknown": "service couldn't tell",
        }.get(value, value or "service couldn't tell")

    def credits(self, key, http):
        status, data = http("https://api.hunter.io/v2/account", {"api_key": key})
        _raise_for_http(status, data, [("errors", "details")])
        requests = (data.get("data") or {}).get("requests") or {}
        for bucket in ("verifications", "credits"):
            b = requests.get(bucket)
            if isinstance(b, dict) and "available" in b:
                return max(0, (b.get("available") or 0) - (b.get("used") or 0))
        return None


class ZeroBounce:
    name, label = "zerobounce", "ZeroBounce"
    signup = "https://www.zerobounce.net/members/signup"

    def verify(self, email, key, http):
        status, data = http(
            "https://api.zerobounce.net/v2/validate", {"api_key": key, "email": email, "ip_address": ""}
        )
        _raise_for_http(status, data, [("error",), ("message",)])
        if data.get("error"):
            message = str(data["error"])
            raise ServiceError(message, fatal="key" in message.lower() or "credit" in message.lower())
        value = str(data.get("status", "")).lower()
        if value == "valid":
            return VALID, "mailbox exists"
        if value in ("invalid", "spamtrap", "abuse", "do_not_mail"):
            return INVALID, {
                "invalid": "mailbox doesn't exist",
                "spamtrap": "spam trap address",
                "abuse": "known complainer address",
                "do_not_mail": "do-not-mail address",
            }[value]
        return RISKY, {
            "catch-all": "domain accepts all addresses (can't confirm)",
            "unknown": "service couldn't tell",
        }.get(value, value or "service couldn't tell")

    def credits(self, key, http):
        status, data = http("https://api.zerobounce.net/v2/getcredits", {"api_key": key})
        _raise_for_http(status, data, [("error",)])
        value = int(str(data.get("Credits", data.get("credits", "-1"))))
        if value < 0:
            raise ServiceError("API key rejected — check it in Settings", fatal=True)
        return value


class AbstractApi:
    name, label = "abstract", "Abstract API"
    signup = "https://www.abstractapi.com/api/email-verification-validation-api"

    def verify(self, email, key, http):
        status, data = http("https://emailvalidation.abstractapi.com/v1/", {"api_key": key, "email": email})
        _raise_for_http(status, data, [("error", "message"), ("error", "details")])
        value = str(data.get("deliverability", "")).upper()
        if value == "DELIVERABLE":
            return VALID, "mailbox exists"
        if value == "UNDELIVERABLE":
            return INVALID, "mailbox doesn't exist"
        return RISKY, "service couldn't confirm" if value == "RISKY" else "service couldn't tell"

    def credits(self, key, http):
        return None  # no balance endpoint


PROVIDERS = {p.name: p for p in (Reoon(), Hunter(), ZeroBounce(), AbstractApi())}


# ─── Cache ────────────────────────────────────────────────────────────────────


def cached_service_results(conn, emails, provider):
    if not emails:
        return {}
    cutoff = (datetime.now(UTC) - timedelta(days=SERVICE_CACHE_DAYS)).isoformat()
    marks = ",".join("?" * len(emails))
    rows = conn.execute(
        f"SELECT email, verdict, reason FROM email_checks WHERE source = ? AND checked_at >= ? AND email IN ({marks})",
        [provider, cutoff, *[e.lower() for e in emails]],
    ).fetchall()
    return {row["email"]: (row["verdict"], row["reason"]) for row in rows}


def store_service_result(conn, email, provider, verdict, reason):
    conn.execute(
        "INSERT OR REPLACE INTO email_checks (email, verdict, reason, source, checked_at) VALUES (?, ?, ?, ?, ?)",
        (email.lower(), verdict, reason, provider, datetime.now(UTC).isoformat()),
    )
    conn.commit()


# ─── Full pipeline ────────────────────────────────────────────────────────────


def verify_emails(emails, config, conn, progress=None, domain_status=None, http=None):
    """
    Built-in checks for every address, then (if configured) the verification
    service for addresses that passed. `progress(done, total)` is called as it goes.
    Returns a list of CheckResult in input order.
    """
    domain_status = domain_status or dns_domain_status  # looked up at call time, so tests can patch them
    http = http or http_get_json
    emails = list(dict.fromkeys(e.strip() for e in emails if e and e.strip()))  # de-dupe, keep order
    total = len(emails)
    results = builtin_checks(emails, conn, domain_status=domain_status)
    if progress:
        progress(0, total)

    provider = PROVIDERS.get(config.get("verify_provider") or "")
    key = (config.get("verify_api_key") or "").strip()
    use_service = bool(provider and key and config.get("verify_with_service", True))
    candidates = [e for e in emails if results[e].verdict != INVALID] if use_service else []

    done = total - len(candidates)
    if progress:
        progress(done, total)
    if not candidates:
        return [results[e] for e in emails]

    cached = cached_service_results(conn, candidates, provider.name)
    stop_reason = None
    for email in candidates:
        if email.lower() in cached:
            verdict, reason = cached[email.lower()]
            results[email] = CheckResult(email, verdict, reason, results[email].suggestion, provider.name)
        elif stop_reason:
            results[email] = _not_checked(results[email], provider, stop_reason)
        else:
            try:
                verdict, reason = provider.verify(email, key, http)
                store_service_result(conn, email, provider.name, verdict, reason)
                results[email] = CheckResult(email, verdict, reason, results[email].suggestion, provider.name)
            except ServiceError as e:
                results[email] = _not_checked(results[email], provider, str(e))
                if e.fatal:
                    stop_reason = str(e)  # don't burn requests on a bad key / empty quota
        done += 1
        if progress:
            progress(done, total)
    return [results[e] for e in emails]


def _not_checked(builtin, provider, why):
    """Keep the address when the service can't answer — never remove on uncertainty."""
    return CheckResult(
        builtin.email, RISKY, f"mailbox not checked ({provider.label}: {why})", builtin.suggestion, "built-in"
    )
