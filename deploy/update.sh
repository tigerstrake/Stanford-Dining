#!/usr/bin/env bash
# Deploy whatever is on main. Runs before every scheduled run
# (stanford-dining.service ExecStartPre), so a push is live by the next run.
# Best-effort throughout: if GitHub or PyPI is unreachable, the code already
# checked out still runs.
#
# Everything is inside main() so bash has read the whole script before
# `git pull` rewrites this file underneath it.

main() {
  set -u
  cd "$(dirname "$0")/.." || exit 1
  local before after units=~/.config/systemd/user changed=0
  before=$(git rev-parse HEAD)
  git pull --ff-only --quiet || echo "update.sh: git pull failed; staying on ${before:0:7}" >&2
  after=$(git rev-parse HEAD)

  .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt \
    || echo "update.sh: pip install failed" >&2

  [ "$before" = "$after" ] && return 0
  echo "update.sh: updated ${before:0:7} -> ${after:0:7}"

  # Keep the installed units in step with deploy/systemd/.
  for f in deploy/systemd/*; do
    cmp -s "$f" "$units/$(basename "$f")" && continue
    cp "$f" "$units/" && changed=1 && echo "update.sh: installed $(basename "$f")"
  done
  [ "$changed" = 1 ] && systemctl --user daemon-reload

  # The bot is long-running, so it only picks up new code on a restart.
  systemctl --user try-restart stanford-dining-bot.service || true
}

main "$@"
exit
