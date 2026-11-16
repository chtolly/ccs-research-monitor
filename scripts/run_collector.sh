#!/usr/bin/env bash
# Run the collector inside the Claude routine's own cloud sandbox. Used when GitHub's
# scheduled collector has not delivered today's list (GitHub often starts it hours late,
# or skips it). Does not depend on GitHub's timer at all.
#   bash scripts/run_collector.sh
# Last line printed: FRESH (today's list is on main and pulled) or STALE (reason).
set -u
cd "$(dirname "$0")/.."
TODAY=$(TZ=Europe/Oslo date +%F)

main_date() {
  git fetch -q origin main 2>/dev/null
  git show origin/main:data/candidates.json 2>/dev/null |
    python3 -c "import json,sys;print(json.load(sys.stdin).get('digest_date',''))" 2>/dev/null
}
use_main() { git reset -q --hard origin/main && echo "FRESH: candidates.json dated $TODAY pulled"; exit 0; }

[ "$(main_date)" = "$TODAY" ] && use_main
git reset -q --hard origin/main

# Python packages for the collector (venv first; fall back to pip variants)
PY=python3
if ! python3 -c "import feedparser, requests, yaml, dateutil" 2>/dev/null; then
  if python3 -m venv /tmp/collector-venv >/dev/null 2>&1 && /tmp/collector-venv/bin/pip install -q -r requirements.txt; then
    PY=/tmp/collector-venv/bin/python
  else
    python3 -m pip install -q -r requirements.txt 2>/dev/null ||
      python3 -m pip install -q --user -r requirements.txt 2>/dev/null ||
      python3 -m pip install -q --break-system-packages -r requirements.txt ||
      { echo "STALE: could not install the collector's Python packages"; exit 1; }
  fi
fi

$PY scripts/collect.py > /tmp/collector.log 2>&1 || { tail -5 /tmp/collector.log; echo "STALE: collector crashed (see above)"; exit 1; }
tail -1 /tmp/collector.log
[ "$(python3 -c "import json;print(json.load(open('data/candidates.json')).get('digest_date',''))")" = "$TODAY" ] ||
  { echo "STALE: collector ran but did not write today's list"; exit 1; }

git add data/candidates.json data/seen_links.json data/source_health.json
git commit -qm "Candidates $TODAY (collected by the Claude routine)" || true
for i in 1 2 3 4 5; do
  git push -q origin HEAD:main 2>/dev/null && { echo "FRESH: today's list collected and pushed"; exit 0; }
  # GitHub's own collector may have pushed today's list meanwhile: then use that one
  [ "$(main_date)" = "$TODAY" ] && use_main
  git pull -q --rebase origin main 2>/dev/null || git rebase --abort 2>/dev/null
  sleep 5
done
echo "STALE: could not push today's list to main"; exit 1
