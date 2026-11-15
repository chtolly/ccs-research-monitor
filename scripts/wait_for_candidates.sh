#!/usr/bin/env bash
# Wait until data/candidates.json on origin/main is dated today (Europe/Oslo),
# then pull it. Used by the Claude routine when the collector is running late.
#   bash scripts/wait_for_candidates.sh [max_minutes]   (default 15)
# Exit 0 = fresh candidates pulled; exit 1 = still stale after the wait.
set -u
TODAY=$(TZ=Europe/Oslo date +%F)
MAX=${1:-15}
END=$(( $(date +%s) + MAX * 60 ))
while :; do
  git fetch -q origin main 2>/dev/null
  D=$(git show origin/main:data/candidates.json 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('digest_date',''))" 2>/dev/null)
  if [ "$D" = "$TODAY" ]; then
    git pull -q --rebase origin main && echo "FRESH: candidates.json dated $TODAY pulled" && exit 0
  fi
  [ "$(date +%s)" -ge "$END" ] && echo "STALE: candidates.json still dated ${D:-none} after $MAX min" && exit 1
  sleep 30
done
