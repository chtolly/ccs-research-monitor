"""Step 1 (free, runs on GitHub Actions ~05:30 UTC).

Collects candidate items from RSS/Atom feeds, organisation news pages, OpenAlex
and Crossref, keeps only items that are new since the previous digest and not
already published, and writes data/candidates.json for the Claude routine.
Nothing here uses AI; every candidate comes from a page actually retrieved.
"""
from __future__ import annotations

import html
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import feedparser
import requests
import yaml

from common import (DATA, ROOT, item_id, load_json, norm_doi, norm_title, norm_url,
                    oslo_now, oslo_today, save_json, strip_html, title_similarity)

UA = {"User-Agent": "Mozilla/5.0 (compatible; ccs-research-monitor/1.0; +https://github.com/chtolly/ccs-research-monitor)"}
TIMEOUT = 25
LOG: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.append(msg)


def get(url: str, **kw) -> requests.Response:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("unreachable")


# ── keyword matching ────────────────────────────────────────────────────────
def build_matcher(terms: list[str]):
    pats = []
    for t in terms:
        t2 = re.escape(t.replace("₂", "2"))
        # short all-caps acronyms (CCS, CDR) need word boundaries
        pats.append(rf"\b{t2}\b" if len(t) <= 6 else t2)
    rx = re.compile("|".join(pats), re.I)
    return lambda text: bool(rx.search((text or "").replace("₂", "2")))


PAPER_TOPIC = re.compile(r"carbon capture|\bccs\b|\bccus\b|\bbeccs\b|\bdaccs\b|direct air capture|carbon (dioxide )?(transport|storage|sequestration|removal)|co2[ -](capture|transport|transportation|shipping|ship|ships|carrier|carriers|pipeline|pipelines|storage|terminal|sequestration|hub|hubs|value chain|infrastructure|injection)", re.I)
# The supply-chain angle must appear in the TITLE (abstract mentions are too loose)
PAPER_ANGLE = re.compile(r"transport|shipping|\bships?\b|vessel|pipeline|network|supply chain|logistic|infrastructure|\bhubs?\b|cluster|terminal|\bports?\b|value chain|chain|polic|regulat|market|business model|contract|siting|source.sink|matching|techno.economic|cost", re.I)


def paper_relevant(title: str, abstract: str = "") -> bool:
    title = (title or "").replace("₂", "2")
    text = f"{title} {abstract or ''}".replace("₂", "2")
    return bool(PAPER_TOPIC.search(text) and PAPER_ANGLE.search(title))


def to_date(struct) -> date | None:
    if not struct:
        return None
    try:
        return datetime(*struct[:6], tzinfo=timezone.utc).date()
    except Exception:
        return None


# ── sources ─────────────────────────────────────────────────────────────────
def read_feed(src: dict, since: date, match, seen_links: set, first_run: bool) -> list[dict]:
    try:
        r = get(src["url"])
    except Exception as e:
        log(f"  ✗ feed {src['name']}: {type(e).__name__}")
        return []
    if r.status_code >= 400:
        log(f"  ✗ feed {src['name']}: HTTP {r.status_code}")
        return []
    f = feedparser.parse(r.content)
    out = []
    for e in f.entries:
        link = e.get("link") or ""
        title = strip_html(e.get("title", ""))
        if not link or not title:
            continue
        summary = strip_html(e.get("summary", "") or e.get("description", ""), 600)
        d = to_date(e.get("published_parsed") or e.get("updated_parsed"))
        if d is None:
            # Undated entry (e.g. ScienceDirect): treat as new only if never seen before,
            # and never on the very first run (no baseline yet).
            if first_run or norm_url(link) in seen_links:
                continue
        elif d < since:
            continue
        if src.get("filter", True) and not match(f"{title} {summary}"):
            continue
        if src.get("kind") == "paper" and not paper_relevant(title, summary):
            continue
        out.append({
            "kind": src.get("kind", "news"), "source": src["name"], "title": title, "url": link,
            "date": d.isoformat() if d else None, "summary": summary,
        })
    log(f"  ✓ feed {src['name']}: {len(f.entries)} entries, {len(out)} new & relevant")
    return out


def all_feed_links(src: dict) -> list[str]:
    """Links currently in a feed (to build the 'seen' baseline for undated feeds)."""
    try:
        f = feedparser.parse(get(src["url"]).content)
        return [e.get("link") for e in f.entries if e.get("link")]
    except Exception:
        return []


A_RX = re.compile(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.S | re.I)


def read_watch_page(src: dict, seen_links: set, first_run: bool) -> tuple[list[dict], list[str]]:
    try:
        r = get(src["url"])
    except Exception as e:
        log(f"  ✗ page {src['name']}: {type(e).__name__}")
        return [], []
    if r.status_code >= 400:
        log(f"  ✗ page {src['name']}: HTTP {r.status_code}")
        return [], []
    host = urlsplit(src["url"]).netloc.removeprefix("www.")
    found: dict[str, str] = {}
    for m in A_RX.finditer(r.text):
        href = urljoin(src["url"], html.unescape(m.group(1)))
        if urlsplit(href).netloc.removeprefix("www.") != host:
            continue
        if src.get("link_contains") and src["link_contains"] not in href:
            continue
        text = strip_html(m.group(2))
        if len(text) < 20:
            continue
        found.setdefault(href, text[:250])
    new = [] if first_run else [
        {"kind": "news", "source": src["name"], "title": t, "url": u, "date": None,
         "summary": "(New link on the organisation's news page; publication date not given by the page list — confirm before use.)"}
        for u, t in found.items() if norm_url(u) not in seen_links
    ]
    log(f"  ✓ page {src['name']}: {len(found)} news links, {len(new)} new")
    return new, list(found)


def openalex(queries: list[str], since: date, today: date) -> list[dict]:
    out = []
    for q in queries:
        try:
            r = get("https://api.openalex.org/works", params={
                "search": q, "per_page": 25,
                "filter": f"from_publication_date:{since},to_publication_date:{today + timedelta(days=60)},type:article|review|report|preprint",
                "select": "id,doi,title,publication_date,authorships,primary_location,abstract_inverted_index",
            })
            r.raise_for_status()
        except Exception as e:
            log(f"  ✗ OpenAlex '{q}': {e}")
            continue
        n = 0
        for w in r.json().get("results", []):
            title = strip_html(w.get("title") or "")
            abstract = ""
            inv = w.get("abstract_inverted_index") or {}
            if inv:
                pos = sorted((p, word) for word, ps in inv.items() for p in ps)
                abstract = " ".join(word for _, word in pos)
            if not title or not paper_relevant(title, abstract):
                continue
            loc = w.get("primary_location") or {}
            venue = ((loc.get("source") or {}).get("display_name")) or ""
            url = w.get("doi") or loc.get("landing_page_url") or w.get("id")
            authors = [a["author"]["display_name"] for a in (w.get("authorships") or [])[:6] if a.get("author")]
            out.append({
                "kind": "paper", "source": strip_html(venue) or "OpenAlex", "title": title, "url": url,
                "doi": norm_doi(w.get("doi")), "date": w.get("publication_date"),
                "authors": ", ".join(authors) + (" et al." if len(w.get("authorships") or []) > 6 else ""),
                "summary": strip_html(abstract, 700),
            })
            n += 1
        log(f"  ✓ OpenAlex '{q}': {n} relevant")
        time.sleep(0.3)
    return out


def crossref(queries: list[str], since: date) -> list[dict]:
    out = []
    for q in queries:
        try:
            r = get("https://api.crossref.org/works", params={
                "query.bibliographic": q, "rows": 30, "sort": "relevance",
                "filter": f"from-created-date:{since},type:journal-article",
                "select": "DOI,title,author,container-title,issued,created,URL,abstract",
            })
            r.raise_for_status()
        except Exception as e:
            log(f"  ✗ Crossref '{q}': {e}")
            continue
        n = 0
        for it in r.json()["message"]["items"]:
            title = strip_html((it.get("title") or [""])[0])
            abstract = strip_html(it.get("abstract") or "", 700)
            if not title or not paper_relevant(title, abstract):
                continue
            d = None
            for key in ("published-online", "issued", "created"):
                parts = ((it.get(key) or {}).get("date-parts") or [[None]])[0]
                if parts and parts[0]:
                    p = parts + [1, 1]
                    cand_d = date(p[0], p[1], p[2])
                    if cand_d <= date.today() + timedelta(days=3):  # skip future print-issue dates
                        d = cand_d.isoformat()
                        break
            authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in (it.get("author") or [])[:6]]
            out.append({
                "kind": "paper", "source": strip_html((it.get("container-title") or ["Crossref"])[0]), "title": title,
                "url": f"https://doi.org/{it['DOI']}", "doi": norm_doi(it["DOI"]), "date": d,
                "authors": ", ".join(authors) + (" et al." if len(it.get("author") or []) > 6 else ""),
                "summary": abstract,
            })
            n += 1
        log(f"  ✓ Crossref '{q}': {n} relevant")
        time.sleep(0.3)
    return out


def enrich_abstracts(papers: list[dict]) -> None:
    """New papers often have no abstract in Crossref; try OpenAlex, then Semantic Scholar."""
    found = 0
    for c in papers[:40]:
        doi = c.get("doi")
        if not doi:
            continue
        abstract = ""
        try:
            r = get(f"https://api.openalex.org/works/doi:{doi}", params={"select": "abstract_inverted_index"})
            inv = (r.json().get("abstract_inverted_index") if r.ok else None) or {}
            if inv:
                abstract = " ".join(w for _, w in sorted((p, w) for w, ps in inv.items() for p in ps))
        except Exception:
            pass
        if not abstract:
            try:
                r = get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}", params={"fields": "abstract,tldr"})
                if r.ok:
                    j = r.json()
                    abstract = j.get("abstract") or ((j.get("tldr") or {}).get("text") or "")
            except Exception:
                pass
            time.sleep(1.1)  # Semantic Scholar shared rate limit
        if abstract:
            c["summary"] = strip_html(abstract, 700)
            found += 1
        else:
            c["summary"] = "(No abstract available yet from Crossref, OpenAlex or Semantic Scholar.)"
    log(f"  ✓ abstracts found for {found} of {len(papers)} papers that lacked one")


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    st = cfg["settings"]
    state = load_json(DATA / "state.json", {})
    history = load_json(DATA / "history.json", [])
    seen = load_json(DATA / "seen_links.json", {"links": []})
    seen_links = set(seen["links"])
    first_run = not seen_links

    today = oslo_today()
    # "New" = since the last digest before today (re-running on the same day keeps the same window)
    earlier = sorted(d for d in (k.removesuffix(".json") for k in state.get("processed", {})) if d < today.isoformat())
    if earlier:
        since = date.fromisoformat(earlier[-1]) - timedelta(days=1)  # 1-day overlap; dedupe removes repeats
    else:
        since = today - timedelta(days=st.get("first_run_lookback_days", 7))
    log(f"Collecting for {today} (items since {since}; first run: {first_run})")

    match = build_matcher(cfg["keywords"]["core"])
    cands: list[dict] = []
    new_seen: list[str] = []

    with ThreadPoolExecutor(8) as ex:
        feed_results = list(ex.map(lambda s: read_feed(s, since, match, seen_links, first_run), cfg.get("feeds", [])))
        page_results = list(ex.map(lambda s: read_watch_page(s, seen_links, first_run), cfg.get("watch_pages", [])))
        undated_links = list(ex.map(all_feed_links, cfg.get("feeds", [])))
    for r in feed_results:
        cands += r
    for items, links in page_results:
        cands += items
        new_seen += links
    for links in undated_links:
        new_seen += links

    papers = openalex(cfg["keywords"]["paper_queries"], since, today) + crossref(cfg["keywords"]["paper_queries"], since)
    cands += papers

    # Remove anything already published (URL, DOI or near-identical title) and internal duplicates
    # Items published on earlier days are excluded. Today's own digest is not, so a re-run today keeps them.
    prior = [h for h in history if h.get("digest_date") != today.isoformat()]
    hist_urls = {norm_url(h["url"]) for h in prior}
    hist_dois = {h.get("doi") for h in prior if h.get("doi")}
    hist_titles = [h["title"] for h in prior[-1500:]]
    uniq: dict[str, dict] = {}
    by_doi: dict[str, str] = {}
    dropped_dupes = 0
    for c in cands:
        nu, nd = norm_url(c["url"]), c.get("doi", "")
        if nu in hist_urls or (nd and nd in hist_dois) or any(title_similarity(c["title"], t) > 0.92 for t in hist_titles):
            dropped_dupes += 1
            continue
        if nu in uniq or (nd and nd in by_doi):
            continue
        if any(title_similarity(c["title"], u["title"]) > 0.92 for u in uniq.values()):
            continue
        c["id"] = item_id(c["url"])
        uniq[nu] = c
        if nd:
            by_doi[nd] = nu

    items = list(uniq.values())
    enrich_abstracts([c for c in items if c["kind"] == "paper" and len(c.get("summary") or "") < 80])
    items.sort(key=lambda c: (c.get("date") or "9999"), reverse=True)  # undated (=newly seen) first
    cap = st.get("max_candidates_for_claude", 150)
    news = [c for c in items if c["kind"] != "paper"][: cap // 2 + cap // 4]
    pap = [c for c in items if c["kind"] == "paper"][: cap - len(news)]
    items = news + pap

    out = {
        "generated_at": oslo_now().isoformat(timespec="seconds"),
        "digest_date": today.isoformat(),
        "since": since.isoformat(),
        "first_run": first_run,
        "categories": [c["key"] for c in cfg["categories"]],
        "max_items_per_day": st.get("max_items_per_day", 25),
        "max_web_searches": st.get("max_web_searches", 15),
        "counts": {"news": len(news), "papers": len(pap), "already_published_skipped": dropped_dupes},
        "items": items,
        "log": LOG,
    }
    save_json(DATA / "candidates.json", out)
    seen_links |= {norm_url(u) for u in new_seen}
    save_json(DATA / "seen_links.json", {"links": sorted(seen_links)[-20000:]})
    log(f"Wrote {len(items)} candidates ({len(news)} news, {len(pap)} papers).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
