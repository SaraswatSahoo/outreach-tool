"""
Gmail SMTP email sending logic.
Supports plain text emails with PDF attachment and thread continuity headers.

Connecting is resilient: port 587 (STARTTLS) and port 465 (SSL) are both tried,
each also over IPv4 only, before giving up with GmailUnreachable. Only the
connection is retried — never the transmission — so an email can't be sent twice.
"""

import os
import smtplib
import socket
import ssl
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

SMTP_HOST = "smtp.gmail.com"
STARTTLS_PORT = 587  # plain connection upgraded with STARTTLS
SSL_PORT = 465  # TLS from the start; often open on networks that block 587
CONNECT_TIMEOUT = 20
ATTEMPTS_PER_PORT = 3  # flaky networks often connect on the 2nd or 3rd try
RETRY_PAUSE = 2  # seconds between attempts
DEAD_PORT_SECONDS = 600  # skip a port that failed every attempt for 10 minutes

# What we've learned about this network, shared by all sends in this process:
# the port that last worked, whether IPv6 is broken, and ports that are blocked.
_net = {"port": STARTTLS_PORT, "ipv4_only": False, "dead_until": {}}


def _pause(seconds):
    """Wait between connection attempts (patched out in tests)."""
    time.sleep(seconds)


class GmailUnreachable(Exception):
    """
    Couldn't connect to Gmail's mail server on any port. This happens before any
    email data is sent, so nothing was delivered and the email can safely be
    sent again later. Usually a network, firewall, VPN or antivirus block.
    """


class _IPv4Socket(smtplib.SMTP):
    """Connect over IPv4 only (works around broken IPv6 routes, common on Windows)."""

    def _get_socket(self, host, port, timeout):
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if not infos:
            raise OSError(f"no IPv4 address for {host}")
        return socket.create_connection(infos[0][4][:2], timeout, self.source_address)


_IPv4SMTP = _IPv4Socket


class _IPv4SMTP_SSL(smtplib.SMTP_SSL, _IPv4Socket):  # SMTP_SSL wraps the IPv4 socket in TLS
    pass


def _open(port, ipv4_only, timeout=CONNECT_TIMEOUT):
    """An encrypted, not-yet-logged-in connection to Gmail. The hostname is kept for certificate checks."""
    context = ssl.create_default_context()
    if port == SSL_PORT:
        cls = _IPv4SMTP_SSL if ipv4_only else smtplib.SMTP_SSL
        return cls(SMTP_HOST, port, timeout=timeout, context=context)
    cls = _IPv4SMTP if ipv4_only else smtplib.SMTP
    server = cls(SMTP_HOST, port, timeout=timeout)
    try:
        server.starttls(context=context)
    except Exception:
        server.close()
        raise
    return server


def describe_connection_error(error):
    """Plain-English version of a low-level connection error."""
    text = str(error).lower()
    if isinstance(error, (TimeoutError, socket.timeout)) or "10060" in text or "timed out" in text:
        return "connection timed out"
    if "10061" in text or "refused" in text:
        return "connection refused"
    if "getaddrinfo" in text or "name or service not known" in text or "11001" in text:
        return "couldn't look up smtp.gmail.com (no internet?)"
    if "unreachable" in text or "10051" in text or "10065" in text:
        return "network unreachable"
    if isinstance(error, ssl.SSLError) or "certificate" in text:
        return "secure connection failed (antivirus or proxy intercepting email?)"
    return str(error) or error.__class__.__name__


def connect_and_login(sender_email, app_password):
    """
    Log in to Gmail, trying the port that last worked first. Each port gets up to
    ATTEMPTS_PER_PORT tries (retries over IPv4, which fixes broken IPv6 routes).
    Raises SMTPAuthenticationError for a wrong password (no point retrying) or
    GmailUnreachable if no attempt could connect.
    """
    now = time.monotonic()
    ports = [_net["port"]] + [p for p in (STARTTLS_PORT, SSL_PORT) if p != _net["port"]]
    live = [p for p in ports if _net["dead_until"].get(p, 0) <= now] or ports  # all blocked? try all again
    problems = []
    for port in live:
        for attempt in range(ATTEMPTS_PER_PORT):
            ipv4_only = _net["ipv4_only"] or attempt > 0
            server = None
            try:
                server = _open(port, ipv4_only)
                server.login(sender_email, app_password)
            except smtplib.SMTPAuthenticationError:
                if server:
                    server.close()
                raise
            except (OSError, smtplib.SMTPException) as e:  # socket errors, timeouts, TLS, disconnects
                if server:
                    server.close()
                problems.append(describe_connection_error(e))
                if attempt < ATTEMPTS_PER_PORT - 1:
                    _pause(RETRY_PAUSE)
                continue
            if ipv4_only and attempt > 0:
                _net["ipv4_only"] = True  # the normal route failed but IPv4 worked: use IPv4 from now on
            _net["port"] = port
            _net["dead_until"].pop(port, None)
            return server
        _net["dead_until"][port] = time.monotonic() + DEAD_PORT_SECONDS
    reasons = "; ".join(dict.fromkeys(problems))
    raise GmailUnreachable(
        f"Couldn't reach Gmail's mail server (tried ports {', '.join(map(str, live))}): {reasons}. "
        "Your network, firewall, VPN or antivirus is probably blocking or dropping outgoing email. Nothing was sent."
    )


def test_gmail_connection(sender_email, app_password, attempts=ATTEMPTS_PER_PORT):
    """Check each port separately, with retries (for the Settings 'Test Gmail connection' button)."""
    results = []
    for port in (STARTTLS_PORT, SSL_PORT):
        result = {"port": port, "connected": False, "logged_in": False, "attempts": 0, "seconds": None, "message": ""}
        for attempt in range(attempts):
            result["attempts"] = attempt + 1
            started = time.monotonic()
            server = None
            try:
                server = _open(port, ipv4_only=_net["ipv4_only"] or attempt > 0)
                result["connected"] = True
                result["seconds"] = round(time.monotonic() - started, 1)
                if sender_email and app_password:
                    server.login(sender_email, app_password)
                    result["logged_in"] = True
                    result["message"] = "Connected and logged in"
                else:
                    result["message"] = "Connected (save your Gmail address and App Password to test the login too)"
            except smtplib.SMTPAuthenticationError:
                result["message"] = "Connected, but Gmail rejected the login — check the App Password"
            except (OSError, smtplib.SMTPException) as e:
                result["message"] = describe_connection_error(e)
            finally:
                if server:
                    try:
                        server.quit()
                    except Exception:
                        server.close()
            if result["connected"]:
                break
            if attempt < attempts - 1:
                _pause(RETRY_PAUSE)
        results.append(result)
    return results


def send_email(
    sender_email,
    sender_name,
    app_password,
    recipient_email,
    subject,
    body,
    resume_path,
    reply_to_message_id=None,
    references=None,
):
    """
    Send an email via Gmail SMTP with optional resume attachment and threading.

    Args:
        sender_email: Gmail address
        sender_name: Display name for the sender
        app_password: Gmail App Password
        recipient_email: Recipient's email address
        subject: Email subject line
        body: Plain text email body
        resume_path: Path to resume PDF file
        reply_to_message_id: Message-ID being replied to (In-Reply-To), for threading
        references: Full list of earlier Message-IDs in the thread, oldest first

    Returns:
        The Message-ID of the sent email

    Raises:
        Exception on SMTP errors
    """
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    # Use the sender's domain rather than this machine's hostname
    message_id = make_msgid(domain=sender_email.split("@")[-1])
    msg["Message-ID"] = message_id

    # Threading headers for follow-ups
    if reply_to_message_id:
        msg["In-Reply-To"] = reply_to_message_id
        msg["References"] = " ".join(references or [reply_to_message_id])

    msg.attach(MIMEText(body, "plain"))

    # Attach resume PDF
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", 'attachment; filename="Resume.pdf"')
            msg.attach(part)

    server = connect_and_login(sender_email, app_password)
    try:
        # Not retried: once the message is being transmitted, a retry could send it twice
        server.sendmail(sender_email, recipient_email, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            server.close()
    return message_id


def send_with_config(config, recipient_email, subject, body, resume_path, reply_to_message_id=None, references=None):
    """send_email() using the Gmail credentials and display name from config."""
    return send_email(
        sender_email=config["gmail_address"],
        sender_name=config["profile"]["name"],
        app_password=config["gmail_app_password"],
        recipient_email=recipient_email,
        subject=subject,
        body=body,
        resume_path=resume_path,
        reply_to_message_id=reply_to_message_id,
        references=references,
    )
