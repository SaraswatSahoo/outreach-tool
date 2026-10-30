"""
Automatic reply detection.

Scans the Gmail inbox for replies from people we emailed and records them:
  1. A reply is matched to a recipient by its In-Reply-To / References headers
     (which contain the Message-ID we sent), or failing that by sender address.
  2. The quoted history is stripped and the new text is classified with Groq
     (interested / check back / no openings / rejection / interview).
  3. Every reply is logged in the `replies` table; the recipient's reply_status
     is only changed if it hasn't been set by hand. Any reply sets replied_at,
     which stops automatic follow-up sequences.
"""

import email
import re
from datetime import UTC, date, datetime, timedelta
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from constants import AUTO_UPDATABLE_STATUSES
from llm import DEFAULT_TEXT_MODELS, json_chat, text_models

LOOKBACK_DAYS = 30
MAX_MESSAGES_PER_RUN = 300
HEADER_FIELDS = "FROM IN-REPLY-TO REFERENCES MESSAGE-ID SUBJECT DATE AUTO-SUBMITTED"

# Statuses the classifier may assign
CLASSIFIABLE_STATUSES = ("interested", "check_back", "no_openings", "final_rejection", "interview_scheduled")

_MSGID_RE = re.compile(r"<[^<>\s]+>")


# ─── Pure helpers (unit-tested) ───────────────────────────────────────────────


def normalize_msgid(value):
    return value.strip().lower() if value else ""


def referenced_ids(headers):
    """All Message-IDs a message refers to via In-Reply-To and References."""
    ids = []
    for field in ("In-Reply-To", "References"):
        ids += _MSGID_RE.findall(headers.get(field, "") or "")
    return [normalize_msgid(i) for i in ids]


def decode_mime_header(value):
    parts = []
    for part, enc in decode_header(value or ""):
        if isinstance(part, bytes):
            parts.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


def is_automated(headers):
    """Auto-replies (out-of-office, ticket systems) are handled elsewhere, not as human replies."""
    auto = (headers.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    subject = decode_mime_header(headers.get("Subject", "")).lower()
    return any(
        k in subject
        for k in (
            "out of office",
            "auto-reply",
            "auto reply",
            "automatic reply",
            "undeliverable",
            "delivery status notification",
        )
    )


def match_reply(headers, known_ids, sent_addresses, own_address):
    """
    Decide which recipient (if any) a message is a reply from.
    known_ids: {normalized Message-ID we sent: recipient_id}
    sent_addresses: {lowercased recipient address: recipient_id}
    Returns (recipient_id, how) or (None, None).
    """
    from_addr = parseaddr(headers.get("From", ""))[1].lower()
    if not from_addr or from_addr == (own_address or "").lower() or is_automated(headers):
        return None, None
    for ref in referenced_ids(headers):
        if ref in known_ids:
            return known_ids[ref], "thread"
    if from_addr in sent_addresses:
        return sent_addresses[from_addr], "sender"
    return None, None


_QUOTE_HEADER_RE = re.compile(
    r"^(On .{0,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+|Sent from my \w+)\s*$",
    re.IGNORECASE,
)


def strip_quoted(text):
    """Keep only the newly written part of a reply (drop quoted history and signatures' tails)."""
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _QUOTE_HEADER_RE.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        lines.append(line.rstrip())
    result = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", result)[:4000]


def extract_text(msg):
    """Plain-text body of a parsed email (falls back to tag-stripped HTML)."""
    plain, html = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain":
            plain += text
        elif part.get_content_type() == "text/html":
            html += text
    if plain.strip():
        return plain
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    return re.sub(r"<[^>]+>", "", html)


def classify_reply(api_key, reply_text, today=None, models=None):
    """Classify a human reply with Groq. Never raises; returns {"status": None} on failure."""
    today = today or date.today()
    system_prompt = f"""You classify replies to a student's internship/job outreach email.
Today's date is {today.isoformat()}.

Choose exactly one status:
- "interested": positive — wants to talk, asks for a resume/portfolio/availability, invites next steps
- "interview_scheduled": an interview or call has been scheduled or a time was proposed
- "check_back": asks to reach out again later (a date or period may be given)
- "no_openings": no positions now, but polite/open to the future
- "final_rejection": a clear no / not interested / do not contact
- "other": anything else (questions you can't categorise, forwarding to someone, etc.)

If status is "check_back", convert any mentioned time ("next month", "after March", "in two weeks")
into a concrete date "YYYY-MM-DD"; otherwise null.

Return ONLY JSON: {{"status": "...", "check_back_date": "YYYY-MM-DD" or null, "summary": "one short sentence"}}"""
    try:
        result = json_chat(
            api_key,
            models or DEFAULT_TEXT_MODELS,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"REPLY:\n{reply_text[:3000]}"},
            ],
        )
    except Exception as e:
        return {"status": None, "check_back_date": None, "summary": f"Classification failed: {e}"}

    status = result.get("status")
    if status not in CLASSIFIABLE_STATUSES:
        status = None
    cb = result.get("check_back_date")
    if not (isinstance(cb, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cb)):
        cb = None
    return {"status": status, "check_back_date": cb, "summary": (result.get("summary") or "")[:300]}


# ─── DB ───────────────────────────────────────────────────────────────────────


def load_match_tables(conn):
    """Message-IDs we sent and addresses we emailed, each mapped to a recipient id."""
    known_ids, sent_addresses = {}, {}
    rows = conn.execute("""
        SELECT id, email, message_id FROM recipients
        WHERE status = 'sent' ORDER BY datetime(sent_at)
    """).fetchall()
    for r in rows:
        if r["message_id"]:
            known_ids[normalize_msgid(r["message_id"])] = r["id"]
        sent_addresses[r["email"].lower()] = r["id"]  # latest send wins
    for f in conn.execute(
        "SELECT recipient_id, message_id FROM followups WHERE status = 'sent' AND message_id IS NOT NULL"
    ):
        known_ids[normalize_msgid(f["message_id"])] = f["recipient_id"]
    return known_ids, sent_addresses


def record_reply(conn, recipient_id, message_id, from_email, subject, body, received_at, classification):
    """
    Store a reply and update the recipient. Returns "new_status" if the reply
    status changed, "logged" if only recorded, or None if already known.
    """
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO replies
            (recipient_id, message_id, from_email, subject, body, received_at, classification, summary, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            recipient_id,
            message_id,
            from_email,
            subject,
            body,
            received_at,
            classification.get("status") or "other",
            classification.get("summary"),
            now,
        ),
    )
    if cur.rowcount == 0:
        conn.rollback()  # end the implicit transaction; leaving it open holds the write lock
        return None

    row = conn.execute(
        "SELECT reply_status, reply_content, replied_at FROM recipients WHERE id = ?", (recipient_id,)
    ).fetchone()
    fields = {}
    if not row["replied_at"]:
        fields["replied_at"] = received_at or now
    # Never overwrite text or status a human has curated
    status_is_auto = row["reply_status"] in AUTO_UPDATABLE_STATUSES
    if status_is_auto or not row["reply_content"]:
        fields["reply_content"] = body
    result = "logged"
    if status_is_auto and classification.get("status"):
        fields["reply_status"] = classification["status"]
        fields["status_updated_at"] = now
        if classification["status"] == "check_back":
            fields["check_back_date"] = classification.get("check_back_date")
        if classification["status"] in ("interview_scheduled", "final_rejection"):
            fields["exclude_followup"] = 1
        result = "new_status"

    if fields:
        conn.execute(
            "UPDATE recipients SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
            list(fields.values()) + [recipient_id],
        )
    conn.commit()
    return result


# ─── IMAP ─────────────────────────────────────────────────────────────────────


def _since(days):
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%d-%b-%Y")


def fetch_candidate_headers(mail, lookback_days=LOOKBACK_DAYS, limit=MAX_MESSAGES_PER_RUN):
    """[(imap sequence number, parsed header Message)] for recent INBOX messages, newest first."""
    status, _ = mail.select("INBOX", readonly=True)
    if status != "OK":
        return []
    _, data = mail.search(None, f'SINCE "{_since(lookback_days)}"')
    ids = sorted((data[0].split() if data and data[0] else []), key=int, reverse=True)[:limit]
    if not ids:
        return []
    _, fetched = mail.fetch(b",".join(ids), f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
    result = []
    for item in fetched or []:
        if not isinstance(item, tuple):
            continue
        m = re.match(rb"(\d+)", item[0])
        if m:
            result.append((m.group(1), email.message_from_bytes(item[1])))
    return result


def detect_replies(mail, conn, config, processed_ids, mark_processed):
    """
    Find and record new human replies. `processed_ids` is the set of inbox
    Message-IDs already handled; `mark_processed(message_id, kind)` records one.
    Returns a summary dict.
    """
    summary = {"replies_detected": 0, "statuses_updated": 0, "details": []}
    known_ids, sent_addresses = load_match_tables(conn)
    if not sent_addresses:
        return summary
    api_key = config.get("groq_api_key") or config.get("groq_api_key_2") or config.get("groq_api_key_3")

    for seq, headers in fetch_candidate_headers(mail):
        header_id = (headers.get("Message-ID") or "").strip()
        if not header_id or header_id in processed_ids:
            continue
        recipient_id, how = match_reply(headers, known_ids, sent_addresses, config.get("gmail_address"))
        if not recipient_id:
            continue  # not from anyone we emailed; don't mark, it costs nothing to skip again

        try:
            _, msg_data = mail.fetch(seq, "(BODY.PEEK[])")
            full = email.message_from_bytes(msg_data[0][1])
        except Exception:
            continue

        body = strip_quoted(extract_text(full))
        from_email = parseaddr(headers.get("From", ""))[1].lower()
        subject = decode_mime_header(headers.get("Subject", ""))
        try:
            received_at = parsedate_to_datetime(headers.get("Date")).astimezone(UTC).isoformat()
        except Exception:
            received_at = None

        classification = (
            classify_reply(api_key, body, models=text_models(config))
            if api_key and body
            else {"status": None, "summary": None}
        )
        outcome = record_reply(conn, recipient_id, header_id, from_email, subject, body, received_at, classification)
        mark_processed(header_id, "reply")
        processed_ids.add(header_id)
        if outcome is None:
            continue

        summary["replies_detected"] += 1
        if outcome == "new_status":
            summary["statuses_updated"] += 1
        summary["details"].append(
            {
                "type": "reply",
                "email": from_email,
                "matched_by": how,
                "status": classification.get("status") or "unchanged",
                "summary": classification.get("summary"),
            }
        )

    return summary
