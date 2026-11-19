// Review space for the Nordic CCS Supply-Chain Monitor.
// This page holds no data itself: after login it talks to the review API (Cloudflare Worker).
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KEY = "ccs-review-session";
  let CFG = null, ME = null, TOKEN = null, QUEUE = [];

  // ── session ──
  function readToken() {
    const m = location.hash.match(/session=([\w.-]+)/);
    if (m) {
      try { localStorage.setItem(KEY, m[1]); } catch (e) { /* private mode: keep it in memory */ }
      history.replaceState(null, "", location.pathname + location.search);
      return m[1];
    }
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function logout() {
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    TOKEN = null; ME = null;
    show("login");
  }
  function login() {
    const ret = location.origin + location.pathname;
    location.href = CFG.login_url.replace(/\/+$/, "") + "/?return=" + encodeURIComponent(ret);
  }

  // ── API ──
  async function api(path, body) {
    const r = await fetch(CFG.api_url.replace(/\/+$/, "") + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { authorization: "Bearer " + TOKEN, ...(body === undefined ? {} : { "content-type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* empty */ }
    if (r.status === 401) { logout(); throw Object.assign(new Error(data.error || "Please log in again."), { data }); }
    if (!r.ok) throw Object.assign(new Error(data.error || `Error ${r.status}`), { status: r.status, data });
    return data;
  }

  // ── messages ──
  function note(html, kind = "ok", where = $("#msg")) {
    where.innerHTML = html ? `<div class="note ${kind}">${html}</div>` : "";
    if (html && where === $("#msg")) where.scrollIntoView({ block: "nearest" });
  }
  const errHtml = (e) => esc(e.message) + (e.data && e.data.errors && e.data.errors.length > 1 ? "<ul>" + e.data.errors.map((x) => `<li>${esc(x)}</li>`).join("") + "</ul>" : "");
  const dupHtml = (d) => {
    const list = [...(d.duplicates || []), ...(d.similar || [])];
    if (!list.length) return "";
    const where = { published: "already on the public page", pending: "already waiting for review", rejected: "rejected earlier" };
    return "<ul>" + list.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a> — ${esc(where[x.where] || x.where)}</li>`).join("") + "</ul>";
  };

  // ── screens and tabs ──
  function show(id) {
    for (const s of ["login", "app", "notsetup"]) $("#" + s).hidden = s !== id;
    $("#who").hidden = id !== "app";
  }
  const tabs = () => [...document.querySelectorAll('.tabs [role="tab"]')].filter((t) => !t.hidden);
  function selectTab(tab, focus) {
    for (const t of tabs()) {
      const on = t === tab;
      t.setAttribute("aria-selected", on); t.tabIndex = on ? 0 : -1;
      $("#" + t.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
    const id = tab.id;
    if (id === "tab-queue") loadQueue();
    if (id === "tab-mine") loadMine();
    if (id === "tab-people") loadPeople();
    if (id === "tab-log") loadLog();
    note("");
  }
  document.querySelector(".tabs").addEventListener("click", (e) => { const t = e.target.closest('[role="tab"]'); if (t) selectTab(t); });
  document.querySelector(".tabs").addEventListener("keydown", (e) => {
    const list = tabs(), i = list.indexOf(document.activeElement);
    if (i < 0) return;
    if (e.key === "ArrowRight") { selectTab(list[(i + 1) % list.length], true); e.preventDefault(); }
    if (e.key === "ArrowLeft") { selectTab(list[(i - 1 + list.length) % list.length], true); e.preventDefault(); }
  });

  // ── item form (submit, and edit in the queue) ──
  function itemForm(it = {}, opts = {}) {
    const opt = (list, val) => list.map((c) => `<option value="${esc(c.key)}"${c.key === val ? " selected" : ""}>${esc(c.name)}</option>`).join("");
    const p = opts.prefix || "f";
    return `
      <label for="${p}-url">Link to the item <span class="hint">The page where people can read it (the original source if possible).</span>
        <input id="${p}-url" name="url" type="url" required value="${esc(it.url)}" placeholder="https://…"></label>
      <div class="dup" aria-live="polite"></div>
      <label for="${p}-title">Title <span class="hint">Exactly as on the page, in its original language.</span>
        <input id="${p}-title" name="title" required value="${esc(it.title)}"></label>
      <label for="${p}-title_en">English title <span class="hint">Only if the original title is not in English.</span>
        <input id="${p}-title_en" name="title_en" value="${esc(it.title_en)}"></label>
      <div class="grid2">
        <label for="${p}-source">Source <span class="hint">Organisation, outlet or journal, e.g. “SINTEF”.</span>
          <input id="${p}-source" name="source" required value="${esc(it.source)}"></label>
        <label for="${p}-date">Publication date
          <input id="${p}-date" name="date" type="date" required value="${esc(it.date)}"></label>
        <label for="${p}-type">Content type
          <select id="${p}-type" name="type" required><option value="">Choose…</option>${opt(CFG.types, it.type)}</select></label>
        <label for="${p}-category">Topic
          <select id="${p}-category" name="category" required><option value="">Choose…</option>${opt(CFG.categories, it.category)}</select></label>
        <label for="${p}-event_date" class="ev">Event or deadline date <span class="hint">For conferences, courses and calls.</span>
          <input id="${p}-event_date" name="event_date" type="date" value="${esc(it.event_date)}"></label>
        <label class="check rep" for="${p}-paywalled"><input id="${p}-paywalled" name="paywalled" type="checkbox"${it.paywalled ? " checked" : ""}>
          <span>Paywalled <span class="hint">The full report is not free. Summarise only what the public page says.</span></span></label>
      </div>
      <label for="${p}-authors">Authors <span class="hint">For papers and reports, as given (optional).</span>
        <input id="${p}-authors" name="authors" value="${esc(it.authors)}"></label>
      <label for="${p}-summary">Short summary <span class="hint">2–3 neutral sentences, only facts from the page.</span>
        <textarea id="${p}-summary" name="summary" required>${esc(it.summary)}</textarea></label>
      <label for="${p}-why">Why it matters (optional) <span class="hint">One line on why it matters for a Nordic CCS supply-chain project.</span>
        <input id="${p}-why" name="why_it_matters" value="${esc(it.why_it_matters)}"></label>`;
  }
  function wireTypeFields(form) {
    const upd = () => {
      const t = form.elements.type.value;
      form.querySelector(".ev").hidden = t !== "event";
      form.querySelector(".rep").hidden = t !== "report";
    };
    form.elements.type.addEventListener("change", upd); upd();
  }
  function formData(form) {
    const f = form.elements, out = {};
    for (const k of ["url", "title", "title_en", "source", "date", "type", "category", "event_date", "authors", "summary", "why_it_matters"]) out[k] = f[k] ? f[k].value.trim() : "";
    out.paywalled = !!(f.paywalled && f.paywalled.checked);
    return out;
  }
  async function checkDup(form, excludeId) {
    const d = formData(form), box = form.querySelector(".dup");
    if (!d.url && !d.title) { box.innerHTML = ""; return; }
    try {
      const r = await api("/api/check", { url: d.url, title: d.title, exclude_id: excludeId });
      if (r.duplicates.length) note("<strong>This item is already in the monitor:</strong>" + dupHtml({ duplicates: r.duplicates }), "err", box);
      else if (r.similar.length) note("<strong>Similar items exist.</strong> Please check this is not the same story:" + dupHtml({ similar: r.similar }), "warn", box);
      else box.innerHTML = "";
    } catch (e) { /* the server checks again on submit */ }
  }

  function setupSubmit() {
    const form = $("#submit-form");
    form.innerHTML = itemForm({}, { prefix: "s" }) + `<div class="actions"><button class="btn primary" type="submit">Submit for review</button><span class="meta">An approver will check it before it goes public.</span></div><div class="res" aria-live="polite"></div>`;
    wireTypeFields(form);
    form.elements.url.addEventListener("change", () => checkDup(form));
    form.elements.title.addEventListener("change", () => checkDup(form));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      await sendSubmit(form, false);
    });
  }
  async function sendSubmit(form, confirmSimilar) {
    const res = form.querySelector(".res"), btn = form.querySelector('button[type="submit"]');
    btn.disabled = true; note("Checking the link and looking for duplicates…", "ok", res);
    try {
      const r = await api("/api/submit", { ...formData(form), confirm_similar: confirmSimilar });
      setupSubmit();
      note(esc(r.message), "ok");
    } catch (err) {
      if (err.data && err.data.needs_confirm) {
        note(`<strong>${esc(err.message)}</strong>${dupHtml(err.data)}<div class="inline"><button type="button" class="btn primary" id="confirm-sim">It is a different item — submit anyway</button></div>`, "warn", res);
        $("#confirm-sim").addEventListener("click", () => sendSubmit(form, true));
      } else note(errHtml(err) + (err.data && err.data.duplicates ? dupHtml(err.data) : ""), "err", res);
    } finally { btn.disabled = false; }
  }

  // ── queue (approvers) ──
  const typeName = (k) => (CFG.types.find((t) => t.key === k) || {}).name || k;
  const catName = (k) => (CFG.categories.find((c) => c.key === k) || {}).name || k;
  const when = (iso) => { try { return new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }); } catch (e) { return iso; } };

  function relatedHtml(list, id, editable) {
    if (!list || !list.length) return "";
    return `<div class="rel"><strong>“You might also be interested in” links</strong>${editable ? ' <span class="meta">(untick a link to remove it)</span>' : ""}<ul>` +
      list.map((r, i) => `<li>${editable ? `<label class="check"><input type="checkbox" class="relk" data-i="${i}" checked aria-label="Keep this related link">` : ""}<span><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a> · ${esc(r.source)}<br><span class="reason">${esc(r.reason)}</span></span>${editable ? "</label>" : ""}</li>`).join("") + "</ul></div>";
  }
  function queueCard(q) {
    const d = q.data;
    const by = q.origin === "agent" ? `<span class="badge agent">Found by ${esc(q.submitted_by_name)}</span>` : `<span class="badge manual">Submitted by ${esc(q.submitted_by_name)}</span>`;
    if (q.kind === "related") {
      return `<article class="item" data-id="${esc(q.id)}"><div class="badges">${by}<span class="badge">New related links for a published item</span></div>
        <h3>For: <a href="${esc(d.target_url)}" target="_blank" rel="noopener">${esc(d.target_title)}</a></h3>
        ${relatedHtml(d.related, q.id, true)}
        <div class="actions"><button type="button" class="btn primary" data-act="approve">Approve links</button><button type="button" class="btn danger" data-act="reject">Reject</button></div><div class="res" aria-live="polite"></div></article>`;
    }
    return `<article class="item" data-id="${esc(q.id)}">
      <div class="badges"><label class="check"><input type="checkbox" class="sel" aria-label="Select this item"></label>${by}<span class="badge">${esc(typeName(d.type))}</span><span class="badge">${esc(catName(d.category))}</span>${d.paywalled ? '<span class="badge pay">paywalled</span>' : ""}${d.link_note ? `<span class="badge pay">${esc(d.link_note)}</span>` : ""}</div>
      <h3><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a></h3>
      ${d.title_en ? `<p class="meta"><em>${esc(d.title_en)}</em></p>` : ""}
      <p class="meta">${esc(d.source)} · ${esc(d.date)}${d.event_date ? " · event " + esc(d.event_date) : ""}${d.authors ? " · " + esc(d.authors) : ""} · added ${esc(when(q.submitted_at))}</p>
      <p>${esc(d.summary)}</p>
      ${d.why_it_matters ? `<p class="why"><strong>Why it matters:</strong> ${esc(d.why_it_matters)}</p>` : ""}
      ${relatedHtml(d.related, q.id, true)}
      <div class="actions"><button type="button" class="btn primary" data-act="approve">Approve</button><button type="button" class="btn" data-act="edit">Edit</button><button type="button" class="btn danger" data-act="reject">Reject</button></div>
      <div class="res" aria-live="polite"></div></article>`;
  }
  async function loadQueue() {
    const box = $("#queue");
    box.innerHTML = '<p class="meta">Loading…</p>';
    try {
      QUEUE = await api("/api/pending");
      $("#n-queue").textContent = QUEUE.length || "";
      $("#bulk").hidden = !QUEUE.some((q) => q.kind === "item");
      box.innerHTML = QUEUE.length ? QUEUE.map(queueCard).join("") : '<p class="empty">Nothing is waiting for review. 🎉</p>';
      updateBulk();
    } catch (e) { box.innerHTML = ""; note(errHtml(e), "err"); }
  }
  const keptRelated = (card, q) => {
    const boxes = [...card.querySelectorAll(".relk")];
    if (!boxes.length) return undefined;
    return (q.data.related || []).filter((_, i) => boxes[i] && boxes[i].checked);
  };
  function removeCard(card, text) {
    card.innerHTML = `<p class="meta">${esc(text)}</p>`;
    QUEUE = QUEUE.filter((x) => x.id !== card.dataset.id);
    $("#n-queue").textContent = QUEUE.length || "";
    setTimeout(() => {
      card.remove();
      if (!QUEUE.length) $("#queue").innerHTML = '<p class="empty">Nothing is waiting for review. 🎉</p>';
      $("#bulk").hidden = !QUEUE.some((q) => q.kind === "item");
      updateBulk();
    }, 1500);
  }
  async function approveOne(card, q, extra) {
    const res = card.querySelector(".res");
    const rel = keptRelated(card, q);
    const body = { ...(extra || {}) };
    if (rel && rel.length !== (q.data.related || []).length) body.related = rel;
    if (q.kind === "related" && rel) body.related = rel;
    note("Approving…", "ok", res);
    try {
      const r = await api(`/api/items/${encodeURIComponent(q.id)}/approve`, body);
      removeCard(card, "✓ " + r.message);
      return true;
    } catch (e) { note(errHtml(e), "err", res); return false; }
  }
  $("#queue").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".item"), q = QUEUE.find((x) => x.id === card.dataset.id), res = card.querySelector(".res");
    if (!q) return;
    const act = btn.dataset.act;
    if (act === "approve") return approveOne(card, q);
    if (act === "reject") {
      res.innerHTML = `<div class="inline"><input type="text" class="why-rej" placeholder="Reason (optional), e.g. off-topic, old, duplicate" aria-label="Reason for rejecting"><button type="button" class="btn danger" data-act="reject-yes">Yes, reject</button><button type="button" class="btn" data-act="cancel">Cancel</button></div>`;
      res.querySelector("input").focus();
      return;
    }
    if (act === "cancel") { res.innerHTML = ""; return; }
    if (act === "reject-yes") {
      const reason = res.querySelector(".why-rej").value;
      note("Rejecting…", "ok", res);
      try { const r = await api(`/api/items/${encodeURIComponent(q.id)}/reject`, { reason }); removeCard(card, "✗ " + r.message); }
      catch (e) { note(errHtml(e), "err", res); }
      return;
    }
    if (act === "edit") {
      res.innerHTML = `<form class="card editf" novalidate>${itemForm(q.data, { prefix: "e-" + q.id })}<div class="actions"><button type="submit" class="btn primary" data-mode="save">Save changes</button><button type="button" class="btn" data-act="save-approve">Save and approve</button><button type="button" class="btn" data-act="cancel">Cancel</button></div><div class="fres" aria-live="polite"></div></form>`;
      const f = res.querySelector("form");
      wireTypeFields(f);
      f.elements.url.addEventListener("change", () => checkDup(f, q.id));
      f.elements.url.focus();
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const r = await api(`/api/items/${encodeURIComponent(q.id)}/edit`, { ...formData(f), related: keptRelated(card, q) ?? q.data.related });
          q.data = r.data;
          const fresh = document.createElement("div"); fresh.innerHTML = queueCard(q);
          card.replaceWith(fresh.firstElementChild);
          note("Saved.", "ok");
        } catch (err) { note(errHtml(err), "err", f.querySelector(".fres")); }
      });
      return;
    }
    if (act === "save-approve") {
      const f = res.querySelector("form");
      const ok = await approveOne(card, q, { ...formData(f), related: keptRelated(card, q) ?? q.data.related });
      if (!ok) note("Not approved — see the message above.", "err", f.querySelector(".fres"));
    }
  });

  // bulk approve
  function updateBulk() {
    const sel = [...document.querySelectorAll("#queue .sel:checked")];
    $("#bulk-approve").disabled = !sel.length;
    $("#bulk-n").textContent = sel.length ? `${sel.length} selected` : "";
  }
  $("#queue").addEventListener("change", (e) => { if (e.target.classList.contains("sel")) updateBulk(); });
  $("#sel-all").addEventListener("change", (e) => { document.querySelectorAll("#queue .sel").forEach((c) => (c.checked = e.target.checked)); updateBulk(); });
  $("#bulk-approve").addEventListener("click", async () => {
    const cards = [...document.querySelectorAll("#queue .sel:checked")].map((c) => c.closest(".item"));
    $("#bulk-approve").disabled = true;
    let ok = 0;
    for (const card of cards) {
      const q = QUEUE.find((x) => x.id === card.dataset.id);
      if (q && (await approveOne(card, q))) ok++;
    }
    $("#sel-all").checked = false;
    note(`${ok} of ${cards.length} approved.` + (ok < cards.length ? " See the messages on the items that were not approved." : ""), ok === cards.length ? "ok" : "warn");
    updateBulk();
  });

  // ── my submissions ──
  async function loadMine() {
    const box = $("#mine");
    box.innerHTML = '<p class="meta">Loading…</p>';
    try {
      const rows = await api("/api/mine");
      const st = { pending: "Waiting for review", approved: "Approved — on the public page", rejected: "Not accepted" };
      box.innerHTML = rows.length ? rows.map((r) => `<article class="item"><div class="badges"><span class="badge ${r.status === "approved" ? "agent" : r.status === "pending" ? "" : "manual"}">${esc(st[r.status])}</span>${r.decided_by_name ? `<span class="meta">by ${esc(r.decided_by_name)}, ${esc(when(r.decided_at))}</span>` : ""}</div>
        <h3><a href="${esc(r.data.url)}" target="_blank" rel="noopener">${esc(r.data.title)}</a></h3><p class="meta">Submitted ${esc(when(r.submitted_at))}${r.reason ? " · reason: " + esc(r.reason) : ""}</p></article>`).join("")
        : '<p class="empty">You have not submitted anything yet.</p>';
    } catch (e) { box.innerHTML = ""; note(errHtml(e), "err"); }
  }

  // ── people (approvers) ──
  async function loadPeople() {
    const box = $("#people");
    try {
      const rows = await api("/api/people");
      box.innerHTML = `<table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr></thead><tbody>` + rows.map((p) => `<tr data-email="${esc(p.email)}" data-name="${esc(p.name)}">
        <td>${esc(p.name)}</td><td>${esc(p.email)}</td>
        <td><select class="role" aria-label="Role of ${esc(p.name)}"${p.email === ME.email ? " disabled" : ""}><option value="editor"${p.role === "editor" ? " selected" : ""}>Editor</option><option value="approver"${p.role === "approver" ? " selected" : ""}>Approver</option></select></td>
        <td>${p.email === ME.email ? '<span class="meta">you</span>' : `<button type="button" class="btn danger rm">Remove</button>`}<div class="res"></div></td></tr>`).join("") + "</tbody></table>";
    } catch (e) { note(errHtml(e), "err"); }
  }
  $("#people").addEventListener("change", async (e) => {
    if (!e.target.classList.contains("role")) return;
    const tr = e.target.closest("tr");
    try { await api("/api/people", { email: tr.dataset.email, name: tr.dataset.name, role: e.target.value }); note(`${esc(tr.dataset.name)} is now ${e.target.value === "approver" ? "an approver" : "an editor"}.`); }
    catch (err) { note(errHtml(err), "err"); loadPeople(); }
  });
  $("#people").addEventListener("click", async (e) => {
    const tr = e.target.closest("tr");
    if (e.target.classList.contains("rm")) {
      tr.querySelector(".res").innerHTML = `<div class="inline"><button type="button" class="btn danger rm-yes">Yes, remove</button><button type="button" class="btn rm-no">Cancel</button></div>`;
    } else if (e.target.classList.contains("rm-no")) tr.querySelector(".res").innerHTML = "";
    else if (e.target.classList.contains("rm-yes")) {
      try { await api("/api/people/remove", { email: tr.dataset.email }); note(`${esc(tr.dataset.name)} was removed and can no longer log in.`); loadPeople(); }
      catch (err) { note(errHtml(err), "err"); }
    }
  });
  $("#person-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    try {
      await api("/api/people", { email: f.email.value, name: f.name.value, role: f.role.value });
      note(`${esc(f.name.value)} was added. Send them this link: <strong>${esc(location.origin + location.pathname)}</strong>`);
      e.target.reset(); loadPeople();
    } catch (err) { note(errHtml(err), "err"); }
  });

  // ── activity log (approvers) ──
  async function loadLog() {
    const box = $("#log");
    try {
      const rows = await api("/api/log");
      const what = { submit: "submitted", edit: "edited", approve: "approved", reject: "rejected", ingest: "weekly agent run added items", person_add: "added a person", person_update: "changed a person", person_remove: "removed a person" };
      box.innerHTML = rows.length ? `<table><thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead><tbody>` + rows.map((r) => {
        const d = r.details || {};
        let detail = d.title ? `“${esc(d.title)}”` : "";
        if (r.action === "ingest") detail = `${d.added ?? 0} new item(s)${d.related_added ? `, ${d.related_added} related-link update(s)` : ""}${d.run_date ? ` (run of ${esc(d.run_date)})` : ""}`;
        if (r.action.startsWith("person")) detail = `${esc(d.name || "")} (${esc(d.email || "")})${d.role ? " as " + esc(d.role) : ""}`;
        if (r.action === "edit" && d.changed) detail += ` — changed: ${esc(d.changed.join(", "))}`;
        if (r.action === "reject" && d.reason) detail += ` — reason: ${esc(d.reason)}`;
        return `<tr><td>${esc(when(r.at))}</td><td>${esc(r.actor_name)}</td><td>${esc(what[r.action] || r.action)} ${detail}</td></tr>`;
      }).join("") + "</tbody></table>" : '<p class="empty">Nothing yet.</p>';
    } catch (e) { note(errHtml(e), "err"); }
  }

  // ── start ──
  $("#login-btn").addEventListener("click", login);
  async function start() {
    try { CFG = await (await fetch("config.json", { cache: "no-store" })).json(); } catch (e) { CFG = null; }
    if (!CFG || !CFG.api_url || !CFG.login_url) return show("notsetup");
    TOKEN = readToken();
    if (!TOKEN) return show("login");
    try { ME = await api("/api/me"); }
    catch (e) {
      show("login");
      if (e.status === 403) note(`${errHtml(e)} <button type="button" class="btn" id="other">Log in with another email</button>`, "err");
      const o = $("#other"); if (o) o.addEventListener("click", () => { logout(); login(); });
      return;
    }
    $("#who").innerHTML = `Logged in as <strong>${esc(ME.name)}</strong> (${ME.role === "approver" ? "approver" : "editor"}) · <a href="#" id="logout">Log out</a>`;
    $("#logout").addEventListener("click", (e) => { e.preventDefault(); logout(); note("You are logged out."); });
    document.querySelectorAll('[data-role="approver"]').forEach((t) => (t.hidden = ME.role !== "approver"));
    setupSubmit();
    show("app");
    selectTab(ME.role === "approver" ? $("#tab-queue") : $("#tab-submit"));
  }
  start();
})();
