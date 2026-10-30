#!/usr/bin/env python3
"""
One-step launcher for Outreach Tool (Windows, macOS, Linux).

    python run.py              set up if needed, start the app, open the browser
    python run.py --no-browser start without opening a browser
    python run.py --port 9000  use a different port (default 8765)
    python run.py --check      set up, start, verify it works, then stop (used by CI)
    python run.py --dev        development mode: Vite dev server + Flask debug server

First run: creates .venv/ and installs the Python packages, then builds the
website (with Node.js if installed, otherwise downloads the pre-built website
from the latest GitHub Release). Later runs start in a couple of seconds.

Only the Python standard library is used here, so this runs on a fresh machine.
"""

import sys

# Keep everything above the version check parseable by old Pythons, so users
# get this message instead of a syntax error.
if sys.version_info < (3, 11):  # noqa: UP036 (deliberate: friendly message on old Pythons)
    found = ".".join(str(n) for n in sys.version_info[:2])
    sys.stderr.write("Outreach Tool needs Python 3.11 or newer (you have " + found + ").\n")
    sys.stderr.write("Download it from https://www.python.org/downloads/ and run this again.\n")
    sys.exit(1)

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402
import zipfile  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
FRONTEND = os.path.join(ROOT, "frontend")
DIST = os.path.join(FRONTEND, "dist")
VENV = os.path.join(ROOT, ".venv")
DEFAULT_REPO = "SaraswatSahoo/outreach-tool"
IS_WINDOWS = os.name == "nt"

# Inputs that change the built website
FRONTEND_INPUTS = [
    "src",
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.js",
    "tailwind.config.js",
    "postcss.config.js",
]


def say(message):
    print(f"[outreach] {message}", flush=True)


def fail(message):
    sys.stderr.write(f"\n[outreach] ERROR: {message}\n")
    sys.exit(1)


# ─── Hash stamps: skip work that's already done ──────────────────────────────


def hash_paths(base, relative_paths, extra=""):
    """sha256 over file names and contents (directories walked in sorted order)."""
    digest = hashlib.sha256(extra.encode())
    for rel in relative_paths:
        path = os.path.join(base, rel)
        files = []
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                files += [os.path.join(dirpath, f) for f in sorted(filenames)]
        elif os.path.isfile(path):
            files = [path]
        for f in files:
            digest.update(os.path.relpath(f, base).replace(os.sep, "/").encode())
            with open(f, "rb") as fh:
                digest.update(fh.read())
    return digest.hexdigest()


def read_stamp(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def write_stamp(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)


# ─── Python environment ───────────────────────────────────────────────────────


def venv_python():
    return os.path.join(VENV, "Scripts", "python.exe") if IS_WINDOWS else os.path.join(VENV, "bin", "python")


def ensure_python_env():
    py = venv_python()
    if not os.path.exists(py):
        say("First run: creating a Python environment in .venv (one-time)...")
        try:
            subprocess.run([sys.executable, "-m", "venv", VENV], check=True)
        except subprocess.CalledProcessError:
            hint = " On Debian/Ubuntu: sudo apt install python3-venv" if sys.platform.startswith("linux") else ""
            fail("Couldn't create the Python environment." + hint)

    requirements = os.path.join(BACKEND, "requirements.txt")
    stamp_file = os.path.join(VENV, ".requirements.sha256")
    wanted = hash_paths(BACKEND, ["requirements.txt"], extra=sys.version)
    if read_stamp(stamp_file) != wanted:
        say("Installing Python packages (one-time, about a minute)...")
        cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", requirements]
        if subprocess.run(cmd).returncode != 0:
            fail("Installing Python packages failed. Check your internet connection and try again.")
        write_stamp(stamp_file, wanted)
    return py


# ─── Website (frontend) ───────────────────────────────────────────────────────


def github_repo():
    """owner/name of the GitHub repo this checkout came from (so forks use their own releases)."""
    try:
        url = subprocess.run(
            ["git", "-C", ROOT, "config", "--get", "remote.origin.url"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    if "github.com" in url:
        path = url.split("github.com", 1)[1].lstrip(":/").removesuffix(".git").strip("/")
        if path.count("/") == 1:
            return path
    return DEFAULT_REPO


def safe_extract(zip_path, target):
    """Extract a zip, refusing any entry that would land outside `target`."""
    target = os.path.abspath(target)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            dest = os.path.abspath(os.path.join(target, member))
            if dest != target and not dest.startswith(target + os.sep):
                raise ValueError(f"Unsafe path in archive: {member}")
        zf.extractall(target)


def download_prebuilt_frontend():
    """Fetch frontend-dist.zip from the latest GitHub Release (for machines without Node.js)."""
    url = os.environ.get("OUTREACH_FRONTEND_ZIP_URL")
    tag = "custom"
    if not url:
        repo = github_repo()
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        say(f"Node.js not found — downloading the pre-built website from {repo} releases...")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(api, headers={"Accept": "application/vnd.github+json"}), timeout=30
            ) as resp:
                release = json.load(resp)
        except OSError as e:
            fail(f"Couldn't reach GitHub ({e}). Install Node.js from https://nodejs.org/ to build the website locally.")
        asset = next((a for a in release.get("assets", []) if a.get("name") == "frontend-dist.zip"), None)
        if not asset:
            fail("The latest release has no pre-built website. Install Node.js from https://nodejs.org/ and try again.")
        url, tag = asset["browser_download_url"], release.get("tag_name", "release")

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, "frontend-dist.zip")
        try:
            urllib.request.urlretrieve(url, zip_path)
        except OSError as e:
            fail(f"Downloading the website failed: {e}")
        staging = os.path.join(tmp, "dist")
        try:
            safe_extract(zip_path, staging)
        except (ValueError, zipfile.BadZipFile) as e:
            fail(f"The downloaded website archive is invalid ({e}); not using it.")
        if not os.path.isfile(os.path.join(staging, "index.html")):
            fail("The downloaded website archive is missing index.html.")
        shutil.rmtree(DIST, ignore_errors=True)
        shutil.copytree(staging, DIST)
    write_stamp(os.path.join(DIST, ".build-stamp"), f"prebuilt:{tag}")
    say(f"Website ready (pre-built, {tag}).")


def npm_command():
    return shutil.which("npm")


def build_frontend_with_npm(npm):
    lock_stamp = os.path.join(FRONTEND, "node_modules", ".lock-stamp")
    lock_hash = hash_paths(FRONTEND, ["package-lock.json"])
    if read_stamp(lock_stamp) != lock_hash:
        say("Installing website dependencies (one-time)...")
        if subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--loglevel=error"], cwd=FRONTEND).returncode != 0:
            fail("npm ci failed.")
        write_stamp(lock_stamp, lock_hash)
    say("Building the website...")
    if subprocess.run([npm, "run", "build", "--silent"], cwd=FRONTEND).returncode != 0:
        fail("Building the website failed.")


def ensure_frontend():
    wanted = hash_paths(FRONTEND, FRONTEND_INPUTS)
    stamp_file = os.path.join(DIST, ".build-stamp")
    current = read_stamp(stamp_file)
    has_build = os.path.isfile(os.path.join(DIST, "index.html"))
    if has_build and current == wanted:
        return

    npm = npm_command()
    if npm:
        build_frontend_with_npm(npm)
        write_stamp(stamp_file, wanted)
    elif has_build:
        # Pre-built (downloaded) website without Node.js to rebuild: use it as is
        if not (current or "").startswith("prebuilt:"):
            say("Website source changed but Node.js isn't installed; using the existing build.")
    else:
        download_prebuilt_frontend()


# ─── Modes ────────────────────────────────────────────────────────────────────


def serve_cmd(py, extra_args):
    return [py, os.path.join(BACKEND, "serve.py"), *extra_args]


def run_app(py, extra_args):
    try:
        return subprocess.call(serve_cmd(py, extra_args))
    except KeyboardInterrupt:
        return 0


def wait_for(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except OSError:
            time.sleep(0.5)
    return None, ""


def check(py):
    """Start on a free port, verify the API and the website respond, then stop."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    with tempfile.TemporaryDirectory() as data:
        # Isolated data so a check never touches real settings or contacts
        env.update(
            OUTREACH_DB_PATH=os.path.join(data, "check.db"),
            OUTREACH_CONFIG_PATH=os.path.join(data, "config.json"),
            OUTREACH_UPLOAD_DIR=os.path.join(data, "uploads"),
        )
        proc = subprocess.Popen(serve_cmd(py, ["--no-browser", "--port", str(port)]), env=env)
        try:
            status, body = wait_for(f"http://127.0.0.1:{port}/api/health", 90)
            ok_api = status == 200 and '"outreach-tool"' in body
            status, page = wait_for(f"http://127.0.0.1:{port}/campaigns", 10)
            ok_web = status == 200 and 'id="root"' in page
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    say(f"Check: API {'OK' if ok_api else 'FAILED'}, website {'OK' if ok_web else 'FAILED'}")
    return 0 if ok_api and ok_web else 1


def dev(py):
    """Vite dev server (hot reload, :5173) + Flask debug server (:5000)."""
    npm = npm_command()
    if not npm:
        fail("Development mode needs Node.js: https://nodejs.org/")
    if not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=FRONTEND, check=True)
    backend = subprocess.Popen([py, "app.py"], cwd=BACKEND, env={**os.environ, "FLASK_DEBUG": "1"})
    frontend = subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND)
    say("Dev servers starting — open http://localhost:5173 (Ctrl+C stops both)")
    try:
        frontend.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (frontend, backend):
            p.terminate()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Start Outreach Tool")
    parser.add_argument("--check", action="store_true", help="set up, start, verify, stop")
    parser.add_argument("--dev", action="store_true", help="development mode (Vite + Flask debug)")
    args, passthrough = parser.parse_known_args()

    os.chdir(ROOT)
    py = ensure_python_env()
    if args.dev:
        return dev(py)
    ensure_frontend()
    if args.check:
        return check(py)
    return run_app(py, passthrough)


if __name__ == "__main__":
    sys.exit(main())
