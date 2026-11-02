#!/bin/sh
# Weekly ritual while the OAuth consent screen stays in "Testing" mode:
# Google expires those refresh tokens after 7 days, so the token has to be
# re-consented roughly weekly. This does the whole thing in one command -
# re-auth, then immediately resume the pipeline (idempotent: a week that is
# already sent is a no-op).

set -u

HERE=$(cd "$(dirname "$0")/.." && pwd)
CLI="$HERE/.venv/bin/newsletter-podcast"

cd "$HERE" || exit 1

echo "== Google re-auth (a browser window will open) =="
rm -f token.json
"$CLI" auth || exit $?

echo
echo "== Resuming the pipeline =="
"$CLI" run
status=$?

echo
if [ "$status" -eq 0 ]; then
    echo "Done. Next re-auth is due in 7 days (see README, 'Weekly automation')."
else
    echo "Pipeline exited $status - see $HERE/logs/run.log"
fi

exit "$status"
