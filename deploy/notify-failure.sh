#!/usr/bin/env bash
# Run by stanford-dining-failed.service (OnFailure=) when a scheduled run exits
# non-zero. GitHub used to email on a failed job; without this, a broken bot on
# the server would just go quiet.
set -u

text="Dining bot run failed on $(hostname) at $(TZ=America/Los_Angeles date '+%a %-I:%M %p %Z').
Logs: journalctl --user -u stanford-dining -n 50"

IFS=',' read -ra ids <<< "${TELEGRAM_CHAT_ID:-}"
status=0
for id in "${ids[@]}"; do
  id="${id// /}"
  [ -n "$id" ] || continue
  curl -fsS -m 20 -o /dev/null \
    --data-urlencode "chat_id=$id" --data-urlencode "text=$text" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" || status=1
done
exit "$status"
