"""
Shared constants and filesystem locations.

Paths can be overridden with environment variables or a backend/.env file
(see .env.example) — used by the test suite, and handy for running several
isolated instances.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)

# True inside a standalone executable built by PyInstaller
FROZEN = bool(getattr(sys, "frozen", False))


def user_data_dir(platform=None, environ=None):
    """Per-user folder for app data, following each OS's convention."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = os.path.expanduser("~")
    if platform.startswith("win"):
        return os.path.join(environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "OutreachTool")
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "OutreachTool")
    return os.path.join(environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), "outreach-tool")


def default_data_dir(frozen=FROZEN):
    """
    Where config, database and uploads live by default.
    The executable unpacks to a temporary folder on every run, so it must keep
    data in the user's app-data folder; running from source keeps it in backend/.
    """
    return user_data_dir() if frozen else BASE_DIR


def frontend_dist_dir():
    """The built React app (index.html + assets) served by Flask, if present."""
    if os.environ.get("OUTREACH_FRONTEND_DIST"):
        return os.environ["OUTREACH_FRONTEND_DIST"]
    if FROZEN:
        return os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "frontend_dist")
    return os.path.join(REPO_DIR, "frontend", "dist")


DATA_DIR = default_data_dir()


def load_dotenv(path):
    """
    Minimal .env loader (KEY=VALUE lines, # comments, optional quotes).
    Variables already set in the real environment take precedence.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().removeprefix("export ").strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_dotenv(os.path.join(DATA_DIR, ".env"))

DB_PATH = os.environ.get("OUTREACH_DB_PATH", os.path.join(DATA_DIR, "outreach.db"))
CONFIG_PATH = os.environ.get("OUTREACH_CONFIG_PATH", os.path.join(DATA_DIR, "config.json"))
UPLOAD_FOLDER = os.environ.get("OUTREACH_UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))
RESUME_PATH = os.path.join(UPLOAD_FOLDER, "resume.pdf")
RESUME_TEXT_PATH = os.path.join(UPLOAD_FOLDER, "resume.txt")

MAX_RESUME_BYTES = 5 * 1024 * 1024

# Every reply status a recipient can have
REPLY_STATUSES = (
    "no_reply",
    "check_back",
    "interested",
    "no_openings",
    "interview_scheduled",
    "final_rejection",
    "invalid_email",
)

# Reply statuses that permanently stop follow-ups
AUTO_EXCLUDED_STATUSES = ("invalid_email", "interview_scheduled", "final_rejection")

# Reply statuses that are not eligible for a batch follow-up
# (interested contacts deserve a personal reply, not a templated nudge).
# Keep in sync with frontend/src/constants/status.js
FOLLOWUP_INELIGIBLE_STATUSES = AUTO_EXCLUDED_STATUSES + ("interested",)

GROQ_KEY_FIELDS = ("groq_api_key", "groq_api_key_2", "groq_api_key_3")

SEND_DELAY_MIN = 20
SEND_DELAY_MAX = 90

# Follow-ups per recipient (manual or automatic sequence)
MAX_FOLLOWUPS = 3
# Default automatic sequence: days to wait after the previous email for each follow-up
DEFAULT_SEQUENCE = [{"delay_days": 4}, {"delay_days": 7}]

# Reply statuses automation may overwrite. Anything else was set by a human
# and is never touched automatically.
AUTO_UPDATABLE_STATUSES = (None, "no_reply", "check_back")
