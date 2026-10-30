"""Minimal in-memory stand-in for imaplib.IMAP4_SSL, enough for the inbox monitor."""

import email


def make_message(
    frm,
    to,
    subject,
    body,
    message_id,
    in_reply_to=None,
    references=None,
    date="Mon, 05 Oct 2026 10:00:00 +0000",
    extra_headers=None,
):
    lines = [f"From: {frm}", f"To: {to}", f"Subject: {subject}", f"Message-ID: {message_id}", f"Date: {date}"]
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append(f"References: {references}")
    for k, v in (extra_headers or {}).items():
        lines.append(f"{k}: {v}")
    lines += ["Content-Type: text/plain; charset=utf-8", "", body]
    return "\r\n".join(lines).encode()


class FakeIMAP:
    def __init__(self, messages):
        self.messages = list(messages)  # raw bytes; sequence numbers are 1-based positions
        self.logged_out = False

    def select(self, folder, readonly=False):
        return ("OK", [str(len(self.messages)).encode()]) if folder == "INBOX" else ("NO", [b""])

    def search(self, charset, criteria):
        ids = []
        for i, raw in enumerate(self.messages, start=1):
            msg = email.message_from_bytes(raw)
            frm, subject = (msg.get("From") or "").lower(), (msg.get("Subject") or "").lower()
            if "mailer-daemon" in criteria:
                ok = "mailer-daemon" in frm or "postmaster" in frm
            elif "out of office" in criteria:
                ok = "out of office" in subject or "auto-reply" in subject
            else:
                ok = True
            if ok:
                ids.append(str(i).encode())
        return "OK", [b" ".join(ids)]

    def fetch(self, ids, spec):
        out = []
        for seq in (ids.decode() if isinstance(ids, bytes) else ids).split(","):
            raw = self.messages[int(seq) - 1]
            if "HEADER.FIELDS" in spec:
                payload = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            else:
                payload = raw
            out.append((f"{seq} (BODY[] {{{len(payload)}}}".encode(), payload))
            out.append(b")")
        return "OK", out

    def logout(self):
        self.logged_out = True
