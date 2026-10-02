#!/bin/sh
panel_dir=$(CDPATH= cd "$(dirname "$0")" && pwd) || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3 is required to run this script.'
    exit 1
fi
exec python3 "$panel_dir/stop.py"
