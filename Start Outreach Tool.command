#!/bin/sh
# macOS: double-click in Finder to start Outreach Tool (opens a Terminal window).
cd "$(dirname "$0")" || exit 1
exec ./start.sh "$@"
