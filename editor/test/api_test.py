"""End-to-end tests for the review API, run against a local `wrangler dev` (see README → Technical notes).

Needs: API on http://localhost:8787, the site (with known.json) on SITE_URL, mock GitHub writing into REPO_DIR,
and env SESSION_SECRET + INGEST_SECRET equal to the Worker's. People in the test database:
approver.one@example.test (approver) and editor.one@example.test (editor).
    python3 api_test.py <repo_dir>
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("API", "http://localhost:8787")
REPO = Path(sys.argv[1])
SECRET = os.environ["SESSION_SECRET"].encode()
KEY = os.environ["INGEST_SECRET"]
OK, BAD = 0, []


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def token(email, days=14):
    p = b64u(json.dumps({"e": email, "x": int(time.time()) + days * 86400}).encode())
    return p + "." + b64u(hmac.new(SECRET, p.encode(), hashlib.sha256).digest())


def call(path, body=None, tok=None, key=None, origin="http://localhost:8000"):
    h = {"content-type": "application/json", "origin": origin}
    if tok:
        h["authorization"] = "Bearer " + tok
    if key:
        h["x-ingest-key"] = key
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def check(name, cond, info=""):
    global OK
    if cond:
        OK += 1
        print(f"  ✓ {name}")
    else:
        BAD.append(name)
        print(f"  ✗ {name}  {info}")


A, E, X = token("approver.one@example.test"), token("editor.one@example.test"), token("stranger@example.test")
published = json.loads((REPO / "_site" / "known.json").read_text())[0]

print("Login and roles")
check("no token → 401", call("/api/me")[0] == 401)
check("forged token → 401", call("/api/me", tok=A[:-3] + "abc")[0] == 401)
check("expired token → 401", call("/api/me", tok=token("approver.one@example.test", days=-1))[0] == 401)
s, b, _ = call("/api/me", tok=X)
check("person not on the list → 403 with clear message", s == 403 and "not on the list" in b.get("error", ""), b)
s, b, h = call("/api/me", tok=A)
check("approver → role approver", s == 200 and b.get("role") == "approver", b)
check("CORS header for the review page origin", h.get("Access-Control-Allow-Origin") == "http://localhost:8000", h)
check("no CORS header for another origin", "Access-Control-Allow-Origin" not in call("/api/me", tok=A, origin="https://evil.example")[2])
check("editor → role editor", call("/api/me", tok=E)[1].get("role") == "editor")
check("editor cannot see pending queue", call("/api/pending", tok=E)[0] == 403)

print("Editor submissions")
good = {"url": "https://ccusnorway.no/", "title": "Local test item: CCUS Norway network page", "source": "CCUS Norway",
        "date": "2026-11-18", "type": "news", "category": "ccus_norway",
        "summary": "Test summary written for the local end-to-end test of the review space; not a real item.",
        "why_it_matters": "Test only."}
s, b, _ = call("/api/submit", good, tok=E)
check("editor submits a valid item", s == 200 and b.get("ok"), b)
editor_item = b.get("id")
s, b, _ = call("/api/submit", good, tok=E)
check("same link again → duplicate refused", s == 409, b)
s, b, _ = call("/api/submit", {**good, "url": published["url"], "title": "x" + published["title"]}, tok=E)
check("link already on the public page → duplicate with 'published'", s == 409 and any(d["where"] == "published" for d in b.get("duplicates", [])), b)
sim = {**good, "url": "https://gassnova.no/en/", "title": published["title"] + " (copy)"}
s, b, _ = call("/api/submit", sim, tok=E)
check("very similar title → asks to confirm", s == 409 and b.get("needs_confirm"), b)
s, b, _ = call("/api/submit", {**sim, "confirm_similar": True}, tok=E)
check("confirmed similar title → accepted", s == 200, b)
similar_item = b.get("id")
s, b, _ = call("/api/submit", {**good, "url": "https://www.sintef.no/en/this-page-does-not-exist-ccs-test-404/", "title": "Broken link test"}, tok=E)
check("broken link → refused with a clear message", s == 400 and "link" in b.get("error", "").lower(), b)
s, b, _ = call("/api/submit", {**good, "url": "not a link", "summary": "short", "category": "nope", "type": "nope", "date": ""}, tok=E)
check("missing/invalid fields → all errors listed", s == 400 and len(b.get("errors", [])) >= 4, b)
s, b, _ = call("/api/submit", {**good, "url": "https://norlights.com/", "title": "Event test without date", "type": "event"}, tok=E)
check("event without event date → refused", s == 400, b)
s, b, _ = call("/api/check", {"url": published["url"]}, tok=E)
check("duplicate check while typing finds the published item", s == 200 and b["duplicates"], b)
s, b, _ = call("/api/mine", tok=E)
check("editor sees own submissions", s == 200 and {x["id"] for x in b} >= {editor_item, similar_item}, b)
check("editor cannot approve", call(f"/api/items/{editor_item}/approve", {}, tok=E)[0] == 403)

print("Agent run into the pending queue")
agent_items = [
    {"id": "agenttest01", "title": "Local test: agent item one (Northern Lights page)", "url": "https://norlights.com/",
     "source": "Northern Lights", "date": "2026-11-17", "type": "news", "category": "nordic_projects",
     "summary": "Agent test item one, with a summary long enough to pass the same checks as real items.", "why_it_matters": "Test.", "found_by": "candidate", "run_date": "2026-11-23",
     "related": [{"title": "Gassnova", "url": "https://gassnova.no/en/", "source": "Gassnova", "reason": "Test related link."}]},
    {"id": "agenttest02", "title": "Local test: agent report two", "url": "https://climit.no/en/",
     "source": "CLIMIT", "date": "2026-11-16", "type": "report", "paywalled": True, "category": "research",
     "summary": "Agent test item two, with a summary long enough to pass the same checks as real items.", "why_it_matters": "Test.", "found_by": "web_search", "run_date": "2026-11-23", "related": []},
]
check("ingest without key → 401", call("/api/agent/ingest", {"items": agent_items})[0] == 401)
check("ingest with wrong key → 401", call("/api/agent/ingest", {"items": agent_items}, key="x" * len(KEY))[0] == 401)
s, b, _ = call("/api/agent/ingest", {"run_date": "2026-11-23", "items": agent_items}, key=KEY)
check("ingest adds 2 agent items", s == 200 and b.get("added") == 2, b)
s, b, _ = call("/api/agent/ingest", {"run_date": "2026-11-23", "items": agent_items}, key=KEY)
check("ingesting the same items again adds nothing", s == 200 and b.get("added") == 0, b)
s, b, _ = call("/api/agent/ingest", {"items": [{**agent_items[0], "id": "agenttest03", "url": published["url"]}]}, key=KEY)
check("agent item already published → skipped", b.get("added") == 0, b)
s, b, _ = call("/api/agent/state", key=KEY)
check("agent state lists pending items and manual items needing related links",
      s == 200 and len(b["pending"]) == 4 and {n["id"] for n in b["needs_related"]} == {editor_item, similar_item}, b)

print("Approver: one shared queue")
s, q, _ = call("/api/pending", tok=A)
check("approver sees all 4 pending items (2 agent + 2 manual)", s == 200 and len(q) == 4 and {x["origin"] for x in q} == {"agent", "manual"}, q)
s, b, _ = call(f"/api/items/{editor_item}/edit", {"summary": "Edited by the approver during the local test of the review space."}, tok=A)
check("approver edits an item", s == 200 and b["data"]["summary"].startswith("Edited"), b)
s, b, _ = call(f"/api/items/{editor_item}/approve", {}, tok=A)
f = REPO / "data" / "approved" / f"{editor_item}.json"
rec = json.loads(f.read_text()) if f.exists() else {}
check("approve writes data/approved/<id>.json via GitHub", s == 200 and f.exists(), b)
check("record keeps who submitted, who approved, when", rec.get("submitted_by") == "Test Editor" and rec.get("approved_by") == "Test Approver"
      and rec.get("approved_at") and rec.get("submitted_at") and rec.get("origin") == "manual", rec)
check("no emails in the public record", "@" not in json.dumps(rec), rec)
check("approving twice → refused", call(f"/api/items/{editor_item}/approve", {}, tok=A)[0] == 409)
s, b, _ = call("/api/items/agenttest01/approve", {"related": []}, tok=A)
rec1 = json.loads((REPO / "data/approved/agenttest01.json").read_text())
check("approver removes the related links while approving", s == 200 and rec1.get("related") == [] and rec1["submitted_by"].startswith("Claude"), rec1)
s, b, _ = call("/api/items/agenttest02/reject", {"reason": "local test"}, tok=A)
r = REPO / "data" / "rejected" / "agenttest02.json"
check("reject writes data/rejected/<id>.json (with reason, no email)", s == 200 and r.exists() and "@" not in r.read_text() and "local test" in r.read_text(), b)
s, b, _ = call(f"/api/items/{similar_item}/reject", {}, tok=A)
check("reject a manual item", s == 200, b)
s, q, _ = call("/api/pending", tok=A)
check("queue is empty after deciding everything", s == 200 and q == [], q)
s, b, _ = call("/api/agent/ingest", {"items": [agent_items[1]]}, key=KEY)
check("rejected item is not added again by the agent", b.get("added") == 0, b)
s, b, _ = call("/api/submit", {**good, "url": "https://gassnova.no/en/", "title": "Resubmitting a rejected link"}, tok=E)
check("editor resubmitting a rejected link → duplicate 'rejected'", s == 409 and any(d["where"] == "rejected" for d in b.get("duplicates", [])), b)

print("Related links for a published manual item")
s, b, _ = call("/api/agent/ingest", {"run_date": "2026-11-30", "items": [], "related_updates": [
    {"item_id": editor_item, "related": [{"title": "Gassnova", "url": "https://gassnova.no/en/", "source": "Gassnova", "reason": "Test."}]}]}, key=KEY)
check("weekly run proposes related links for an approved manual item", b.get("related_added") == 1, b)
s, q, _ = call("/api/pending", tok=A)
rel = [x for x in q if x["kind"] == "related"]
check("they wait in the queue as their own entry", len(rel) == 1, q)
if rel:
    s, b, _ = call(f"/api/items/{rel[0]['id']}/approve", {"related": rel[0]["data"]["related"]}, tok=A)
    rec = json.loads(f.read_text())
    check("approving them adds the links to the published item", s == 200 and len(rec.get("related", [])) == 1 and rec.get("related_approved_by") == "Test Approver", rec)

print("People")
s, b, _ = call("/api/people", {"email": "New.Editor@Example.test", "name": "New Editor", "role": "editor"}, tok=A)
check("approver adds an editor (email stored in lower case)", s == 200 and any(p["email"] == "new.editor@example.test" for p in call("/api/people", tok=A)[1]), b)
check("new editor can log in at once", call("/api/me", tok=token("new.editor@example.test"))[0] == 200)
check("editor cannot add people", call("/api/people", {"email": "a@b.cd", "name": "x", "role": "approver"}, tok=E)[0] == 403)
check("approver cannot remove themself", call("/api/people/remove", {"email": "approver.one@example.test"}, tok=A)[0] == 400)
check("last approver cannot be made an editor", call("/api/people", {"email": "approver.one@example.test", "name": "Test Approver", "role": "editor"}, tok=A)[0] == 400)
s, b, _ = call("/api/people/remove", {"email": "new.editor@example.test"}, tok=A)
check("approver removes a person", s == 200, b)
check("removed person is locked out at once", call("/api/me", tok=token("new.editor@example.test"))[0] == 403)

print("Activity log")
s, log, _ = call("/api/log", tok=A)
acts = {x["action"] for x in log}
check("log records submit, edit, approve, reject, ingest and people changes",
      {"submit", "edit", "approve", "reject", "ingest", "person_add", "person_remove"} <= acts, acts)
check("editor cannot read the log", call("/api/log", tok=E)[0] == 403)

print(f"\n{OK} passed, {len(BAD)} failed")
sys.exit(1 if BAD else 0)
