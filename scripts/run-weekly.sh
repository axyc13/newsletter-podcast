#!/bin/sh
# launchd entry point for the weekly pipeline.
#
# The pipeline's own error handling (notify.send_failure) can only email you
# once Python has started. Failures before that point - a missing venv, a
# macOS permission error while the interpreter reads pyvenv.cfg, a broken
# shebang - used to be completely silent: nothing in logs/run.log and no email.
# This wrapper makes those loud: any non-zero exit is recorded and announced.
#
# Paths are derived from this script, so the checkout can live anywhere.

set -u

HERE=$(cd "$(dirname "$0")/.." && pwd)
LOG_DIR="$HERE/logs"
ERR_LOG="$LOG_DIR/launchd.err.log"
CLI="$HERE/.venv/bin/newsletter-podcast"

mkdir -p "$LOG_DIR" || exit 1

notify() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"Newsletter podcast failed\"" \
        >/dev/null 2>&1 || true
}

if [ ! -x "$CLI" ]; then
    printf '%s startup: %s not found or not executable; rebuild the venv:\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$CLI" >>"$ERR_LOG"
    printf '    cd %s && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"\n' \
        "$HERE" >>"$ERR_LOG"
    notify "venv missing - rebuild it in $HERE"
    exit 78
fi

"$CLI" run
status=$?

if [ "$status" -ne 0 ]; then
    printf '%s pipeline exited %s (details: %s)\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$status" "$LOG_DIR/run.log" >>"$ERR_LOG"

    # A consent screen left in Testing has its refresh tokens expired by Google
    # after 7 days, and the pipeline cannot email about that failure because
    # Gmail is the credential that broke. Point at the fix instead.
    TOKEN="$HERE/token.json"
    if [ ! -f "$TOKEN" ] || [ -n "$(find "$TOKEN" -mtime +6 2>/dev/null)" ]; then
        printf '%s note: %s is missing or older than 6 days; Testing-mode refresh tokens expire after 7.\n' \
            "$(date '+%Y-%m-%d %H:%M:%S')" "$TOKEN" >>"$ERR_LOG"
        printf '    fix: %s/scripts/reauth.sh\n' "$HERE" >>"$ERR_LOG"
        notify "Google token likely expired - run scripts/reauth.sh"
    else
        notify "run exited $status - see logs/run.log"
    fi
fi

exit "$status"
