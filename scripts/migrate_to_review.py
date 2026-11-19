"""One-off migration (Nov 2026) from the daily digests to the review workflow.

Every item that was already published counts as approved. Each one becomes
data/approved/<id>.json with a record of where it came from; nothing is lost.
data/digests/ is renamed to data/runs/ (one record per agent run).
Safe to run twice: items that already exist in data/approved/ are left alone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, load_json, norm_doi, save_json  # noqa: E402

OLD = DATA / "digests"
RUNS = DATA / "runs"
APPROVED = DATA / "approved"


def content_type(it: dict) -> str:
    if it.get("doi"):
        return "paper"
    if it.get("category") == "events":
        return "event"
    return "news"


def main() -> int:
    src = OLD if OLD.exists() else RUNS
    APPROVED.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("*.json")):
        d = load_json(p, {})
        for it in d.get("items", []):
            out = APPROVED / f"{it['id']}.json"
            if out.exists():
                continue
            rec = {k: v for k, v in it.items() if k != "origin"}
            rec.update({
                "type": content_type(it),
                "paywalled": False,
                "doi": norm_doi(it.get("doi")) or None,
                "found_by": it.get("origin"),          # candidate | web_search
                "run_date": d["date"],
                "related": [],
                "origin": "agent",
                "submitted_by": f"Claude (daily run {d['date']})",
                "submitted_at": d["published_at"],
                "approved_by": "Published automatically (before the review step existed)",
                "approved_at": d["published_at"],
            })
            save_json(out, rec)
            n += 1
    if OLD.exists():
        OLD.rename(RUNS)
    state = load_json(DATA / "state.json", {})
    if "last_digest_date" in state:
        state["last_run_date"] = state.pop("last_digest_date")
        save_json(DATA / "state.json", state)
    print(f"Migrated {n} published items to data/approved/; run records are in data/runs/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
