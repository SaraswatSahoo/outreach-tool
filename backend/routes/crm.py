"""Dashboard, contact search/history, duplicates, re-engagement and blocked domains."""

import sqlite3
import urllib.parse

from flask import Blueprint, jsonify, request

from common import arg_int, json_error, utc_now_iso
from constants import REPLY_STATUSES
from db import get_db, query_db
from utils import resolve_company_name

bp = Blueprint("crm", __name__, url_prefix="/api")


# ─── Dashboard ────────────────────────────────────────────────────────────────


@bp.route("/dashboard/reply-stats", methods=["GET"])
def reply_stats():
    rows = query_db("""
        SELECT COALESCE(reply_status, 'no_reply') as reply_status, COUNT(*) as count
        FROM recipients
        WHERE status = 'sent'
        GROUP BY COALESCE(reply_status, 'no_reply')
    """)
    stats = {s: 0 for s in REPLY_STATUSES}
    for row in rows:
        if row["reply_status"] in stats:
            stats[row["reply_status"]] += row["count"]
    return jsonify(stats)


@bp.route("/dashboard", methods=["GET"])
def dashboard():
    stats = query_db(
        """
        SELECT
            (SELECT COUNT(*) FROM campaigns) as total_campaigns,
            (SELECT COUNT(*) FROM recipients WHERE status = 'sent') as total_sent,
            (SELECT COUNT(*) FROM recipients WHERE status = 'failed') as total_failed,
            (SELECT COUNT(*) FROM followups WHERE status = 'sent') as total_followups_sent
    """,
        one=True,
    )

    recent = query_db("""
        SELECT c.id, c.name, c.created_at, COUNT(r.id) as recipient_count
        FROM campaigns c
        LEFT JOIN recipients r ON r.campaign_id = c.id
        GROUP BY c.id
        ORDER BY c.created_at DESC
        LIMIT 5
    """)

    # Check-backs due within the next 7 days (or overdue), across all campaigns
    reminders = query_db("""
        SELECT r.id, r.email, r.name, r.check_back_date, c.id as campaign_id, c.name as campaign_name
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE r.reply_status = 'check_back' AND r.check_back_date IS NOT NULL
          AND r.follow_up_sent = 0
          AND date(r.check_back_date) <= date('now', '+7 days')
        ORDER BY date(r.check_back_date)
    """)

    recent_replies = query_db("""
        SELECT rp.id, rp.from_email, rp.summary, rp.classification, rp.received_at,
               r.id as recipient_id, r.name, c.id as campaign_id, c.name as campaign_name
        FROM replies rp
        JOIN recipients r ON r.id = rp.recipient_id
        JOIN campaigns c ON c.id = r.campaign_id
        ORDER BY datetime(rp.received_at) DESC
        LIMIT 5
    """)

    # Follow-ups drafted by automatic sequences that are waiting for review
    pending_sequence = query_db("""
        SELECT c.id as campaign_id, c.name as campaign_name, COUNT(*) as count
        FROM followups f
        JOIN recipients r ON r.id = f.recipient_id
        JOIN campaigns c ON c.id = r.campaign_id
        WHERE f.status = 'draft' AND f.source = 'sequence' AND c.auto_send_followups = 0
        GROUP BY c.id
    """)

    return jsonify(
        {
            "stats": stats,
            "recent_campaigns": recent,
            "reminders": reminders,
            "recent_replies": recent_replies,
            "pending_sequence_drafts": pending_sequence,
        }
    )


# ─── Search & contact history ─────────────────────────────────────────────────


@bp.route("/search", methods=["GET"])
def search_global():
    query = request.args.get("q", "").strip()
    status = request.args.get("status")
    campaign_id = request.args.get("campaign_id")
    days = arg_int("days", None, minimum=1, maximum=3650)
    page = arg_int("page", 1, minimum=1)
    limit = arg_int("limit", 20, minimum=1, maximum=100)

    has_filter = (status and status != "all") or campaign_id or days
    if len(query) < 2 and not has_filter:
        return jsonify({"results": [], "total": 0, "page": 1, "limit": limit, "pages": 0})

    base_sql = """
        SELECT
            r.id, r.email, r.name, c.name as campaign_name, r.reply_status, r.reply_content,
            r.check_back_date, r.follow_up_sent, r.sent_at, r.subject, r.email_body,
            r.status as send_status, r.error_message,
            c.id as campaign_id, c.goal as campaign_goal, c.created_at as campaign_created_at
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE 1 = 1
    """
    params = []

    if len(query) >= 2:
        q = f"%{query.lower()}%"
        base_sql += """ AND (
            LOWER(r.email) LIKE ?
            OR LOWER(r.name) LIKE ?
            OR LOWER(SUBSTR(r.email, INSTR(r.email, '@') + 1)) LIKE ?
        )"""
        params += [q, q, q]

    if status and status != "all":
        base_sql += " AND COALESCE(r.reply_status, 'no_reply') = ?"
        params.append(status)

    if days:
        base_sql += " AND datetime(r.sent_at) >= datetime('now', ?)"
        params.append(f"-{days} days")

    if campaign_id:
        base_sql += " AND r.campaign_id = ?"
        params.append(campaign_id)

    count_res = query_db(f"SELECT COUNT(*) as cnt FROM ({base_sql})", params, one=True)
    total_count = count_res["cnt"] if count_res else 0

    base_sql += " ORDER BY r.sent_at IS NULL, r.sent_at DESC LIMIT ? OFFSET ?"
    results = query_db(base_sql, params + [limit, (page - 1) * limit])

    return jsonify(
        {
            "results": results,
            "total": total_count,
            "page": page,
            "limit": limit,
            "pages": (total_count + limit - 1) // limit,
        }
    )


@bp.route("/contact/<path:email>", methods=["GET"])
def contact_history(email):
    email = urllib.parse.unquote(email)
    domain = email.split("@")[-1].lower() if "@" in email else ""

    history = query_db(
        """
        SELECT r.*, c.name as campaign_name
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE LOWER(r.email) = LOWER(?)
        ORDER BY r.sent_at DESC
    """,
        (email,),
    )

    for h in history:
        h["followups"] = query_db(
            "SELECT id, step, subject, email_body, sent_at, source FROM followups "
            "WHERE recipient_id = ? AND status = 'sent' ORDER BY step, id",
            (h["id"],),
        )
        h["replies"] = query_db(
            "SELECT id, subject, body, received_at, classification, summary FROM replies "
            "WHERE recipient_id = ? ORDER BY received_at",
            (h["id"],),
        )

    same_domain = query_db(
        """
        SELECT r.email, c.name as campaign_name, r.sent_at, r.reply_status
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE LOWER(SUBSTR(r.email, INSTR(r.email, '@') + 1)) = ? AND LOWER(r.email) != LOWER(?)
        ORDER BY r.sent_at DESC
    """,
        (domain, email),
    )

    return jsonify(
        {
            "email": email,
            "resolved_name": next((h["name"] for h in history if h.get("name")), None),
            "domain": domain,
            "company": resolve_company_name(domain) if domain else None,
            "total_contacts": len(history),
            "history": history,
            "same_domain_contacts": same_domain,
        }
    )


@bp.route("/campaign/check-duplicates", methods=["POST"])
def check_duplicates():
    data = request.json or {}
    emails = data.get("emails", [])

    if not emails:
        return jsonify({"duplicates": [], "new_emails": [], "duplicate_count": 0, "new_count": 0})

    lowered = list({e.lower() for e in emails})
    placeholders = ",".join("?" for _ in lowered)

    # Only addresses that were actually emailed count as "contacted before";
    # the latest campaign is reported for each.
    duplicates_res = query_db(
        f"""
        SELECT LOWER(r.email) as email, r.sent_at as last_contacted, c.name as campaign_name,
               c.id as campaign_id, r.reply_status, cnt.times_contacted
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        JOIN (
            SELECT LOWER(email) as em, MAX(sent_at) as last_sent, COUNT(*) as times_contacted
            FROM recipients
            WHERE status = 'sent' AND LOWER(email) IN ({placeholders})
            GROUP BY LOWER(email)
        ) cnt ON cnt.em = LOWER(r.email) AND cnt.last_sent = r.sent_at
        WHERE r.status = 'sent'
        GROUP BY LOWER(r.email)
    """,
        lowered,
    )

    dup_emails = {d["email"] for d in duplicates_res}
    new_emails = [e for e in emails if e.lower() not in dup_emails]

    return jsonify(
        {
            "duplicates": duplicates_res,
            "new_emails": new_emails,
            "duplicate_count": len(duplicates_res),
            "new_count": len(new_emails),
        }
    )


@bp.route("/reengagement", methods=["GET"])
def reengagement_candidates():
    min_days = arg_int("min_days", 14, minimum=0, maximum=3650)
    max_days = arg_int("max_days", 60, minimum=0, maximum=3650)
    statuses = [s.strip() for s in request.args.get("status", "no_reply,check_back").split(",") if s.strip()]
    if not statuses:
        return jsonify({"candidates": []})

    placeholders = ",".join("?" for _ in statuses)
    candidates = query_db(
        f"""
        SELECT r.email, r.name, r.sent_at, r.reply_status, c.name as campaign_name, c.id as campaign_id
        FROM recipients r
        JOIN campaigns c ON r.campaign_id = c.id
        WHERE r.status = 'sent'
        AND datetime(r.sent_at) <= datetime('now', ?)
        AND datetime(r.sent_at) >= datetime('now', ?)
        AND COALESCE(r.reply_status, 'no_reply') IN ({placeholders})
        AND r.follow_up_sent = 0
        AND r.exclude_followup = 0
        ORDER BY r.sent_at DESC
    """,
        [f"-{min_days} days", f"-{max_days} days"] + statuses,
    )

    return jsonify({"candidates": candidates})


# ─── Pipeline (Kanban) ────────────────────────────────────────────────────────


@bp.route("/pipeline", methods=["GET"])
def pipeline():
    """Every contacted recipient with the fields a pipeline card needs."""
    sql = """
        SELECT r.id, r.email, r.name, r.company, r.role, COALESCE(r.reply_status, 'no_reply') as reply_status,
               r.check_back_date, r.sent_at, r.replied_at, r.follow_up_sent, r.status_updated_at,
               c.id as campaign_id, c.name as campaign_name
        FROM recipients r
        JOIN campaigns c ON c.id = r.campaign_id
        WHERE r.status = 'sent'
    """
    params = []
    campaign_id = arg_int("campaign_id", None)
    if campaign_id:
        sql += " AND r.campaign_id = ?"
        params.append(campaign_id)
    sql += " ORDER BY datetime(COALESCE(r.status_updated_at, r.sent_at)) DESC LIMIT 2000"
    return jsonify({"recipients": query_db(sql, params)})


# ─── Blocked domains ──────────────────────────────────────────────────────────


@bp.route("/blocked-domains", methods=["GET"])
def get_blocked_domains():
    return jsonify(query_db("SELECT * FROM blocked_domains ORDER BY blocked_at DESC"))


@bp.route("/blocked-domains", methods=["POST"])
def block_domain():
    data = request.json or {}
    domain = (data.get("domain") or "").strip().lower().lstrip("@")
    if not domain or "." not in domain:
        return json_error("A valid domain is required (e.g. example.com)")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO blocked_domains (domain, reason, blocked_at) VALUES (?, ?, ?)",
            (domain, data.get("reason", ""), utc_now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return json_error("Domain already blocked", 409)
    finally:
        conn.close()
    return jsonify({"message": f"{domain} blocked"})


@bp.route("/blocked-domains/<string:domain>", methods=["DELETE"])
def unblock_domain(domain):
    conn = get_db()
    conn.execute("DELETE FROM blocked_domains WHERE domain = ?", (domain.lower(),))
    conn.commit()
    conn.close()
    return jsonify({"message": f"{domain} unblocked"})
