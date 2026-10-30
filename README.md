# Outreach Tool 🚀

A powerful, local-first personal email outreach tool that generates unique, human-sounding personalized emails using the Groq API and sends them via Gmail SMTP with automated resume attachments.

**Single-user • Localhost-only • Privacy-focused**
---
## Demo

<video width="630" height="300" src="https://github.com/user-attachments/assets/da78db07-0ccb-463e-ad02-e3029b84d825"></video>




---

## ✨ Key Features

- **Parallel Email Generation**: Leverages multi-threading (`ThreadPoolExecutor`) to generate multiple personalized drafts simultaneously, significantly reducing wait times.
- **Multi-Key Round-Robin**: Supports up to 3 Groq API keys, used in rotation to spread load across rate limits.
- **Live Progress Tracking**: Real-time visual feedback via a dedicated progress bar UI. Monitor successes, failures, and individual error messages as they happen.
- **Gmail SMTP Integration**: Securely send emails using Gmail App Passwords. Supports automated resume attachments (PDF) and threaded follow-ups.
- **Intelligent Personalization**: Powered by Groq's high-speed inference (Llama 3), tailoring every email to the lead's profile and your custom campaign goal.
- **Context-Aware Follow-ups**: Paste a recruiter's reply and the follow-up adapts to it (no reply, check back later, no openings). Follow-ups are sent as threaded replies.
- **Automatic Reply Detection**: Every 10 minutes the inbox monitor finds replies to your emails (matched by email thread, or by sender), classifies them with AI (interested / check back / no openings / rejection / interview) and records them. It also catches bounces and out-of-office replies. Statuses you set by hand are never overwritten.
- **Follow-up Sequences**: Per campaign, set up to 3 automatic follow-ups (e.g. +4 days, +7 days). Each step is drafted for everyone who hasn't replied, threaded onto the original email, and either queued for your review or sent automatically. Any reply stops the sequence for that person.
- **Scheduled Sending**: Schedule a campaign for later, restrict sending to working hours / weekdays, set a daily limit (with an optional warm-up ramp for new Gmail accounts), and stop a send at any time. Sends pause and resume automatically around those limits, and a send cut off by a backend restart picks up where it left off.
- **CSV / Excel Import**: Import contacts (`.csv` or `.xlsx`) with company, role and personal notes; the AI uses them to personalise each email.
- **Pipeline Board**: A Kanban view of every contact by reply status, with drag-and-drop status changes.
- **Lightweight CRM**: Search contacts, see every email and reply per contact, check-back reminders, blocked domains, and re-engagement candidates.
- **Safe Sending**: Duplicate-send protection, automatic stop on Gmail login failure, retry for failed emails, and progress that survives a backend restart.
- **Local-First**: Your leads, drafts and credentials stay on your machine in a SQLite database and `backend/config.json`. Note: credentials are stored **unencrypted** in that file — keep it private.

---

## 🛠️ Tech Stack

- **Backend**: Python 3.11+, Flask (REST API), SQLite (Database), APScheduler.
- **Frontend**: React 18, Vite, Tailwind CSS v3, React Router v6.
- **AI Inference**: Groq SDK — `openai/gpt-oss-20b` by default, falling back to `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` (Groq shut down its Llama models for free accounts on 2026-08-16). Models are configurable in **Settings → AI Models**, which lists what your key can use.
- **Automation**: Multi-threaded generation workers; send/generation progress persisted in SQLite.

---

## 🚀 Getting Started

You need a **Gmail account with [2-Step Verification](https://myaccount.google.com/security)** (for an App Password) and at least one free **Groq API key** from the [Groq Console](https://console.groq.com/keys). Pick one of the two ways to run it:

### Option 1 — Download the app (nothing to install)

Go to the [**Releases page**](https://github.com/SaraswatSahoo/outreach-tool/releases/latest) and download the file for your system:

| System | File | How to start |
|---|---|---|
| Windows | `OutreachTool-windows.exe` | Double-click. If SmartScreen appears: **More info → Run anyway**. |
| macOS | `OutreachTool-macos.zip` | Unzip, then **right-click → Open** the first time (the app isn't code-signed). |
| Linux | `OutreachTool-linux.tar.gz` | `tar xzf OutreachTool-linux.tar.gz && ./OutreachTool` |

Your browser opens the tool automatically. A small window shows that it's running — keep it open while you use the tool, and close it to stop.

### Option 2 — Clone and double-click (needs Python 3.11+)

```bash
git clone https://github.com/SaraswatSahoo/outreach-tool.git
```

Then start it:

| System | How to start |
|---|---|
| Windows | Double-click **`Start Outreach Tool.bat`** |
| macOS | Double-click **`Start Outreach Tool.command`** (or run `./start.sh`) |
| Linux | Run `./start.sh` (or `python3 run.py`) |

The first start sets everything up by itself — a private Python environment (`.venv/`), the Python packages, and the website (built with Node.js if you have it, otherwise downloaded pre-built from the latest release) — then opens your browser. Later starts take a couple of seconds. If Python is missing, the launcher tells you where to get it ([python.org](https://www.python.org/downloads/); on Windows tick **"Add python.exe to PATH"**).

Options: `--no-browser`, `--port 9000` (default `8765`), `--check` (start, verify, stop).

### Where your data lives

| How you run it | Settings, contacts, database, resume |
|---|---|
| Downloaded app | Windows `%APPDATA%\OutreachTool` · macOS `~/Library/Application Support/OutreachTool` · Linux `~/.local/share/outreach-tool` |
| Cloned repo | `backend/` inside the repo (`config.json`, `outreach.db`, `uploads/`) |

These files are never committed (they're in `.gitignore`). Credentials are stored **unencrypted** in `config.json` — keep it private.

---

## ⚙️ Configuration

No manual file editing is required. All configuration is handled through the application UI:

1.  **Profile Page**: Fill in your personal details (Name, College, Skills, Bio) and upload your Resume (PDF). Don't forget to parse your resume. This data is used by the AI to personalize your outreach mails.
2.  **Settings Page**:
    - **Gmail**: Enter your Gmail address and **App Password** (see below).
    - **API Keys**: Add up to 3 Groq API keys from the [Groq Console](https://console.groq.com/keys). Requests rotate across them. Keys are never sent back to the browser once saved.
    - **Delay**: Configure the send delay (20–90s, default 60s) to stay well within Gmail's sending limits.
    - **Blocked Domains**: Domains you never want to email (also manageable from Search and the contact drawer).

### 🔑 Getting a Gmail App Password
1.  Go to [Google Account Security](https://myaccount.google.com/security).
2.  Navigate to **2-Step Verification** > **App Passwords**.
3.  Generate a new app password for "Mail".
4.  Copy the 16-character code into the Settings page.

---

## 🏗️ Architecture Overview

### CSV / Excel format

Any CSV or `.xlsx` file with a header row works (for Excel, the first sheet is used); you map the columns when importing. Recognised automatically: `email`, `name`, `company`, `role`/`title`, `notes`. Example:

```csv
email,name,company,role,notes
jane@acme.com,Jane Doe,Acme Robotics,CTO,Gave a talk on SLAM at PyCon 2026
```

### Data Flow
```mermaid
graph LR
    UI[React Frontend] <--> API[Flask Backend]
    API <--> DB[(SQLite DB)]
    API <--> Groq[Groq AI Worker]
    API <--> SMTP[Gmail SMTP]
```

- **Frontend**: A SPA built with React that polls the backend for real-time progress updates during generation.
- **Backend**: A Flask application that manages a thread pool for parallel AI requests.
- **Database**: SQLite stores all lead information, campaign history, and email templates.
- **In-Memory Progress**: Generation status is tracked in real-time, allowing the UI to show instant feedback.

---

## 🧪 Development

```bash
# Hot-reloading dev servers: Vite on http://localhost:5173 + Flask debug on :5000
python run.py --dev

# Backend: lint, format check, tests (tests use a throwaway database; no network calls)
cd backend && pip install -r requirements-dev.txt
ruff check . && ruff format --check . && python -m pytest

# Frontend lint + production build
cd frontend && npm run lint && npm run build
```

CI runs both on every push, plus the launcher (`run.py --check`) on Windows, macOS and Linux (`.github/workflows/ci.yml`).

**Making a release:** bump `backend/version.py`, then `git tag v1.2.3 && git push origin v1.2.3`. The Release workflow (`.github/workflows/release.yml`) builds the Windows/macOS/Linux executables with PyInstaller (`packaging/outreach.spec`), smoke-tests each one, and publishes them with the pre-built website.

Backend layout: `app.py` (app factory) · `routes/` (one Flask blueprint per area) · `jobs.py` (background generation/sending) · `sending.py` (starting send jobs) · `automation.py` (scheduled sends + follow-up sequences) · `scheduling.py` (send window / daily cap) · `ai_generator.py` (prompts + Groq) · `inbox_monitor.py` + `reply_detector.py` (IMAP bounce/OOO/reply detection).

How it runs: `run.py` (launcher) → `backend/serve.py` → one Waitress server on `127.0.0.1:8765` serving both the API (`/api/*`) and the built React app (`frontend/dist`).

Background schedule (while the tool runs): inbox check every 10 min, scheduled sends every minute, follow-up sequences every 15 min. Automation only works while the tool is running.

Environment variables: `PORT`, `FLASK_DEBUG` (dev server only), and `OUTREACH_DB_PATH` / `OUTREACH_CONFIG_PATH` / `OUTREACH_UPLOAD_DIR` to relocate data files. They can also go in a `.env` file in the data folder — see `backend/.env.example`.

Database changes are versioned migrations in `db.py` (`MIGRATIONS`); they run automatically at startup, and older databases are upgraded in place.

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an issue for bugs and feature requests.

## 📄 License

This project is licensed under the MIT License.
