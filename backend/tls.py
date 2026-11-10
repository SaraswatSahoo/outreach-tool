"""
TLS settings shared by every outgoing connection (Gmail SMTP/IMAP, verification services).

The packaged macOS app can't see the Mac's certificate store, so every secure
connection failed certificate verification there. Trusting certifi's CA bundle as
well as the system store fixes that; the system store is still used where it
exists (e.g. a company or antivirus certificate installed on Windows).
"""

import ssl

import certifi


def ssl_context():
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cafile=certifi.where())
    except OSError:
        pass  # bundle missing from a broken install: the system store alone is still tried
    return context
