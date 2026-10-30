"""
Run Outreach Tool as a local app: one production server (Waitress) for the
API and the built React frontend, plus the background scheduler, then open
the browser.

This is the entry point for both the launcher (run.py) and the standalone
executables built by PyInstaller.

    python serve.py [--port 8765] [--host 127.0.0.1] [--no-browser]
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

# Make sibling modules importable when run as `python backend/serve.py` or frozen
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_PORT = 8765


def safe_console():
    """
    Never crash on console output. Windows consoles often use a legacy code page
    (cp1252/cp437) that can't encode characters like em dashes, and print()
    would raise UnicodeEncodeError -- even from background job threads.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def probe(host, port, timeout=1.0):
    """'ours' if Outreach Tool answers on host:port, 'other' if something else does, None if free."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode() or "{}")
            return "ours" if data.get("app") == "outreach-tool" else "other"
    except (OSError, ValueError):
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return "other" if s.connect_ex((host, port)) == 0 else None


def free_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def open_when_ready(url, health_url, timeout=60):
    """Open the browser once the server answers, so the user never sees 'connection refused'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.3)


def main(argv=None):
    safe_console()
    parser = argparse.ArgumentParser(description="Run Outreach Tool locally")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = parser.parse_args(argv)

    host, port = args.host, args.port
    state = probe(host, port)
    if state == "ours":
        url = f"http://{host}:{port}"
        print(f"Outreach Tool is already running at {url} - opening it.")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if state == "other":
        port = free_port(host)
        print(f"Port {args.port} is in use by another program; using {port} instead.")

    # Import after the port check so a second launch exits without touching the database
    from waitress import serve

    from app import app
    from constants import CONFIG_PATH, frontend_dist_dir
    from routes.inbox import start_scheduler
    from version import VERSION

    url = f"http://{host}:{port}"
    has_frontend = os.path.isfile(os.path.join(frontend_dist_dir(), "index.html"))
    start_scheduler()

    line = "-" * 64
    print(f"\n{line}\n  Outreach Tool {VERSION} is running at {url}")
    print(f"  Your data: {os.path.dirname(os.path.abspath(CONFIG_PATH))}")
    if not has_frontend:
        print("  (API only - the website hasn't been built; use run.py to build it)")
    print(f"  Keep this window open while you use it. Close it (or press Ctrl+C) to stop.\n{line}\n", flush=True)

    if not args.no_browser and has_frontend:
        threading.Thread(target=open_when_ready, args=(url, f"{url}/api/health"), daemon=True).start()

    serve(app, host=host, port=port, threads=8)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
