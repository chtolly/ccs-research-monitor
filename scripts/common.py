"""Shared helpers (standard library only, so the Claude routine can import them too)."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OSLO = ZoneInfo("Europe/Oslo")

TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|source$|cmp$)")


def oslo_now() -> datetime:
    return datetime.now(OSLO)


def oslo_today() -> date:
    return oslo_now().date()


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def norm_url(url: str) -> str:
    """Normalise a URL for duplicate detection."""
    if not url:
        return ""
    p = urlsplit(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+$", "", p.path) or "/"
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not TRACKING_PARAMS.match(k.lower())])
    return urlunsplit(("https", host, path, q, ""))


def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip().lower())


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").lower()
    t = t.replace("₂", "2")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def item_id(url: str) -> str:
    import hashlib
    return hashlib.sha1(norm_url(url).encode()).hexdigest()[:10]


def strip_html(s: str, limit: int | None = None) -> str:
    import html as _html
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rsplit(" ", 1)[0] + "…"
    return s
