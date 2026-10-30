#!/usr/bin/env bash
# Start the backend (Flask, :5000) and frontend (Vite, :5173) together.
# First run creates backend/venv and installs dependencies. Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d backend/venv ]; then
  echo "Creating Python virtualenv..."
  python3 -m venv backend/venv
  backend/venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install --silent)
fi

trap 'kill 0' EXIT
(cd backend && venv/bin/python app.py) &
(cd frontend && npm run dev) &
wait
