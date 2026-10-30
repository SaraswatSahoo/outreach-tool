"""
Gmail SMTP email sending logic.
Supports plain text emails with PDF attachment and thread continuity headers.
"""

import os
import smtplib
import socket
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # TLS


def _connect_smtp(host=SMTP_HOST, port=SMTP_PORT, timeout=30):
    """
    Connect to Gmail SMTP server. Fallbacks to IPv4 explicitly if IPv6
    returns network unreachable (Errno 101).
    """
    try:
        return smtplib.SMTP(host, port, timeout=timeout)
    except OSError as e:
        if getattr(e, 'errno', None) == 101 or "unreachable" in str(e).lower():
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
            if infos:
                ipv4_ip = infos[0][4][0]
                server = smtplib.SMTP(timeout=timeout)
                server.connect(ipv4_ip, port)
                # connect() records the IP as the host; restore the hostname so
                # STARTTLS verifies the certificate against smtp.gmail.com.
                server._host = host
                return server
        raise


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

    with _connect_smtp(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(sender_email, app_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())

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
