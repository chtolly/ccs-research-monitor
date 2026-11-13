"""Step 3 (free, runs on GitHub Actions after Claude pushes its draft).

  python scripts/build.py --check data/inbox/2026-11-12.json
      Offline format check only (stdlib, no network). Claude runs this before pushing.

  python scripts/build.py
      1. Verifies every item in new/changed inbox drafts: required fields, allowed
         category, link really opens, page title matches (for web-search items),
         date is recent, not already published.
      2. Saves the accepted items as data/digests/<date>.json and adds them to history.
      3. Builds the website into _site/ (index, archive pages, RSS feed, search data).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, ROOT, item_id, load_json, norm_doi, norm_title, norm_url,  # noqa: E402
                    oslo_now, oslo_today, save_json, strip_html, title_similarity)

INBOX = DATA / "inbox"
DIGESTS = DATA / "digests"
SITE = ROOT / "_site"
REQUIRED = ["title", "source", "date", "url", "category", "summary", "why_it_matters", "origin"]
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ════════════════════════════════════════════════════════════════════════════
#  1. Format check (offline)
# ════════════════════════════════════════════════════════════════════════════
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
    return errs


# ════════════════════════════════════════════════════════════════════════════
#  2. Verification (network) and publishing into history
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


def verify_item(it: dict, cands: dict[str, dict]) -> str | None:
    """Return None if OK, else the reason the item is rejected."""
    url = it["url"]
    if it["origin"] == "candidate":
        c = cands.get(it.get("candidate_id"))
        if not c:
            return "candidate_id not in today's candidate list"
        if norm_url(c["url"]) != norm_url(url):
            return "URL differs from the collected candidate"
        if title_similarity(c["title"], it["title"]) < 0.6:
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
    if it["category"] == "events" and it.get("event_date"):
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


def publish_inbox(path: Path, history: list[dict], state: dict, network: bool = True) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    cand_doc = load_json(DATA / "candidates.json", {})
    cands = {c["id"]: c for c in cand_doc.get("items", [])}
    categories = load_categories()
    errs = check_format(doc, [c["key"] for c in categories], set(cands))
    digest_date = doc["date"]
    today = oslo_today()
    since = date.fromisoformat(cand_doc.get("since") or (date.fromisoformat(digest_date) - timedelta(days=7)).isoformat())
    max_items = cand_doc.get("max_items_per_day", 25)

    # Re-processing the same day replaces that day's earlier entries
    history[:] = [h for h in history if h.get("digest_date") != digest_date]
    hist_urls = {norm_url(h["url"]) for h in history}
    hist_dois = {h.get("doi") for h in history if h.get("doi")}
    hist_titles = [h["title"] for h in history[-2000:]]

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
        reason = None
        if norm_url(it["url"]) in hist_urls or (doi and doi in hist_dois) or \
                any(title_similarity(it["title"], t) > 0.9 for t in hist_titles):
            reason = "already published on an earlier day"
        elif any(norm_url(a["url"]) == norm_url(it["url"]) or title_similarity(a["title"], it["title"]) > 0.9 for a in accepted):
            reason = "duplicate within today's list"
        else:
            reason = (verify_item(it, cands) if network else None) or \
                (date_ok(it, since, today) if it.get("date") else "no publication date")
        if reason:
            dropped.append({"title": it["title"], "url": it["url"], "reason": reason})
            print(f"  ✗ dropped: {it['title'][:70]} — {reason}")
            continue
        it["id"] = item_id(it["url"])
        if doi:
            it["doi"] = doi
        accepted.append(it)
        print(f"  ✓ {it['category']:<15} {it['title'][:70]}")
    if len(accepted) > max_items:
        for it in accepted[max_items:]:
            dropped.append({"title": it["title"], "url": it["url"], "reason": "over the daily item cap"})
        accepted = accepted[:max_items]

    order = {c["key"]: n for n, c in enumerate(categories)}
    accepted.sort(key=lambda x: (order.get(x["category"], 99), x.get("date") or ""), reverse=False)
    digest = {
        "date": digest_date,
        "published_at": oslo_now().isoformat(timespec="seconds"),
        "searches_used": doc.get("searches_used"),
        "notes": doc.get("notes", ""),
        "items": accepted,
        "dropped": dropped,
        "candidates_seen": len(cands),
    }
    save_json(DIGESTS / f"{digest_date}.json", digest)
    for it in accepted:
        history.append({"id": it["id"], "url": it["url"], "doi": it.get("doi", ""), "title": it["title"],
                        "date": it["date"], "category": it["category"], "digest_date": digest_date})
    state["last_digest_date"] = max(state.get("last_digest_date") or digest_date, digest_date)
    state.setdefault("processed", {})[path.name] = hashlib.sha1(path.read_bytes()).hexdigest()
    print(f"Digest {digest_date}: {len(accepted)} published, {len(dropped)} dropped.")
    return digest


def load_config() -> dict:
    import yaml
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_categories() -> list[dict]:
    return load_config()["categories"]


# ════════════════════════════════════════════════════════════════════════════
#  3. Website
# ════════════════════════════════════════════════════════════════════════════
def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def nice_date(d: str) -> str:
    try:
        return datetime.fromisoformat(d).strftime("%-d %b %Y")
    except Exception:  # noqa: BLE001
        return d or ""


CSS = """
:root{--bg:#f7f7f4;--card:#fff;--ink:#1b1d1f;--muted:#5d6166;--line:#e3e3de;--accent:#0b6e69;--accent-soft:#e3f1ef;--chip:#eeeeea}
@media (prefers-color-scheme:dark){:root{--bg:#111314;--card:#1a1d1f;--ink:#e9eaeb;--muted:#9da2a7;--line:#2b2f32;--accent:#5cc8bf;--accent-soft:#16302e;--chip:#24282b}}
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
h2{font-size:1.2rem;margin:26px 0 6px}
h3.cat{font-size:1rem;margin:22px 0 8px;color:var(--accent);text-transform:uppercase;letter-spacing:.04em}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 10px}
.item h4{margin:0 0 2px;font-size:1.03rem;line-height:1.35}
.item h4 a{color:var(--ink);text-decoration:none}.item h4 a:hover{text-decoration:underline}
.en{color:var(--muted);font-size:.92rem;margin:0 0 4px;font-style:italic}
.src{font-size:.84rem;color:var(--muted);margin:0 0 8px}
.item p{margin:0 0 6px}
.why{background:var(--accent-soft);border-radius:6px;padding:6px 10px;font-size:.93rem;margin:6px 0 0}
.tag{display:inline-block;font-size:.75rem;border-radius:4px;padding:1px 6px;background:var(--chip);color:var(--muted);margin-left:6px;vertical-align:1px}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:10px;padding:18px;color:var(--muted)}
.archive{list-style:none;padding:0;margin:0;columns:2 220px}
.archive li{padding:3px 0}
.about{color:var(--ink);font-size:.95rem}
footer{color:var(--muted);font-size:.85rem;padding:30px 0 40px;border-top:1px solid var(--line);margin-top:30px}
.hidden{display:none}
.daygroup{font-size:.95rem;color:var(--muted);margin:18px 0 6px;font-weight:600}
"""

JS = """
(function(){
const q=document.getElementById('q'), chips=[...document.querySelectorAll('.chip')];
const today=document.getElementById('today'), results=document.getElementById('results');
let data=null, cat='all';
const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){ if(!data){ data=await (await fetch('items.json')).json(); } return data; }
function card(it){return `<article class="item"><h4><a href="${esc(it.url)}" rel="noopener" target="_blank">${esc(it.title)}</a><span class="tag">${esc(CATS[it.category]||it.category)}</span></h4>`+
 (it.title_en?`<p class="en">${esc(it.title_en)}</p>`:'')+
 `<p class="src">${esc(it.source)} · ${esc(it.date)}${it.event_date?' · event '+esc(it.event_date):''}${it.authors?' · '+esc(it.authors):''}</p><p>${esc(it.summary)}</p><p class="why"><strong>Why it matters:</strong> ${esc(it.why_it_matters)}</p></article>`;}
async function run(){
  const term=q.value.trim().toLowerCase();
  if(!term && cat==='all'){ today.classList.remove('hidden'); results.classList.add('hidden'); return; }
  const d=await load(); const words=term.split(/\\s+/).filter(Boolean);
  const hits=d.filter(it=>(cat==='all'||it.category===cat)&&words.every(w=>(it.title+' '+(it.title_en||'')+' '+it.summary+' '+it.source+' '+it.why_it_matters).toLowerCase().includes(w)));
  let html=`<h2>${hits.length} item${hits.length===1?'':'s'} across all days</h2>`, day='';
  for(const it of hits.slice(0,300)){ if(it.digest_date!==day){day=it.digest_date; html+=`<div class="daygroup">Digest of ${esc(day)}</div>`;} html+=card(it); }
  if(!hits.length) html+='<p class="empty">No matching items.</p>';
  results.innerHTML=html; today.classList.add('hidden'); results.classList.remove('hidden');
}
q.addEventListener('input',()=>{clearTimeout(q._t);q._t=setTimeout(run,150)});
chips.forEach(c=>c.addEventListener('click',()=>{chips.forEach(x=>x.setAttribute('aria-pressed','false'));c.setAttribute('aria-pressed','true');cat=c.dataset.cat;run();}));
})();
"""


def render_item(it: dict, cats: dict, show_tag=False) -> str:
    parts = [f'<article class="item"><h4><a href="{e(it["url"])}" rel="noopener" target="_blank">{e(it["title"])}</a>']
    if show_tag:
        parts.append(f'<span class="tag">{e(cats.get(it["category"], it["category"]))}</span>')
    parts.append("</h4>")
    if it.get("title_en"):
        parts.append(f'<p class="en">{e(it["title_en"])}</p>')
    meta = [e(it["source"]), e(nice_date(it["date"]))]
    if it.get("event_date"):
        meta.append("event " + e(nice_date(it["event_date"])))
    if it.get("authors"):
        meta.append(e(it["authors"]))
    parts.append(f'<p class="src">{" · ".join(meta)}</p><p>{e(it["summary"])}</p>')
    parts.append(f'<p class="why"><strong>Why it matters:</strong> {e(it["why_it_matters"])}</p>')
    if it.get("also_reported_by"):
        links = ", ".join(f'<a href="{e(a.get("url"))}" rel="noopener" target="_blank">{e(a.get("source"))}</a>'
                          for a in it["also_reported_by"] if a.get("url"))
        if links:
            parts.append(f'<p class="src">Also reported by: {links}</p>')
    parts.append("</article>")
    return "".join(parts)


def render_digest_body(dg: dict, categories: list[dict]) -> str:
    cats = {c["key"]: c["name"] for c in categories}
    if not dg["items"]:
        return '<p class="empty">No significant new items today.</p>'
    out = []
    for c in categories:
        items = [i for i in dg["items"] if i["category"] == c["key"]]
        if items:
            out.append(f'<h3 class="cat">{e(c["name"])} <span class="tag">{len(items)}</span></h3>')
            out += [render_item(i, cats) for i in items]
    return "\n".join(out)


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
<p class="meta">Last updated: <strong>{e(updated)}</strong> (Oslo time)</p>
<nav class="top"><a href="{pre}index.html#today">Today</a><a href="{pre}index.html#archive">Archive</a><a href="{pre}index.html#about">About</a><a href="{pre}feed.xml">RSS feed</a></nav></div></header>"""


def footer(cfg: dict) -> str:
    return f"""<footer><div class="wrap">Maintained by {e(cfg['site']['maintainer'])}. Summaries are AI-generated (Claude) — always check the linked source.</div></footer>"""


def build_site(cfg: dict) -> None:
    categories = cfg["categories"]
    cats = {c["key"]: c["name"] for c in categories}
    digests = sorted((load_json(p, None) for p in DIGESTS.glob("*.json")), key=lambda d: d["date"], reverse=True)
    digests = [d for d in digests if d]
    SITE.mkdir(exist_ok=True)
    (SITE / "archive").mkdir(exist_ok=True)
    updated = datetime.fromisoformat(digests[0]["published_at"]).strftime("%-d %B %Y, %H:%M") if digests else "not yet"

    # Search data
    all_items = [dict(it, digest_date=d["date"]) for d in digests for it in d["items"]]
    keep = ["title", "title_en", "source", "date", "event_date", "authors", "url", "category", "summary", "why_it_matters", "digest_date"]
    (SITE / "items.json").write_text(json.dumps([{k: i.get(k) for k in keep if i.get(k)} for i in all_items], ensure_ascii=False), encoding="utf-8")

    # Archive pages
    for d in digests:
        body = header(cfg, updated, 1) + f'<main class="wrap"><h2>Digest of {e(nice_date(d["date"]))}</h2>' + \
            render_digest_body(d, categories) + "</main>" + footer(cfg)
        (SITE / "archive" / f"{d['date']}.html").write_text(page(f"{nice_date(d['date'])} · {cfg['site']['title']}", body, cfg, 1), encoding="utf-8")

    latest = digests[0] if digests else None
    chips = '<button class="chip" data-cat="all" aria-pressed="true">All</button>' + "".join(
        f'<button class="chip" data-cat="{e(c["key"])}" aria-pressed="false">{e(c["name"])}</button>' for c in categories)
    if latest:
        today_html = f'<h2>Digest of {e(nice_date(latest["date"]))} <span class="tag">{len(latest["items"])} item{"s" if len(latest["items"]) != 1 else ""}</span></h2>' + \
            render_digest_body(latest, categories)
        if latest["date"] != oslo_today().isoformat():
            today_html = '<p class="meta">Today\'s update has not been published yet; showing the most recent digest.</p>' + today_html
    else:
        today_html = '<p class="empty">The first digest has not been published yet.</p>'
    arch = "".join(f'<li><a href="archive/{d["date"]}.html">{e(nice_date(d["date"]))}</a> <span class="tag">{len(d["items"])}</span></li>' for d in digests)
    cat_list = "".join(f"<li><strong>{e(c['name'])}</strong> — {e(c['description'])}</li>" for c in categories)
    about = f"""<section id="about" class="about"><h2>About</h2>
<p>This page tracks new research, projects, policy and events relevant to the {e(cfg['site']['project'])}. It updates automatically every morning (about 07:00 Oslo time in winter, 08:00 in summer).</p>
<p><strong>What is tracked:</strong></p><ul>{cat_list}</ul>
<p><strong>How it works:</strong> each morning a script collects new items from about 25 organisation news feeds and pages (SINTEF, NTNU, CCUS Norway, Gassnova, Northern Lights, Longship, Global CCS Institute, IEAGHG, ZEP, Bellona, CATF, the EU Innovation Fund, DG CLIMA, the Danish Energy Agency and others) and from the OpenAlex and Crossref research databases. Claude (an AI model by Anthropic) then adds a limited number of web searches, removes off-topic and repeated items, and writes the summaries. Every link is checked automatically before publishing.</p>
<p><strong>Please note:</strong> summaries and “why it matters” notes are AI-generated and can contain mistakes. Always check the linked source before relying on an item. Items in Norwegian, Swedish or Danish keep their original title, with an English translation underneath.</p>
<p>Maintained by {e(cfg['site']['maintainer'])}. Subscribe via the <a href="feed.xml">RSS feed</a>.</p></section>"""
    body = header(cfg, updated) + f"""<div class="tools"><div class="wrap"><input id="q" class="search" type="search" placeholder="Search all digests (e.g. ship, Greensand, ETS)…" aria-label="Search all digests">
<div class="chips" role="group" aria-label="Filter by category">{chips}</div></div></div>
<main class="wrap"><section id="today">{today_html}</section><section id="results" class="hidden"></section>
<section id="archive"><h2>Archive</h2><ul class="archive">{arch or '<li>No earlier digests yet.</li>'}</ul></section>{about}</main>{footer(cfg)}
<script>const CATS={json.dumps(cats, ensure_ascii=False)};{JS}</script>"""
    (SITE / "index.html").write_text(page(cfg["site"]["title"], body, cfg), encoding="utf-8")

    # RSS
    base = cfg["site"]["base_url"].rstrip("/") + "/"
    rss_items = []
    for it in all_items[:100]:
        pub = datetime.fromisoformat(it["digest_date"] + "T07:00:00+01:00").strftime("%a, %d %b %Y %H:%M:%S %z")
        desc = f"[{cats.get(it['category'], it['category'])}] {it['source']} ({it['date']}). {it['summary']} Why it matters: {it['why_it_matters']}"
        title = it["title"] + (f" ({it['title_en']})" if it.get("title_en") else "")
        rss_items.append(f"<item><title>{e(title)}</title><link>{e(it['url'])}</link><guid isPermaLink=\"false\">{e(it['id'])}</guid>"
                         f"<pubDate>{pub}</pubDate><category>{e(cats.get(it['category']))}</category><description>{e(desc)}</description></item>")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel><title>{e(cfg['site']['title'])}</title><link>{e(base)}</link>
<atom:link href="{e(base)}feed.xml" rel="self" type="application/rss+xml"/><description>{e(cfg['site']['subtitle'])}</description><language>en</language>
{''.join(rss_items)}</channel></rss>"""
    (SITE / "feed.xml").write_text(rss, encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    # Small file Claude reads next morning to avoid repeating stories
    recent = [{"title": i["title"], "source": i["source"], "date": i["date"], "digest_date": i["digest_date"]}
              for i in all_items if i["digest_date"] >= (oslo_today() - timedelta(days=45)).isoformat()]
    save_json(DATA / "recent_titles.json", recent)
    print(f"Site built: {len(digests)} digests, {len(all_items)} items → {SITE}")


# ════════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", metavar="INBOX_FILE", help="offline format check only")
    ap.add_argument("--no-network", action="store_true", help="skip link checks (local testing)")
    args = ap.parse_args()

    if args.check:
        doc = json.loads(Path(args.check).read_text(encoding="utf-8"))
        cand = load_json(DATA / "candidates.json", {})
        errs = check_format(doc, cand.get("categories") or [], {c["id"] for c in cand.get("items", [])})
        if errs:
            print("FORMAT ERRORS — fix these and run the check again:")
            print("\n".join(" - " + x for x in errs))
            return 1
        print(f"OK: {len(doc['items'])} items, format valid.")
        return 0

    cfg = load_config()
    state = load_json(DATA / "state.json", {})
    history = load_json(DATA / "history.json", [])
    processed = state.get("processed", {})
    for path in sorted(INBOX.glob("*.json")):
        h = hashlib.sha1(path.read_bytes()).hexdigest()
        if processed.get(path.name) == h:
            continue
        print(f"Processing {path.name}")
        publish_inbox(path, history, state, network=not args.no_network)
    save_json(DATA / "history.json", history)
    save_json(DATA / "state.json", state)
    build_site(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
