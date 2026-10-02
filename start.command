#!/bin/sh
panel_dir=$(CDPATH= cd "$(dirname "$0")" && pwd) || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10 or newer is required. Install Python, then try again.'
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf '%s\n' 'Python 3.10 or newer is required.'
    exit 1
fi
exec python3 "$panel_dir/launch.pyw" "$@"
