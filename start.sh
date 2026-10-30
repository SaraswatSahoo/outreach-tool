#!/usr/bin/env sh
# Start Outreach Tool on Linux/macOS: ./start.sh  (options: --no-browser, --port N, --dev, --check)
cd "$(dirname "$0")" || exit 1

for py in python3 python3.14 python3.13 python3.12 python3.11 python; do
  if command -v "$py" >/dev/null 2>&1 &&
     "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    exec "$py" run.py "$@"
  fi
done

echo "Outreach Tool needs Python 3.11 or newer: https://www.python.org/downloads/" >&2
exit 1
