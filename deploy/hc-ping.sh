#!/usr/bin/env bash
# Ping a dead-man's switch (healthchecks.io) so it can alert when runs STOP
# arriving, e.g. the NUC is off or offline. The Telegram failure alert can't
# cover that case. Does nothing until HC_PING_URL is set in .env.
#
#   hc-ping.sh          run finished OK
#   hc-ping.sh start    run started (lets the check time each run)
#   hc-ping.sh fail     run failed
[ -n "${HC_PING_URL:-}" ] || exit 0
curl -fsS -m 10 --retry 3 -o /dev/null "${HC_PING_URL%/}${1:+/$1}"
