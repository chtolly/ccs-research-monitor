// API Worker for the review space of the Nordic CCS Supply-Chain Monitor.
//
// Roles:   editor   = can submit items and see their own submissions
//          approver = can also see, edit, approve and reject everything pending, and manage people
// Agent:   the weekly GitHub workflow sends verified agent items here (header x-ingest-key).
// Approve: the item is written to data/approved/<id>.json in the GitHub repository, which
//          rebuilds the public page. Reject: data/rejected/<id>.json (so it is never suggested again).
import { verifySession, b64encodeUtf8, b64decodeUtf8 } from "../../shared/session.js";

const TYPES = ["paper", "report", "news", "event"];
const ROLES = ["editor", "approver"];
const BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";

class HttpError extends Error {
  constructor(status, message, extra) {
    super(message);
    this.status = status;
    this.extra = extra;
  }
}
const fail = (status, message, extra) => { throw new HttpError(status, message, extra); };
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

// ── normalisation (mirrors scripts/common.py so both sides agree on duplicates) ──
const TRACKING = /^(utm_|fbclid|gclid|mc_|ref$|source$|cmp$)/;
export function normUrl(url) {
  if (!url) return "";
  let u;
  try { u = new URL(String(url).trim()); } catch { return String(url).trim().toLowerCase(); }
  const host = u.host.toLowerCase().replace(/^www\./, "");
  const path = u.pathname.replace(/\/+$/, "") || "/";
  const params = [...u.searchParams].filter(([k]) => !TRACKING.test(k.toLowerCase()));
  const q = params.length ? "?" + new URLSearchParams(params).toString() : "";
  return `https://${host}${path}${q}`;
}
export function normTitle(t) {
  return String(t || "").normalize("NFKC").toLowerCase().replace(/₂/g, "2").replace(/<[^>]+>/g, " ")
    .replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();
}
const STOP = new Set("the a an and or of for to in on at by with from is are be as its new into over under this that".split(" "));
function words(t) { return new Set(normTitle(t).split(" ").filter((w) => w.length > 2 && !STOP.has(w))); }
export function titleSimilarity(a, b) {
  const A = words(a), B = words(b);
  if (!A.size || !B.size) return 0;
  let inter = 0;
  for (const w of A) if (B.has(w)) inter++;
  return inter / (A.size + B.size - inter);
}
export async function itemId(url) {
  const d = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(normUrl(url)));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 10);
}
function normDoi(doi) { return String(doi || "").trim().toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, ""); }

// ── CORS (the review page lives on GitHub Pages, a different address) ──
function cors(request, env) {
  const origin = request.headers.get("origin") || "";
  const allowed = String(env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!allowed.includes(origin)) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "authorization, content-type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

export default {
  async fetch(request, env) {
    const headers = cors(request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    let res;
    try {
      res = await route(request, env, new URL(request.url));
    } catch (e) {
      if (e instanceof HttpError) res = json({ error: e.message, ...(e.extra || {}) }, e.status);
      else { console.error(e && e.stack || e); res = json({ error: "Something went wrong on the server. Please try again." }, 500); }
    }
    for (const [k, v] of Object.entries(headers)) res.headers.set(k, v);
    return res;
  },
};

// ── who is asking ──
async function person(request, env) {
  const m = (request.headers.get("authorization") || "").match(/^Bearer\s+(.+)$/);
  if (!m) fail(401, "Please log in.");
  const email = await verifySession(m[1], env.SESSION_SECRET);
  if (!email) fail(401, "Your login has expired. Please log in again.");
  const p = await env.DB.prepare("SELECT email, name, role FROM people WHERE email = ?").bind(email).first();
  if (!p) fail(403, `${email} is not on the list of editors. Ask an approver to add you.`, { email });
  return p;
}
function requireApprover(p) { if (p.role !== "approver") fail(403, "Only approvers can do this."); }
function requireAgent(request, env) {
  const got = request.headers.get("x-ingest-key") || "";
  const want = env.INGEST_SECRET || "";
  if (!want || got.length !== want.length) fail(401, "Wrong or missing ingest key.");
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= got.charCodeAt(i) ^ want.charCodeAt(i);
  if (diff) fail(401, "Wrong or missing ingest key.");
}
async function body(request) {
  try { return await request.json(); } catch { fail(400, "The request was not valid JSON."); }
}
async function audit(env, actor, action, itemId, details) {
  await env.DB.prepare("INSERT INTO audit (at, actor, actor_name, action, item_id, details) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(nowIso(), actor.email, actor.name, action, itemId || null, details ? JSON.stringify(details) : null).run();
}
const AGENT = { email: "agent", name: "Claude (weekly run)" };

// ── router ──
async function route(request, env, url) {
  const p = url.pathname.replace(/\/+$/, "");
  const M = request.method;
  if (p === "" || p === "/api") return json({ ok: true, service: "ccs-review-api" });

  // Agent endpoints (called by GitHub Actions with the ingest key)
  if (p === "/api/agent/ingest" && M === "POST") { requireAgent(request, env); return ingest(env, await body(request)); }
  if (p === "/api/agent/state" && M === "GET") { requireAgent(request, env); return agentState(env); }

  const me = await person(request, env);
  if (p === "/api/me" && M === "GET") return json(me);
  if (p === "/api/options" && M === "GET") return json(await siteConfig(env));
  if (p === "/api/check" && M === "POST") return json(await duplicates(env, await body(request)));
  if (p === "/api/submit" && M === "POST") return submit(env, me, await body(request));
  if (p === "/api/mine" && M === "GET") return mine(env, me);

  requireApprover(me);
  if (p === "/api/pending" && M === "GET") return pending(env);
  let m = p.match(/^\/api\/items\/([\w-]+)\/(edit|approve|reject)$/);
  if (m && M === "POST") {
    const b = await body(request);
    if (m[2] === "edit") return edit(env, me, m[1], b);
    if (m[2] === "approve") return approve(env, me, m[1], b);
    return reject(env, me, m[1], b);
  }
  if (p === "/api/people" && M === "GET") return json((await env.DB.prepare("SELECT email, name, role, added_by, added_at FROM people ORDER BY role DESC, name").all()).results);
  if (p === "/api/people" && M === "POST") return savePerson(env, me, await body(request));
  if (p === "/api/people/remove" && M === "POST") return removePerson(env, me, await body(request));
  if (p === "/api/log" && M === "GET") {
    const rows = (await env.DB.prepare("SELECT at, actor, actor_name, action, item_id, details FROM audit ORDER BY n DESC LIMIT 500").all()).results;
    return json(rows.map((r) => ({ ...r, details: r.details ? JSON.parse(r.details) : null })));
  }
  fail(404, "Not found.");
}

// ── public site data (categories, what is already published or rejected) ──
let cache = { at: 0, config: null, known: null };
async function fetchSite(env, file) {
  const r = await fetch(new URL(file, env.SITE_URL).toString(), { cf: { cacheTtl: 60 } });
  if (!r.ok) fail(502, `Could not read ${file} from the public site (HTTP ${r.status}).`);
  return r.json();
}
async function siteConfig(env) {
  if (!cache.config || Date.now() - cache.at > 300_000) {
    cache = { at: Date.now(), config: await fetchSite(env, "review/config.json"), known: await fetchSite(env, "known.json") };
  }
  return cache.config;
}
async function knownPublic(env) { await siteConfig(env); return cache.known; }

// ── duplicates: exact link/DOI = duplicate; very similar title = warning ──
async function duplicates(env, { url, title, doi, exclude_id }) {
  const nu = normUrl(url), nd = normDoi(doi);
  const out = { duplicates: [], similar: [] };
  const seen = (where, it) => ({ where, title: it.title, url: it.url, id: it.id });
  for (const k of await knownPublic(env)) {
    if (k.id === exclude_id) continue;
    if ((nu && k.url_norm === nu) || (nd && k.doi && k.doi === nd)) out.duplicates.push(seen(k.status, k));
    else if (title && titleSimilarity(title, k.title) >= 0.75) out.similar.push(seen(k.status, k));
  }
  const rows = (await env.DB.prepare("SELECT id, status, data, url_norm, title FROM queue WHERE kind = 'item'").all()).results;
  for (const r of rows) {
    if (r.id === exclude_id) continue;
    const d = JSON.parse(r.data);
    const where = r.status === "pending" ? "pending" : r.status === "approved" ? "published" : "rejected";
    if ((nu && r.url_norm === nu) || (nd && normDoi(d.doi) === nd)) out.duplicates.push(seen(where, { ...d, id: r.id }));
    else if (title && titleSimilarity(title, r.title) >= 0.75) out.similar.push(seen(where, { ...d, id: r.id }));
  }
  return out;
}

// ── validation of item fields (same rules for editors, approvers and edits) ──
const DATE_RX = /^\d{4}-\d{2}-\d{2}$/;
const clean = (v, max) => (v == null ? "" : String(v).replace(/\s+/g, " ").trim().slice(0, max));
async function validate(env, b) {
  const cfg = await siteConfig(env);
  const cats = cfg.categories.map((c) => c.key);
  const it = {
    title: clean(b.title, 400), title_en: clean(b.title_en, 400) || null, url: clean(b.url, 1000),
    source: clean(b.source, 200), date: clean(b.date, 10), category: clean(b.category, 40), type: clean(b.type, 20),
    summary: clean(b.summary, 1200), why_it_matters: clean(b.why_it_matters, 400) || null,
    authors: clean(b.authors, 600) || null, event_date: clean(b.event_date, 10) || null,
    paywalled: b.paywalled === true || b.paywalled === "true", doi: normDoi(b.doi) || null,
  };
  const errs = [];
  if (!it.title) errs.push("Title is required.");
  if (!/^https?:\/\/[^\s/]+\.[^\s]+$/.test(it.url)) errs.push("The link must be a full web address starting with https://");
  if (!it.source) errs.push("Source is required (e.g. 'SINTEF' or 'Offshore Energy').");
  if (!DATE_RX.test(it.date)) errs.push("Publication date is required (YYYY-MM-DD).");
  else if (new Date(it.date) > new Date(Date.now() + 2 * 86400_000)) errs.push("The publication date is in the future.");
  if (!cats.includes(it.category)) errs.push("Please choose a topic.");
  if (!TYPES.includes(it.type)) errs.push("Please choose a content type.");
  if (it.summary.length < 40) errs.push("Please write a short summary (at least one full sentence).");
  if (it.summary.length > 900) errs.push("The summary is too long (max about 900 characters).");
  if (it.type === "event" && !DATE_RX.test(it.event_date || "")) errs.push("Events need the event or deadline date.");
  if (it.event_date && !DATE_RX.test(it.event_date)) errs.push("Event date must be YYYY-MM-DD.");
  if (it.type !== "report") it.paywalled = false;
  if (Array.isArray(b.related)) it.related = cleanRelated(b.related);
  if (errs.length) fail(400, errs.join(" "), { errors: errs });
  return it;
}
function cleanRelated(list) {
  return list.filter((r) => r && /^https?:\/\//.test(r.url || "") && r.title).slice(0, 5).map((r) => ({
    title: clean(r.title, 300), url: clean(r.url, 1000), source: clean(r.source, 200), reason: clean(r.reason, 300),
  }));
}

// Does the link open? 404/410/5xx or no answer = no. 401/403/429 = the site blocks robots; the link is probably fine.
async function checkLink(url) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 12000);
  try {
    const r = await fetch(url, { redirect: "follow", signal: ctl.signal, headers: { "user-agent": BROWSER_UA, accept: "text/html,*/*" } });
    if (r.status < 400) return { ok: true };
    if ([401, 403, 429].includes(r.status)) return { ok: true, unverified: `the site blocks automatic checks (HTTP ${r.status})` };
    return { ok: false, why: `the link does not open (HTTP ${r.status})` };
  } catch (e) {
    return { ok: false, why: "the link does not open (no answer from the website)" };
  } finally {
    clearTimeout(t);
  }
}

// ── editor actions ──
async function submit(env, me, b) {
  const it = await validate(env, b);
  delete it.related; // related links come from the weekly agent run, not from the form
  const dup = await duplicates(env, it);
  if (dup.duplicates.length) fail(409, "This item is already in the monitor.", dup);
  if (dup.similar.length && !b.confirm_similar) fail(409, "An item with a very similar title already exists. Is this the same story?", { ...dup, needs_confirm: true });
  const link = await checkLink(it.url);
  if (!link.ok) fail(400, `Please check the link: ${link.why}.`);
  if (link.unverified) it.link_note = link.unverified;
  const id = await itemId(it.url);
  if (await env.DB.prepare("SELECT 1 FROM queue WHERE id = ?").bind(id).first()) fail(409, "This link was already submitted.");
  const at = nowIso();
  await env.DB.prepare(`INSERT INTO queue (id, kind, status, origin, data, url_norm, title, submitted_by, submitted_by_name, submitted_at)
    VALUES (?, 'item', 'pending', 'manual', ?, ?, ?, ?, ?, ?)`)
    .bind(id, JSON.stringify(it), normUrl(it.url), it.title, me.email, me.name, at).run();
  await audit(env, me, "submit", id, { title: it.title });
  return json({ ok: true, id, message: "Thank you! Your item is now waiting for an approver." });
}

async function mine(env, me) {
  const rows = (await env.DB.prepare("SELECT id, status, data, submitted_at, decided_by_name, decided_at, reason FROM queue WHERE submitted_by = ? ORDER BY submitted_at DESC LIMIT 200").bind(me.email).all()).results;
  return json(rows.map((r) => ({ ...r, data: JSON.parse(r.data) })));
}

// ── approver actions ──
async function pending(env) {
  const rows = (await env.DB.prepare("SELECT id, kind, origin, data, target_id, submitted_by_name, submitted_at FROM queue WHERE status = 'pending' ORDER BY submitted_at DESC, id").all()).results;
  return json(rows.map((r) => ({ ...r, data: JSON.parse(r.data) })));
}

async function getPending(env, id) {
  const r = await env.DB.prepare("SELECT * FROM queue WHERE id = ?").bind(id).first();
  if (!r) fail(404, "Item not found.");
  if (r.status !== "pending") fail(409, `This item was already ${r.status} by ${r.decided_by_name || "someone"}.`);
  return { ...r, data: JSON.parse(r.data) };
}

function changedFields(a, b) {
  return Object.keys({ ...a, ...b }).filter((k) => JSON.stringify(a[k] ?? null) !== JSON.stringify(b[k] ?? null));
}

async function edit(env, me, id, b) {
  const r = await getPending(env, id);
  if (r.kind === "related") {
    const data = { ...r.data, related: cleanRelated(b.related || []) };
    await env.DB.prepare("UPDATE queue SET data = ? WHERE id = ?").bind(JSON.stringify(data), id).run();
    await audit(env, me, "edit", id, { changed: ["related"] });
    return json({ ok: true, data });
  }
  const it = await validate(env, { ...r.data, ...b });
  const keep = Object.fromEntries(Object.entries(r.data).filter(([k]) => !(k in it))); // agent fields (candidate_id, run_date…)
  const data = { ...keep, ...it };
  if (normUrl(data.url) !== r.url_norm) {
    const dup = await duplicates(env, { url: data.url, exclude_id: id });
    if (dup.duplicates.length) fail(409, "Another item already uses this link.", dup);
    const link = await checkLink(data.url);
    if (!link.ok) fail(400, `Please check the link: ${link.why}.`);
  }
  await env.DB.prepare("UPDATE queue SET data = ?, url_norm = ?, title = ? WHERE id = ?").bind(JSON.stringify(data), normUrl(data.url), data.title, id).run();
  await audit(env, me, "edit", id, { changed: changedFields(r.data, data) });
  return json({ ok: true, data });
}

async function approve(env, me, id, b) {
  if (b && Object.keys(b).length) await edit(env, me, id, b);
  const r = await getPending(env, id);
  const at = nowIso();
  if (r.kind === "related") {
    // Add the approved related links to an item that is already public
    const path = `data/approved/${r.target_id}.json`;
    const file = await github(env, "GET", path);
    if (!file) fail(404, "The item these links belong to is no longer published.");
    const item = JSON.parse(b64decodeUtf8(file.content));
    item.related = r.data.related;
    item.related_approved_by = me.name;
    item.related_approved_at = at;
    await github(env, "PUT", path, { content: item, sha: file.sha, message: `Related links approved for "${item.title.slice(0, 60)}" (by ${me.name})` });
  } else {
    const item = {
      id, ...r.data,
      origin: r.origin,
      submitted_by: r.submitted_by_name,
      submitted_at: r.submitted_at,
      approved_by: me.name,
      approved_at: at,
    };
    delete item.link_note;
    const path = `data/approved/${id}.json`;
    const existing = await github(env, "GET", path);
    await github(env, "PUT", path, { content: item, sha: existing?.sha, message: `Approve: ${item.title.slice(0, 70)} (by ${me.name})` });
  }
  await env.DB.prepare("UPDATE queue SET status = 'approved', decided_by = ?, decided_by_name = ?, decided_at = ? WHERE id = ? AND status = 'pending'")
    .bind(me.email, me.name, at, id).run();
  await audit(env, me, "approve", id, { title: r.data.title || r.data.target_title });
  return json({ ok: true, message: "Approved. It will be on the public page in a few minutes." });
}

async function reject(env, me, id, b) {
  const r = await getPending(env, id);
  const at = nowIso();
  const reason = clean(b.reason, 300) || null;
  if (r.kind === "item") {
    // Public record so the weekly agent never suggests it again (no emails, only names)
    const rec = { id, url: r.data.url, title: r.data.title, doi: r.data.doi || null, origin: r.origin, rejected_by: me.name, rejected_at: at, reason };
    const path = `data/rejected/${id}.json`;
    const existing = await github(env, "GET", path);
    await github(env, "PUT", path, { content: rec, sha: existing?.sha, message: `Reject: ${String(r.data.title).slice(0, 70)} (by ${me.name})` });
  }
  await env.DB.prepare("UPDATE queue SET status = 'rejected', decided_by = ?, decided_by_name = ?, decided_at = ?, reason = ? WHERE id = ? AND status = 'pending'")
    .bind(me.email, me.name, at, reason, id).run();
  await audit(env, me, "reject", id, { title: r.data.title || r.data.target_title, reason });
  return json({ ok: true, message: "Rejected. It will not be suggested again." });
}

// ── people ──
async function savePerson(env, me, b) {
  const email = clean(b.email, 200).toLowerCase();
  const name = clean(b.name, 120);
  const role = clean(b.role, 20);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) fail(400, "Please enter a valid email address.");
  if (!name) fail(400, "Please enter the person's name.");
  if (!ROLES.includes(role)) fail(400, "Role must be editor or approver.");
  const old = await env.DB.prepare("SELECT role FROM people WHERE email = ?").bind(email).first();
  if (old && old.role === "approver" && role !== "approver") await keepOneApprover(env, email);
  await env.DB.prepare("INSERT INTO people (email, name, role, added_by, added_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(email) DO UPDATE SET name = excluded.name, role = excluded.role")
    .bind(email, name, role, me.name, nowIso()).run();
  await audit(env, me, old ? "person_update" : "person_add", null, { email, name, role });
  return json({ ok: true });
}
async function keepOneApprover(env, email) {
  const n = (await env.DB.prepare("SELECT COUNT(*) AS n FROM people WHERE role = 'approver' AND email != ?").bind(email).first()).n;
  if (!n) fail(400, "There must always be at least one approver.");
}
async function removePerson(env, me, b) {
  const email = clean(b.email, 200).toLowerCase();
  if (email === me.email) fail(400, "You cannot remove yourself.");
  const old = await env.DB.prepare("SELECT name, role FROM people WHERE email = ?").bind(email).first();
  if (!old) fail(404, "Person not found.");
  if (old.role === "approver") await keepOneApprover(env, email);
  await env.DB.prepare("DELETE FROM people WHERE email = ?").bind(email).run();
  await audit(env, me, "person_remove", null, { email, name: old.name });
  return json({ ok: true });
}

// ── agent: weekly items into the pending queue ──
async function ingest(env, b) {
  const items = Array.isArray(b.items) ? b.items : [];
  const updates = Array.isArray(b.related_updates) ? b.related_updates : [];
  const at = nowIso();
  const known = await knownPublic(env);
  const knownUrls = new Set(known.map((k) => k.url_norm));
  const knownDois = new Set(known.map((k) => k.doi).filter(Boolean));
  const res = { added: 0, skipped: [], related_added: 0 };
  for (const raw of items) {
    if (!raw || !raw.url || !raw.title) continue;
    const nu = normUrl(raw.url);
    const id = raw.id || await itemId(raw.url);
    if (knownUrls.has(nu) || (raw.doi && knownDois.has(normDoi(raw.doi)))) { res.skipped.push({ id, why: "already published or rejected" }); continue; }
    const dupe = await env.DB.prepare("SELECT status FROM queue WHERE id = ? OR (url_norm = ? AND kind = 'item')").bind(id, nu).first();
    if (dupe) { res.skipped.push({ id, why: `already ${dupe.status}` }); continue; }
    const data = { ...raw, related: cleanRelated(raw.related || []) };
    delete data.id;
    await env.DB.prepare(`INSERT INTO queue (id, kind, status, origin, data, url_norm, title, submitted_by, submitted_by_name, submitted_at)
      VALUES (?, 'item', 'pending', 'agent', ?, ?, ?, 'agent', ?, ?)`)
      .bind(id, JSON.stringify(data), nu, raw.title, `Claude (weekly run ${raw.run_date || b.run_date || at.slice(0, 10)})`, at).run();
    res.added++;
  }
  for (const u of updates) {
    const related = cleanRelated(u.related || []);
    if (!u.item_id || !related.length) continue;
    const row = await env.DB.prepare("SELECT id, status, data FROM queue WHERE id = ? AND kind = 'item'").bind(u.item_id).first();
    if (row && row.status === "pending") {
      const data = JSON.parse(row.data);
      if (data.related && data.related.length) continue;
      data.related = related;
      await env.DB.prepare("UPDATE queue SET data = ? WHERE id = ?").bind(JSON.stringify(data), row.id).run();
      res.related_added++;
    } else if (known.some((k) => k.id === u.item_id && k.status === "published") || (row && row.status === "approved")) {
      const rid = `rel-${u.item_id}`;
      if (await env.DB.prepare("SELECT 1 FROM queue WHERE id = ?").bind(rid).first()) continue;
      const target = known.find((k) => k.id === u.item_id) || { title: JSON.parse(row.data).title, url: JSON.parse(row.data).url };
      await env.DB.prepare(`INSERT INTO queue (id, kind, status, origin, data, url_norm, title, target_id, submitted_by, submitted_by_name, submitted_at)
        VALUES (?, 'related', 'pending', 'agent', ?, NULL, ?, ?, 'agent', ?, ?)`)
        .bind(rid, JSON.stringify({ target_title: target.title, target_url: target.url, related }), `Related links for: ${target.title}`, u.item_id,
          `Claude (weekly run ${b.run_date || at.slice(0, 10)})`, at).run();
      res.related_added++;
    }
  }
  await audit(env, AGENT, "ingest", null, { run_date: b.run_date || null, added: res.added, skipped: res.skipped.length, related_added: res.related_added });
  return json({ ok: true, ...res });
}

// What the weekly agent needs to know: what is pending (to avoid repeats) and which
// manual items still have no related links.
async function agentState(env) {
  const rows = (await env.DB.prepare("SELECT id, kind, origin, data, target_id FROM queue WHERE status = 'pending'").all()).results;
  const pend = [], needs = [];
  for (const r of rows) {
    const d = JSON.parse(r.data);
    if (r.kind === "item") {
      pend.push({ id: r.id, url: d.url, title: d.title, doi: d.doi || null });
      if (r.origin === "manual" && !(d.related || []).length) needs.push({ id: r.id, title: d.title, url: d.url, summary: d.summary, category: d.category, type: d.type });
    } else {
      needs.push({ id: r.target_id, has_pending_related: true });
    }
  }
  return json({ pending: pend, needs_related: needs.filter((n) => !n.has_pending_related) , pending_related_targets: needs.filter((n) => n.has_pending_related).map((n) => n.id) });
}

// ── GitHub contents API ──
async function github(env, method, path, opts = {}) {
  const base = (env.GITHUB_API || "https://api.github.com").replace(/\/+$/, "");
  const url = `${base}/repos/${env.GITHUB_REPO}/contents/${path}` + (method === "GET" ? `?ref=${encodeURIComponent(env.GITHUB_BRANCH)}` : "");
  const headers = {
    authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
    "x-github-api-version": "2022-11-28", "user-agent": "ccs-review-api",
  };
  const init = { method, headers };
  if (method === "PUT") {
    init.body = JSON.stringify({
      message: opts.message, branch: env.GITHUB_BRANCH, sha: opts.sha || undefined,
      content: b64encodeUtf8(JSON.stringify(opts.content, null, 1) + "\n"),
      committer: { name: "CCS review space", email: "41898282+github-actions[bot]@users.noreply.github.com" },
    });
    headers["content-type"] = "application/json";
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    let r;
    try { r = await fetch(url, init); } catch (e) {
      console.error("GitHub unreachable", method, path, e && e.message);
      if (attempt < 2) { await new Promise((s) => setTimeout(s, 800 * (attempt + 1))); continue; }
      fail(502, "Could not reach GitHub. Nothing was changed; please try again in a minute.");
    }
    if (method === "GET" && r.status === 404) return null;
    if (r.ok) return r.json();
    if (r.status === 409 || r.status >= 500) { await new Promise((s) => setTimeout(s, 800 * (attempt + 1))); continue; }
    const t = await r.text();
    console.error("GitHub", method, path, r.status, t.slice(0, 300));
    fail(502, `Could not save to GitHub (HTTP ${r.status}). Nothing was changed; please try again.`);
  }
  fail(502, "GitHub was busy. Nothing was changed; please try again.");
}
