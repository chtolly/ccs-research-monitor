"""Check-and-publish step (free, runs on GitHub Actions).

  python scripts/build.py --check data/inbox/2026-11-23.json
      Offline format check only (no network). Claude runs this before pushing.

  python scripts/build.py
      1. Verifies every item in new/changed inbox drafts from the weekly agent run: required
         fields, allowed topic and type, link really opens, page title matches (web-search
         items), date is recent, not already published, rejected or waiting for review.
         Related links are opened and checked too. Saves a run record in data/runs/<date>.json
         and sends the accepted items to the PENDING review queue (nothing goes public here).
      2. Builds the public website into _site/ from data/approved/ only (items an approver
         approved), plus the review page in _site/review/.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, OSLO, ROOT, item_id, load_json, norm_doi, norm_title, norm_url,  # noqa: E402
                    oslo_now, oslo_today, review_api, save_json, strip_html, title_similarity)

INBOX = DATA / "inbox"
RUNS = DATA / "runs"
APPROVED = DATA / "approved"
REJECTED = DATA / "rejected"
SITE = ROOT / "_site"
REVIEW_SRC = ROOT / "site" / "review"
REQUIRED = ["title", "source", "date", "url", "category", "type", "summary", "why_it_matters", "origin"]
TYPES = ["paper", "report", "news", "event"]
TYPE_LABEL = {"paper": "Research paper", "report": "Technical report", "news": "News", "event": "Event"}
# Sections of the public page, in order: (anchor, heading, content types)
SECTIONS = [("news", "News & projects", ["news"]),
            ("research", "Research & publications", ["paper", "report"]),
            ("events", "Conferences, courses & calls", ["event"])]
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ════════════════════════════════════════════════════════════════════════════
#  1. Format check (offline)
# ════════════════════════════════════════════════════════════════════════════
def check_related(links, tag: str) -> list[str]:
    errs = []
    if links is None:
        return errs
    if not isinstance(links, list):
        return [f"{tag}: 'related' must be a list"]
    if len(links) > 5:
        errs.append(f"{tag}: at most 5 related links")
    for j, r in enumerate(links):
        t = f"{tag} related link {j + 1}"
        if not isinstance(r, dict):
            errs.append(f"{t}: must be an object with title, url, source, reason")
            continue
        for k in ("title", "url", "source", "reason"):
            if not str(r.get(k) or "").strip():
                errs.append(f"{t}: missing '{k}'")
        if r.get("url") and not str(r["url"]).startswith(("http://", "https://")):
            errs.append(f"{t}: url must start with https://")
        if len(str(r.get("reason", ""))) > 300:
            errs.append(f"{t}: reason should be one line (≤300 characters)")
    return errs


def check_format(doc: dict, categories: list[str], candidate_ids: set[str]) -> list[str]:
    errs = []
    if not DATE_RX.match(str(doc.get("date", ""))):
        errs.append("top-level 'date' must be YYYY-MM-DD")
    items = doc.get("items")
    if not isinstance(items, list):
        return errs + ["'items' must be a list (use [] when there is nothing new)"]
    for i, it in enumerate(items):
        tag = f"item {i + 1} ({str(it.get('title', ''))[:40]!r})"
        for k in REQUIRED:
            if k == "date" and it.get("origin") == "web_search":
                continue  # the publish step reads the date from the page itself
            if not str(it.get(k) or "").strip():
                errs.append(f"{tag}: missing '{k}'")
        if it.get("category") not in categories:
            errs.append(f"{tag}: category must be one of {categories}")
        if it.get("type") not in TYPES:
            errs.append(f"{tag}: type must be one of {TYPES}")
        if it.get("type") == "event" and not it.get("event_date"):
            errs.append(f"{tag}: events need 'event_date'")
        if "paywalled" in it and not isinstance(it["paywalled"], bool):
            errs.append(f"{tag}: paywalled must be true or false")
        if it.get("origin") not in ("candidate", "web_search"):
            errs.append(f"{tag}: origin must be 'candidate' or 'web_search'")
        if it.get("origin") == "candidate" and it.get("candidate_id") not in candidate_ids:
            errs.append(f"{tag}: candidate_id {it.get('candidate_id')!r} not found in data/candidates.json")
        if it.get("date") and not DATE_RX.match(str(it["date"])):
            errs.append(f"{tag}: date must be YYYY-MM-DD")
        if it.get("event_date") and not DATE_RX.match(str(it["event_date"])):
            errs.append(f"{tag}: event_date must be YYYY-MM-DD")
        if not str(it.get("url", "")).startswith(("http://", "https://")):
            errs.append(f"{tag}: url must start with https://")
        s = str(it.get("summary", ""))
        if s and not (80 <= len(s) <= 900):
            errs.append(f"{tag}: summary should be 2–3 sentences (80–900 characters), has {len(s)}")
        if len(str(it.get("why_it_matters", ""))) > 350:
            errs.append(f"{tag}: why_it_matters should be one line (≤350 characters)")
        errs += check_related(it.get("related"), tag)
    ups = doc.get("related_updates", [])
    if not isinstance(ups, list):
        errs.append("'related_updates' must be a list")
    else:
        for i, u in enumerate(ups):
            tag = f"related_updates {i + 1}"
            if not isinstance(u, dict) or not u.get("item_id"):
                errs.append(f"{tag}: needs 'item_id' (from needs_related in data/candidates.json)")
                continue
            errs += check_related(u.get("related") or [], tag)
    return errs


# ════════════════════════════════════════════════════════════════════════════
#  2. Verification (network)
# ════════════════════════════════════════════════════════════════════════════
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
STOP = set("the a an and or of for to in on at by with from is are be as its new into over under this that".split())


def tokens(t: str) -> set[str]:
    return {w for w in norm_title(t).split() if len(w) > 2 and w not in STOP}


def fetch(url: str):
    import requests
    try:
        return requests.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en,nb;q=0.8,sv;q=0.6,da;q=0.6"},
                            timeout=25, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return e


def doi_exists(doi: str) -> bool:
    import requests
    try:
        r = requests.get(f"https://doi.org/api/handles/{doi}", timeout=20)
        return r.ok and r.json().get("responseCode") == 1
    except Exception:  # noqa: BLE001
        return False


def host_of(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _blocking_hosts() -> set[str]:
    try:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        return {host_of(c["url"]) for c in cfg.get("claude_checks", [])}
    except Exception:  # noqa: BLE001
        return set()


BLOCKING_HOSTS = _blocking_hosts()


def verify_item(it: dict, cands: dict[str, dict]) -> str | None:
    """Return None if OK, else the reason the item is rejected."""
    url = it["url"]
    if it["origin"] == "candidate":
        c = cands.get(it.get("candidate_id"))
        if not c:
            return "candidate_id not in today's candidate list"
        if norm_url(c["url"]) != norm_url(url):
            return "URL differs from the collected candidate"
        if c.get("kind") != "event" and title_similarity(c["title"], it["title"]) < 0.6:
            return "title differs from the collected candidate"
        if c.get("doi") and doi_exists(c["doi"]):
            return None
        r = fetch(url)
        if isinstance(r, Exception):
            return f"link did not open ({type(r).__name__})"
        if r.status_code in (404, 410) or r.status_code >= 500:
            return f"link broken (HTTP {r.status_code})"
        return None  # 401/403/429 on a feed/API-sourced link = site blocks robots, link is real
    # web_search items: the page must open AND show the claimed title
    r = fetch(url)
    if isinstance(r, Exception):
        return f"link did not open ({type(r).__name__})"
    if r.status_code in (401, 403, 429) and host_of(url) in BLOCKING_HOSTS:
        # Known sites that block GitHub's servers (config.yaml → claude_checks). Claude opened
        # the page itself; accept it if it gave a date (the date window is still checked).
        if not it.get("date"):
            return f"site blocks the checker (HTTP {r.status_code}) and no date was given"
        it["date_unconfirmed"] = True
        return None
    if r.status_code >= 400:
        return f"could not verify page (HTTP {r.status_code})"
    page = tokens(strip_html(r.text[:600_000]))
    want = tokens(it["title"])
    if not want:
        return "title too short to verify"
    overlap = len(want & page) / len(want)
    if overlap < 0.6:
        return f"page text does not match the title (overlap {overlap:.0%})"
    page_date = extract_page_date(r.text)
    if page_date:
        it["date"] = page_date.isoformat()  # the page itself is the authority on the date
    elif not it.get("date"):
        return "could not confirm the publication date from the page"
    else:
        it["date_unconfirmed"] = True
    return None


DATE_PATTERNS = [
    r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:article:published_time|datePublished|date|pubdate|publishdate|publish-date|citation_publication_date|citation_online_date|dc\.date|dcterms\.created|DC\.date\.issued|sailthru\.date|parsely-pub-date)["\'][^>]*content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\'](?:article:published_time|datePublished|date|pubdate|citation_publication_date|dc\.date)["\']',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'<time[^>]+datetime=["\']([^"\']+)',
]


def extract_page_date(text: str):
    from dateutil import parser as dp
    head = text[:400_000]
    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, head, re.I):
            try:
                d = dp.parse(m.group(1).strip(), fuzzy=True).date()
                if date(2000, 1, 1) < d <= oslo_today() + timedelta(days=1):
                    return d
            except Exception:  # noqa: BLE001
                continue
    return None


def date_ok(it: dict, since: date, today: date) -> str | None:
    if (it.get("type") == "event" or it.get("category") == "events") and it.get("event_date"):
        if date.fromisoformat(it["event_date"]) < today:
            return "event date has passed"
        return None
    d = date.fromisoformat(it["date"])
    if d > today + timedelta(days=1):
        return None if it["origin"] == "candidate" else "date is in the future"
    if it["origin"] == "candidate":
        return None  # collector already applied the date window
    if d < since - timedelta(days=2):
        return f"published {d}, before the window starting {since}"
    return None



def verify_related(links: list[dict], item_url: str, network: bool) -> tuple[list[dict], list[dict]]:
    """Keep only related links whose page really opens and shows the claimed title."""
    kept, dropped, seen = [], [], {norm_url(item_url)}
    for r in links or []:
        r = {k: str(r.get(k) or "").strip() for k in ("title", "url", "source", "reason")}
        nu = norm_url(r["url"])
        if nu in seen:
            dropped.append({**r, "reason_dropped": "same link twice (or the item itself)"})
            continue
        seen.add(nu)
        if network:
            resp = fetch(r["url"])
            why = None
            if isinstance(resp, Exception):
                why = f"link did not open ({type(resp).__name__})"
            elif resp.status_code in (401, 403, 429):
                if host_of(r["url"]) not in BLOCKING_HOSTS:
                    why = f"could not verify page (HTTP {resp.status_code})"
            elif resp.status_code >= 400:
                why = f"link broken (HTTP {resp.status_code})"
            else:
                want = tokens(r["title"])
                page = tokens(strip_html(resp.text[:600_000]))
                if want and len(want & page) / len(want) < 0.5:
                    why = "page text does not match the title"
            if why:
                dropped.append({**r, "reason_dropped": why})
                continue
        kept.append(r)
        if len(kept) == 5:
            break
    return kept, dropped


# ════════════════════════════════════════════════════════════════════════════
#  3. Review queue (Cloudflare Worker) — only used by GitHub Actions
# ════════════════════════════════════════════════════════════════════════════
def review(path: str, payload: dict | None = None):
    return review_api(path, payload, base=(load_config().get("review") or {}).get("api_url"))


# ════════════════════════════════════════════════════════════════════════════
#  4. Process an agent draft into the review queue
# ════════════════════════════════════════════════════════════════════════════
def load_dir(d: Path) -> list[dict]:
    return [x for x in (load_json(p, None) for p in sorted(d.glob("*.json"))) if x]


def process_inbox(path: Path, state: dict, pending: list[dict], network: bool = True, send: bool = True) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    cand_doc = load_json(DATA / "candidates.json", {})
    cands = {c["id"]: c for c in cand_doc.get("items", [])}
    categories = load_categories()
    errs = check_format(doc, [c["key"] for c in categories], set(cands))
    run_date = doc["date"]
    today = oslo_today()
    since = date.fromisoformat(cand_doc.get("since") or (date.fromisoformat(run_date) - timedelta(days=10)).isoformat())
    max_items = cand_doc.get("max_items_per_run") or cand_doc.get("max_items_per_day") or 60

    known = load_dir(APPROVED) + load_dir(REJECTED)
    k_urls = {norm_url(k["url"]) for k in known}
    k_dois = {norm_doi(k.get("doi")) for k in known if k.get("doi")}
    k_titles = [k["title"] for k in known]
    p_ids = {p["id"] for p in pending}
    p_urls = {norm_url(p["url"]) for p in pending}

    accepted, dropped = [], []
    for i, it in enumerate(doc.get("items", [])):
        tag = f"item {i + 1} ({str(it.get('title', ''))[:40]!r}): "
        mine = [x[len(tag):] for x in errs if x.startswith(tag)]
        if mine:
            dropped.append({"title": it.get("title"), "url": it.get("url"), "reason": "; ".join(mine)})
            print(f"  ✗ dropped (format): {str(it.get('title'))[:70]} — {'; '.join(mine)}")
            continue
        it = {k: (v.strip() if isinstance(v, str) else v) for k, v in it.items()}
        doi = norm_doi(it.get("doi") or (cands.get(it.get("candidate_id"), {}).get("doi")))
        nid = item_id(it["url"])
        reason = None
        if norm_url(it["url"]) in k_urls or (doi and doi in k_dois) or any(title_similarity(it["title"], t) > 0.9 for t in k_titles):
            reason = "already published or rejected earlier"
        elif norm_url(it["url"]) in p_urls and nid not in p_ids:
            reason = "already waiting for review"
        elif any(norm_url(a["url"]) == norm_url(it["url"]) or title_similarity(a["title"], it["title"]) > 0.9 for a in accepted):
            reason = "duplicate within this run"
        else:
            reason = (verify_item(it, cands) if network else None) or \
                (date_ok(it, since, today) if it.get("date") else "no publication date")
        if reason:
            dropped.append({"title": it["title"], "url": it["url"], "reason": reason})
            print(f"  ✗ dropped: {it['title'][:70]} — {reason}")
            continue
        it["related"], rel_dropped = verify_related(it.get("related") or [], it["url"], network)
        if rel_dropped:
            it["related_dropped"] = rel_dropped
        it["id"] = nid
        it["found_by"] = it.pop("origin")  # candidate | web_search
        it["run_date"] = run_date
        it["paywalled"] = bool(it.get("paywalled")) and it["type"] == "report"
        if doi:
            it["doi"] = doi
        accepted.append(it)
        print(f"  ✓ {it['type']:<7} {it['category']:<15} {it['title'][:64]}  (+{len(it['related'])} related)")
    if len(accepted) > max_items:
        for it in accepted[max_items:]:
            dropped.append({"title": it["title"], "url": it["url"], "reason": "over the per-run item cap"})
        accepted = accepted[:max_items]

    updates = []
    for u in doc.get("related_updates") or []:
        kept, rel_dropped = verify_related(u.get("related") or [], u.get("item_url") or "", network)
        if kept:
            updates.append({"item_id": u["item_id"], "related": kept})
        for r in rel_dropped:
            dropped.append({"title": r["title"], "url": r["url"], "reason": f"related link for {u['item_id']}: {r['reason_dropped']}"})

    sent = None
    if send:
        to_send = [{k: v for k, v in it.items() if k != "related_dropped"} for it in accepted]
        sent = review("/api/agent/ingest", {"run_date": run_date, "items": to_send, "related_updates": updates})
        if sent is None:
            raise SystemExit("REVIEW_API_URL / REVIEW_INGEST_KEY are not set, so the items cannot be sent to the review queue. "
                             "Nothing was lost: the draft stays in data/inbox/ and is sent on the next run.")
        print(f"Sent to the review queue: {sent.get('added')} added, {len(sent.get('skipped', []))} already known, "
              f"{sent.get('related_added')} related-link updates.")
    run = {
        "date": run_date,
        "processed_at": oslo_now().isoformat(timespec="seconds"),
        "searches_used": doc.get("searches_used"),
        "notes": doc.get("notes", ""),
        "sent_to_review": accepted,
        "related_updates": updates,
        "dropped": dropped,
        "review_queue_result": sent,
        "candidates_seen": len(cands),
        "candidates_date": cand_doc.get("digest_date"),  # which collector list was used
    }
    save_json(RUNS / f"{run_date}.json", run)
    state["last_run_date"] = max(state.get("last_run_date") or run_date, run_date)
    state.setdefault("processed", {})[path.name] = hashlib.sha1(path.read_bytes()).hexdigest()
    print(f"Run {run_date}: {len(accepted)} items to review, {len(dropped)} dropped.")
    return run


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_categories() -> list[dict]:
    return load_config()["categories"]


# ════════════════════════════════════════════════════════════════════════════
#  5. Website (approved items only)
# ════════════════════════════════════════════════════════════════════════════
def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def nice_date(d: str) -> str:
    try:
        return datetime.fromisoformat(d[:10]).strftime("%-d %b %Y")
    except Exception:  # noqa: BLE001
        return d or ""


def oslo_dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(OSLO) if dt.tzinfo else dt.replace(tzinfo=OSLO)


def week_key(iso: str) -> str:
    y, w, _ = oslo_dt(iso).isocalendar()
    return f"{y}-W{w:02d}"


def week_label(key: str) -> str:
    y, w = key.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    sun = mon + timedelta(days=6)
    span = f"{mon.day}–{sun.day} {sun:%b %Y}" if mon.month == sun.month else f"{mon.day} {mon:%b} – {sun.day} {sun:%b %Y}"
    return f"Week {int(w)} · {span}"


CSS = """
:root{--bg:#f7f7f4;--card:#fff;--ink:#1b1d1f;--muted:#5d6166;--line:#e3e3de;--accent:#0b6e69;--accent-soft:#e3f1ef;--chip:#eeeeea;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#111314;--card:#1a1d1f;--ink:#e9eaeb;--muted:#9da2a7;--line:#2b2f32;--accent:#5cc8bf;--accent-soft:#16302e;--chip:#24282b;--warn:#e0b45a}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent)}
.wrap{max-width:860px;margin:0 auto;padding:0 16px}
header{padding:28px 0 12px;border-bottom:1px solid var(--line)}
h1{font-size:1.55rem;line-height:1.25;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 10px}
.meta{font-size:.88rem;color:var(--muted)}
nav.top{display:flex;gap:16px;font-size:.92rem;margin-top:10px;flex-wrap:wrap}
.tools{position:sticky;top:0;background:var(--bg);padding:12px 0;z-index:5;border-bottom:1px solid var(--line)}
.search{width:100%;padding:10px 12px;font-size:1rem;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{border:1px solid var(--line);background:var(--chip);color:var(--ink);border-radius:999px;padding:4px 11px;font-size:.84rem;cursor:pointer}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
@media (prefers-color-scheme:dark){.chip[aria-pressed=true]{color:#0b1514}}
h2{font-size:1.25rem;margin:26px 0 6px}
h3.sec{font-size:1.05rem;margin:24px 0 8px;color:var(--accent);text-transform:uppercase;letter-spacing:.04em}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 10px}
.item h4{margin:0 0 2px;font-size:1.03rem;line-height:1.35}
.item h4 a{color:var(--ink);text-decoration:none}.item h4 a:hover{text-decoration:underline}
.en{color:var(--muted);font-size:.92rem;margin:0 0 4px;font-style:italic}
.src{font-size:.84rem;color:var(--muted);margin:0 0 8px}
.item p{margin:0 0 6px}
.why{background:var(--accent-soft);border-radius:6px;padding:6px 10px;font-size:.93rem;margin:6px 0 0}
.tag{display:inline-block;font-size:.75rem;border-radius:4px;padding:1px 6px;background:var(--chip);color:var(--muted);margin-left:6px;vertical-align:1px}
.tag.type{background:var(--accent-soft);color:var(--accent)}
.tag.pay{color:var(--warn)}
.relbtn{margin-top:8px;border:1px solid var(--line);background:var(--chip);color:var(--ink);border-radius:6px;padding:5px 10px;font-size:.86rem;cursor:pointer}
.relbtn:hover,.relbtn:focus-visible{border-color:var(--accent)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:10px;padding:18px;color:var(--muted)}
.archive{list-style:none;padding:0;margin:0;columns:2 240px}
.archive li{padding:3px 0}
.about{color:var(--ink);font-size:.95rem}
footer{color:var(--muted);font-size:.85rem;padding:30px 0 40px;border-top:1px solid var(--line);margin-top:30px}
.hidden{display:none}
.daygroup{font-size:.95rem;color:var(--muted);margin:18px 0 6px;font-weight:600}
dialog#rel{border:1px solid var(--line);border-radius:12px;padding:0;max-width:560px;width:calc(100% - 32px);background:var(--card);color:var(--ink)}
dialog#rel::backdrop{background:rgba(0,0,0,.45)}
#rel .hd{display:flex;align-items:flex-start;gap:12px;padding:16px 18px 6px}
#rel h2{font-size:1.08rem;margin:0;flex:1}
#rel .for{font-size:.86rem;color:var(--muted);margin:0 18px 6px}
#rel .x{border:0;background:var(--chip);color:var(--ink);border-radius:8px;min-width:40px;min-height:40px;font-size:1.3rem;line-height:1;cursor:pointer}
#rel ul{list-style:none;margin:0;padding:4px 18px 18px}
#rel li{padding:10px 0;border-top:1px solid var(--line)}
#rel li a{font-weight:600}
#rel .why1{display:block;font-size:.9rem;color:var(--muted);margin-top:2px}
@media (max-width:640px){dialog#rel{margin:auto 0 0;width:100%;max-width:100%;border-radius:14px 14px 0 0;max-height:85vh}}
"""

JS = """
(function(){
const q=document.getElementById('q'), chips=[...document.querySelectorAll('.chip')];
const latest=document.getElementById('latest'), results=document.getElementById('results');
let data=null, cat='all';
const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){ if(!data){ data=await (await fetch(ROOT+'items.json')).json(); } return data; }
function card(it){
 const rel=(it.related||[]).length?`<button type="button" class="relbtn" data-rel="${esc(JSON.stringify(it.related))}" data-for="${esc(it.title)}">You might also be interested in (${it.related.length})</button>`:'';
 return `<article class="item"><h4><a href="${esc(it.url)}" rel="noopener" target="_blank">${esc(it.title)}</a><span class="tag type">${esc(TYPES[it.type]||'')}</span>${it.paywalled?'<span class="tag pay">paywalled</span>':''}<span class="tag">${esc(CATS[it.category]||it.category)}</span></h4>`+
 (it.title_en?`<p class="en">${esc(it.title_en)}</p>`:'')+
 `<p class="src">${esc(it.source)} · ${esc(it.date)}${it.event_date?' · event '+esc(it.event_date):''}${it.authors?' · '+esc(it.authors):''}</p><p>${esc(it.summary)}</p>`+
 (it.why_it_matters?`<p class="why"><strong>Why it matters:</strong> ${esc(it.why_it_matters)}</p>`:'')+rel+`</article>`;}
async function run(){
  if(!q) return;
  const term=q.value.trim().toLowerCase();
  if(!term && cat==='all'){ latest.classList.remove('hidden'); results.classList.add('hidden'); return; }
  const d=await load(); const words=term.split(/\\s+/).filter(Boolean);
  const hits=d.filter(it=>(cat==='all'||it.category===cat)&&words.every(w=>(it.title+' '+(it.title_en||'')+' '+it.summary+' '+it.source+' '+(it.why_it_matters||'')+' '+(TYPES[it.type]||'')).toLowerCase().includes(w)));
  let html=`<h2>${hits.length} item${hits.length===1?'':'s'} across all weeks</h2>`, wk='';
  for(const it of hits.slice(0,300)){ if(it.week!==wk){wk=it.week; html+=`<div class="daygroup">${esc(it.week_label)}</div>`;} html+=card(it); }
  if(!hits.length) html+='<p class="empty">No matching items.</p>';
  results.innerHTML=html; latest.classList.add('hidden'); results.classList.remove('hidden');
}
if(q){ q.addEventListener('input',()=>{clearTimeout(q._t);q._t=setTimeout(run,150)});
chips.forEach(c=>c.addEventListener('click',()=>{chips.forEach(x=>x.setAttribute('aria-pressed','false'));c.setAttribute('aria-pressed','true');cat=c.dataset.cat;run();})); }

// "You might also be interested in" dialog
const dlg=document.getElementById('rel'); let opener=null;
function openRel(btn){
  let links=[]; try{ links=JSON.parse(btn.dataset.rel||'[]'); }catch(e){}
  if(!links.length) return;
  document.getElementById('rel-for').textContent='Related to: '+(btn.dataset.for||'');
  document.getElementById('rel-list').innerHTML=links.map(r=>`<li><a href="${esc(r.url)}" rel="noopener" target="_blank">${esc(r.title)}</a> <span class="meta">· ${esc(r.source)}</span><span class="why1">${esc(r.reason)}</span></li>`).join('');
  opener=btn; dlg.showModal(); dlg.querySelector('.x').focus();
}
dlg.querySelector('.x').addEventListener('click',()=>dlg.close());
dlg.addEventListener('click',ev=>{ if(ev.target===dlg) dlg.close(); });
dlg.addEventListener('close',()=>{ if(opener){ opener.focus(); opener=null; } });
document.addEventListener('click',ev=>{
  const b=ev.target.closest('.relbtn'); if(b){ openRel(b); return; }
  // Opening an item (its title link) also shows its related links on this page
  const a=ev.target.closest('.item h4 a'); if(a){ const rb=a.closest('.item').querySelector('.relbtn'); if(rb) setTimeout(()=>openRel(rb),50); }
});
})();
"""

DIALOG = """<dialog id="rel" aria-labelledby="rel-h"><div class="hd"><h2 id="rel-h">You might also be interested in</h2>
<button type="button" class="x" aria-label="Close">×</button></div><p class="for" id="rel-for"></p><ul id="rel-list"></ul></dialog>"""


def public_item(it: dict) -> dict:
    keep = ["id", "title", "title_en", "source", "date", "event_date", "authors", "url", "category", "type", "paywalled",
            "summary", "why_it_matters", "related", "approved_at"]
    out = {k: it.get(k) for k in keep if it.get(k)}
    out["week"] = week_key(it["approved_at"])
    out["week_label"] = week_label(out["week"])
    return out


def render_item(it: dict, cats: dict) -> str:
    parts = [f'<article class="item"><h4><a href="{e(it["url"])}" rel="noopener" target="_blank">{e(it["title"])}</a>']
    if it.get("type") in ("paper", "report"):
        parts.append(f'<span class="tag type">{e(TYPE_LABEL[it["type"]])}</span>')
    if it.get("paywalled"):
        parts.append('<span class="tag pay">paywalled</span>')
    parts.append(f'<span class="tag">{e(cats.get(it["category"], it["category"]))}</span></h4>')
    if it.get("title_en"):
        parts.append(f'<p class="en">{e(it["title_en"])}</p>')
    meta = [e(it["source"]), e(nice_date(it["date"]))]
    if it.get("event_date"):
        meta.append("event " + e(nice_date(it["event_date"])))
    if it.get("authors"):
        meta.append(e(it["authors"]))
    parts.append(f'<p class="src">{" · ".join(meta)}</p><p>{e(it["summary"])}</p>')
    if it.get("why_it_matters"):
        parts.append(f'<p class="why"><strong>Why it matters:</strong> {e(it["why_it_matters"])}</p>')
    if it.get("also_reported_by"):
        links = ", ".join(f'<a href="{e(a.get("url"))}" rel="noopener" target="_blank">{e(a.get("source"))}</a>'
                          for a in it["also_reported_by"] if a.get("url"))
        if links:
            parts.append(f'<p class="src">Also reported by: {links}</p>')
    rel = it.get("related") or []
    if rel:
        parts.append(f'<button type="button" class="relbtn" data-rel="{e(json.dumps(rel, ensure_ascii=False))}" '
                     f'data-for="{e(it["title"])}">You might also be interested in ({len(rel)})</button>')
    parts.append("</article>")
    return "".join(parts)


def render_week(items: list[dict], categories: list[dict]) -> str:
    cats = {c["key"]: c["name"] for c in categories}
    order = {c["key"]: n for n, c in enumerate(categories)}
    out = []
    for anchor, heading, types in SECTIONS:
        sec = sorted((i for i in items if i.get("type") in types),
                     key=lambda x: (order.get(x["category"], 99), types.index(x["type"]), x.get("date") or ""))
        if sec:
            out.append(f'<h3 class="sec" id="sec-{anchor}">{e(heading)} <span class="tag">{len(sec)}</span></h3>')
            out += [render_item(i, cats) for i in sec]
    return "\n".join(out) or '<p class="empty">No items.</p>'


def page(title: str, body: str, cfg: dict, depth: int = 0, extra_head: str = "") -> str:
    pre = "../" * depth
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{e(title)}</title>
<meta name="description" content="{e(cfg['site']['subtitle'])}">
<link rel="alternate" type="application/rss+xml" title="{e(cfg['site']['title'])}" href="{pre}feed.xml">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%230b6e69'/%3E%3Ctext x='16' y='21' font-size='12' text-anchor='middle' fill='white' font-family='Arial'%3ECO2%3C/text%3E%3C/svg%3E">
<style>{CSS}</style>{extra_head}</head><body>{body}</body></html>"""


def header(cfg: dict, updated: str, depth: int = 0) -> str:
    pre = "../" * depth
    return f"""<header><div class="wrap"><h1><a href="{pre}index.html" style="color:inherit;text-decoration:none">{e(cfg['site']['title'])}</a></h1>
<p class="sub">{e(cfg['site']['subtitle'])}</p>
<p class="meta">Updated weekly (new items appear once an approver has reviewed them). Last update: <strong>{e(updated)}</strong> (Oslo time)</p>
<nav class="top"><a href="{pre}index.html#latest">This week</a><a href="{pre}index.html#upcoming">Upcoming events</a><a href="{pre}index.html#archive">Archive</a><a href="{pre}index.html#about">About</a><a href="{pre}feed.xml">RSS feed</a></nav></div></header>"""


def footer(cfg: dict, depth: int = 0) -> str:
    pre = "../" * depth
    return (f'<footer><div class="wrap">Maintained by {e(cfg["site"]["maintainer"])}. Summaries are written by Claude (AI) or by editors and '
            f'checked by an approver before they appear — always check the linked source. '
            f'<a href="{pre}review/">Review space</a> (editors and approvers only).</div></footer>')


def page_script(cfg: dict, depth: int) -> str:
    cats = {c["key"]: c["name"] for c in cfg["categories"]}
    return (f"<script>const ROOT={json.dumps('../' * depth)};const CATS={json.dumps(cats, ensure_ascii=False)};"
            f"const TYPES={json.dumps({k: v for k, v in TYPE_LABEL.items() if k in ('paper', 'report')})};{JS}</script>")


def build_site(cfg: dict) -> None:
    categories = cfg["categories"]
    approved = sorted(load_dir(APPROVED), key=lambda i: (i["approved_at"], i.get("date") or ""), reverse=True)
    rejected = load_dir(REJECTED)
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "archive").mkdir(parents=True)
    updated = oslo_dt(approved[0]["approved_at"]).strftime("%-d %B %Y, %H:%M") if approved else "not yet"

    # Data files: search (approved only) and the duplicate list for the review space
    pub = [public_item(i) for i in approved]
    (SITE / "items.json").write_text(json.dumps(pub, ensure_ascii=False), encoding="utf-8")
    known = [{"id": i["id"], "url": i["url"], "url_norm": norm_url(i["url"]), "doi": norm_doi(i.get("doi")) or None,
              "title": i["title"], "status": "published"} for i in approved]
    known += [{"id": r["id"], "url": r["url"], "url_norm": norm_url(r["url"]), "doi": norm_doi(r.get("doi")) or None,
               "title": r["title"], "status": "rejected"} for r in rejected]
    (SITE / "known.json").write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")

    # Weeks (by approval date)
    weeks: dict[str, list[dict]] = {}
    for it in approved:
        weeks.setdefault(week_key(it["approved_at"]), []).append(it)
    keys = sorted(weeks, reverse=True)
    for k in keys:
        body = header(cfg, updated, 1) + f'<main class="wrap"><h2>{e(week_label(k))}</h2>' + \
            render_week(weeks[k], categories) + "</main>" + DIALOG + footer(cfg, 1) + page_script(cfg, 1)
        (SITE / "archive" / f"{k}.html").write_text(page(f"{week_label(k)} · {cfg['site']['title']}", body, cfg, 1), encoding="utf-8")
    # Old daily archive links (before the weekly change) keep working: they forward to their week
    for p in RUNS.glob("*.json"):
        d = p.stem
        if (dk := week_key(d + "T12:00:00")) in weeks:
            (SITE / "archive" / f"{d}.html").write_text(
                f'<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><meta http-equiv="refresh" content="0; url={dk}.html">'
                f'<title>Moved</title><p>This page has moved to <a href="{dk}.html">{e(week_label(dk))}</a>.</p>', encoding="utf-8")

    this_week = week_key(oslo_now().isoformat())
    chips = '<button class="chip" data-cat="all" aria-pressed="true">All topics</button>' + "".join(
        f'<button class="chip" data-cat="{e(c["key"])}" aria-pressed="false">{e(c["name"])}</button>' for c in categories)
    if keys:
        latest = keys[0]
        n = len(weeks[latest])
        latest_html = f'<h2>{"This week" if latest == this_week else "Latest week"}: {e(week_label(latest))} <span class="tag">{n} item{"s" if n != 1 else ""}</span></h2>'
        if latest != this_week:
            latest_html = '<p class="meta">No new items have been approved yet this week; showing the most recent week.</p>' + latest_html
        latest_html += render_week(weeks[latest], categories)
    else:
        latest_html = '<p class="meta">No items have been approved yet.</p>'
    arch = "".join(f'<li><a href="archive/{k}.html">{e(week_label(k))}</a> <span class="tag">{len(weeks[k])}</span></li>' for k in keys)

    # Upcoming conferences, courses and deadlines stay visible until their date passes
    today_d = oslo_today().isoformat()
    upcoming, seen_ev = [], set()
    for it in sorted((i for i in approved if i.get("type") == "event" and (i.get("event_date") or "") >= today_d),
                     key=lambda i: i["event_date"]):
        if norm_url(it["url"]) not in seen_ev:
            seen_ev.add(norm_url(it["url"]))
            upcoming.append(it)
    rows = "".join(f'<li><span class="tag">{e(nice_date(i["event_date"]))}</span> <a href="{e(i["url"])}" rel="noopener" target="_blank">'
                   f'{e(i["title"])}</a> <span class="meta">· {e(i["source"])}</span></li>' for i in upcoming[:30])
    events_html = (f'<section id="upcoming"><h2>Upcoming conferences, courses &amp; deadlines</h2><ul class="archive">{rows}</ul></section>'
                   if upcoming else '<section id="upcoming"></section>')

    cat_list = "".join(f"<li><strong>{e(c['name'])}</strong> — {e(c['description'])}</li>" for c in categories)
    n_src = len(cfg.get("feeds", [])) + len(cfg.get("watch_pages", [])) + len(cfg.get("event_pages", []))
    about = f"""<section id="about" class="about"><h2>About</h2>
<p>This page tracks new research, technical reports, news, projects, policy and events relevant to the {e(cfg['site']['project'])}. It is updated once a week: every Monday morning (Oslo time) new items are collected, and they appear here after an approver has reviewed them.</p>
<p><strong>Sections:</strong> <em>News &amp; projects</em> (project launches, FIDs, awards, contracts, partnerships, ship orders, port and terminal developments, policy news), <em>Research &amp; publications</em> (journal papers and technical or industry reports; reports whose full text is not free are marked “paywalled” and summarised only from their public page) and <em>Conferences, courses &amp; calls</em>. Every item is tagged with one topic:</p><ul>{cat_list}</ul>
<p><strong>How it works:</strong> each Monday a script collects new items from about {n_src} organisation news feeds and pages (SINTEF and its CCS research centres gigaCCS and NCCS, NTNU, CCUS Norway, Gassnova, Northern Lights, Longship, the IEA, Global CCS Institute, IEAGHG, ZEP, DNV, Bellona, CATF, the EU Innovation Fund, DG CLIMA, the Danish Energy Agency and others) and from the OpenAlex and Crossref research databases. Claude (an AI model by Anthropic) adds web searches, removes off-topic and repeated items and writes the summaries. Registered editors can also submit items. Every link is checked automatically, and nothing appears here until an approver has approved it.</p>
<p><strong>“You might also be interested in”:</strong> some items have a few related links on neighbouring topics (for example hydrogen or ammonia shipping, port electrification or energy policy). They were opened and checked, and approved together with the item.</p>
<p><strong>Please note:</strong> summaries and “why it matters” notes can contain mistakes. Always check the linked source before relying on an item. Items in Norwegian, Swedish or Danish keep their original title, with an English translation underneath.</p>
<p><strong>Missing a source?</strong> Suggestions, especially Swedish and Danish CCS organisations, are welcome. Send them to the maintainer.</p>
<p>Maintained by {e(cfg['site']['maintainer'])}. Subscribe via the <a href="feed.xml">RSS feed</a>.</p></section>"""
    body = header(cfg, updated) + f"""<div class="tools"><div class="wrap"><input id="q" class="search" type="search" placeholder="Search all items (e.g. ship, Greensand, ETS, DNV)…" aria-label="Search all items">
<div class="chips" role="group" aria-label="Filter by topic">{chips}</div></div></div>
<main class="wrap"><section id="latest">{latest_html}</section><section id="results" class="hidden" aria-live="polite"></section>{events_html}
<section id="archive"><h2>Archive</h2><ul class="archive">{arch or '<li>No earlier weeks yet.</li>'}</ul></section>{about}</main>{DIALOG}{footer(cfg)}{page_script(cfg, 0)}"""
    (SITE / "index.html").write_text(page(cfg["site"]["title"], body, cfg), encoding="utf-8")

    # RSS (newest approvals first)
    base = cfg["site"]["base_url"].rstrip("/") + "/"
    cats = {c["key"]: c["name"] for c in categories}
    rss_items = []
    for it in approved[:100]:
        pub_date = oslo_dt(it["approved_at"]).strftime("%a, %d %b %Y %H:%M:%S %z")
        label = TYPE_LABEL.get(it.get("type"), "")
        desc = f"[{label} · {cats.get(it['category'], it['category'])}] {it['source']} ({it['date']}). {it['summary']}" + \
            (f" Why it matters: {it['why_it_matters']}" if it.get("why_it_matters") else "")
        title = it["title"] + (f" ({it['title_en']})" if it.get("title_en") else "") + (" (paywalled)" if it.get("paywalled") else "")
        rss_items.append(f"<item><title>{e(title)}</title><link>{e(it['url'])}</link><guid isPermaLink=\"false\">{e(it['id'])}</guid>"
                         f"<pubDate>{pub_date}</pubDate><category>{e(cats.get(it['category']))}</category><description>{e(desc)}</description></item>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>{e(cfg['site']['title'])}</title><link>{e(base)}</link>
<atom:link href="{e(base)}feed.xml" rel="self" type="application/rss+xml"/><description>{e(cfg['site']['subtitle'])}</description><language>en</language>
{''.join(rss_items)}</channel></rss>"""
    (SITE / "feed.xml").write_text(rss, encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    # Review space page (static; all data comes from the review API after login)
    if REVIEW_SRC.exists():
        shutil.copytree(REVIEW_SRC, SITE / "review")
        rv = cfg.get("review") or {}
        save_json(SITE / "review" / "config.json", {
            "api_url": rv.get("api_url", ""), "login_url": rv.get("login_url", ""),
            "categories": [{"key": c["key"], "name": c["name"]} for c in categories],
            "types": [{"key": k, "name": {"paper": "Research paper", "report": "Technical report", "news": "News / project announcement",
                                          "event": "Conference, course or call"}[k]} for k in TYPES],
        })

    # Lists used to avoid repeats: by the collector (history.json) and by Claude (recent_titles.json)
    history = [{"id": i["id"], "url": i["url"], "doi": norm_doi(i.get("doi")), "title": i["title"], "date": i.get("date"),
                "status": "published", "digest_date": oslo_dt(i["approved_at"]).date().isoformat()} for i in approved]
    history += [{"id": r["id"], "url": r["url"], "doi": norm_doi(r.get("doi")), "title": r["title"], "date": None,
                 "status": "rejected", "digest_date": oslo_dt(r["rejected_at"]).date().isoformat()} for r in rejected]
    save_json(DATA / "history.json", history)
    cutoff = (oslo_today() - timedelta(days=90)).isoformat()
    recent = [{"title": i["title"], "source": i["source"], "date": i.get("date"), "status": "published"}
              for i in approved if oslo_dt(i["approved_at"]).date().isoformat() >= cutoff]
    recent += [{"title": r["title"], "status": "rejected"} for r in rejected if r["rejected_at"][:10] >= cutoff]
    save_json(DATA / "recent_titles.json", recent)
    print(f"Site built: {len(approved)} approved items in {len(keys)} weeks, {len(rejected)} rejected → {SITE}")


# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="INBOX_FILE", help="offline format check only")
    ap.add_argument("--no-network", action="store_true", help="skip link checks (local testing)")
    ap.add_argument("--no-send", action="store_true", help="do not send to the review queue (local testing)")
    ap.add_argument("--site-only", action="store_true", help="only rebuild the website")
    args = ap.parse_args()

    if args.check:
        doc = json.loads(Path(args.check).read_text(encoding="utf-8"))
        cand = load_json(DATA / "candidates.json", {})
        errs = check_format(doc, cand.get("categories") or [], {c["id"] for c in cand.get("items", [])})
        if cand.get("digest_date") != doc.get("date"):
            errs.append(f"data/candidates.json is dated {cand.get('digest_date')!r}, not {doc.get('date')!r}: the collector has not "
                        "delivered this week's list. Start it and wait (ROUTINE.md step 1); never push a draft from a stale list.")
        if errs:
            print("FORMAT ERRORS — fix these and run the check again:")
            print("\n".join(" - " + x for x in errs))
            return 1
        print(f"OK: {len(doc['items'])} items, {len(doc.get('related_updates') or [])} related-link updates, format valid.")
        return 0

    cfg = load_config()
    state = load_json(DATA / "state.json", {})
    if not args.site_only:
        todo = [p for p in sorted(INBOX.glob("*.json"))
                if state.get("processed", {}).get(p.name) != hashlib.sha1(p.read_bytes()).hexdigest()]
        if todo:
            send = not args.no_send
            st = review("/api/agent/state") if send else None
            if send and st is None:
                print("::error::The review queue is not configured (REVIEW_API_URL / REVIEW_INGEST_KEY). "
                      "The draft stays in data/inbox/ and will be sent on the next run.")
                return 1
            pending = (st or {}).get("pending", [])
            for path in todo:
                print(f"Processing {path.name}")
                process_inbox(path, state, pending, network=not args.no_network, send=send)
            save_json(DATA / "state.json", state)
    build_site(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
