"""
Follow-up generation shared by the manual "Generate follow-ups" flow and the
automatic sequence engine.
"""

import json

from ai_generator import generate_contextual_followup
from common import configured_groq_keys
from constants import DEFAULT_SEQUENCE, MAX_FOLLOWUPS
from llm import text_models


def campaign_sequence(campaign):
    """The campaign's follow-up steps: [{"delay_days": int}, ...] (at most MAX_FOLLOWUPS)."""
    try:
        steps = json.loads(campaign.get("sequence_json") or "null")
    except (TypeError, ValueError):
        steps = None
    if not isinstance(steps, list) or not steps:
        steps = DEFAULT_SEQUENCE
    clean = []
    for s in steps[:MAX_FOLLOWUPS]:
        try:
            clean.append({"delay_days": max(1, min(60, int(s.get("delay_days", 4))))})
        except (TypeError, ValueError, AttributeError):
            continue
    return clean or DEFAULT_SEQUENCE


def previous_followup_bodies(conn, recipient_id):
    return [
        row["email_body"]
        for row in conn.execute(
            "SELECT email_body FROM followups WHERE recipient_id = ? AND status = 'sent' ORDER BY step, id",
            (recipient_id,),
        )
    ]


def generate_followup(config, recipient, api_key, previous_bodies, max_steps, global_context=""):
    """AI-generate the next follow-up for `recipient` (a recipients row as dict)."""
    step = (recipient.get("follow_up_sent") or 0) + 1
    return generate_contextual_followup(
        profile=config.get("profile", {}),
        original_subject=recipient["subject"],
        original_body=recipient["email_body"],
        recipient_email=recipient["email"],
        recipient_name=recipient["name"],
        reply_status=recipient.get("reply_status") or "no_reply",
        reply_content=recipient.get("reply_content"),
        check_back_date=recipient.get("check_back_date"),
        global_context=global_context,
        api_key=api_key,
        resume_parsed=config.get("resume_parsed", {}),
        step=step,
        max_steps=max(step, max_steps),
        previous_followups=previous_bodies,
        company=recipient.get("company"),
        fallback_keys=configured_groq_keys(config),
        models=text_models(config),
    )


def save_followup_draft(conn, recipient, result, source):
    """Insert the draft for the recipient's next step, replacing any unsent draft."""
    step = (recipient.get("follow_up_sent") or 0) + 1
    conn.execute("DELETE FROM followups WHERE recipient_id = ? AND status IN ('draft', 'failed')", (recipient["id"],))
    conn.execute(
        "INSERT INTO followups (recipient_id, subject, email_body, status, step, source) VALUES (?, ?, ?, 'draft', ?, ?)",
        (recipient["id"], result["subject"], result["body"], step, source),
    )
