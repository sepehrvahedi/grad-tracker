"use strict";

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TABS = {
  todo: (r) => r.my_status === "todo",
  progress: (r) => ["applied", "interviewing", "offer"].includes(r.my_status),
  done: (r) => ["rejected", "skip"].includes(r.my_status),
  all: () => true,
};

let DATA = null;
let OPEN_ID = null;
let SEL_ID = null;      // row selected with the keyboard
let ROWS = [];          // rows currently on screen, in order
const store = {
  get(k, d) { try { const v = localStorage.getItem("tracker." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("tracker." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const F = Object.assign({ tab: "todo", search: "", source: "any", trackr: "open", sort: "score", letter: false, week: false, new: false, min: 0,
  role: "", domain: "" }, store.get("filters", {}));
let PERSON = store.get("person", "");
const BOARD_SOURCES = ["greenhouse", "lever", "ashby"];

// ------------------------------------------------------------------ api
async function api(path, body) {
  if (body === undefined && PERSON && path.startsWith("/api/") && path !== "/api/refresh") {
    path += (path.includes("?") ? "&" : "?") + "person=" + encodeURIComponent(PERSON);
  }
  if (body !== undefined && PERSON) body = { person: PERSON, ...body };
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && res.status !== 409) throw new Error(data.error || res.statusText);
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("on"), 1800);
}

// ------------------------------------------------------------------ helpers
const scoreClass = (s) => (s == null ? "s-low" : s >= 85 ? "s-top" : s >= 70 ? "s-good" : s >= 50 ? "s-mid" : "s-low");
function closesCell(r) {
  if (!r.closing) return `<span class="muted">${r.trackr_status === "upcoming" ? "opens " + esc(r.opening || "later") : "—"}</span>`;
  const d = r.days_left;
  let badge = "";
  if (d < 0) badge = `<span class="badge">closed</span>`;
  else if (d === 0) badge = `<span class="badge urgent">today</span>`;
  else if (d <= 3) badge = `<span class="badge urgent">${d}d left</span>`;
  else if (d <= 7) badge = `<span class="badge soon">${d}d left</span>`;
  else badge = `<span class="badge">${d}d</span>`;
  return `${esc(r.closing)}<span class="days">${badge}</span>`;
}
const letterBadge = (r) => r.letter === "required" ? `<span class="badge req">required</span>`
  : r.letter === "optional" ? `<span class="badge">optional</span>`
  : r.letter === "unknown" ? `<span class="badge">unknown</span>` : `<span class="muted">—</span>`;
const visaBadge = (v) => v === "Yes" ? `<span class="badge yes">yes</span>` : v === "No" ? `<span class="badge no">no</span>` : `<span class="badge">?</span>`;
const trackrBadge = (s, removed) => removed ? `<span class="badge" title="No longer listed by any source; kept because you acted on it">removed</span>`
  : s === "open" ? `<span class="badge yes">open</span>` : s === "upcoming" ? `<span class="badge soon">upcoming</span>` : `<span class="badge">closed</span>`;
const sourceLabel = (s) => (DATA.source_labels || {})[s] || s;
const sourceChips = (r) => [...new Set((r.sources || []).map((s) => s.source))]
  .map((s) => `<span class="src">${esc(sourceLabel(s))}</span>`).join("");
const roleLabel = (k) => ((DATA.role_types || []).find((x) => x.key === k) || { label: k === "other" ? "Other" : k }).label;
const roleText = (r) => (r.roles || []).map(roleLabel).join(", ");
const domainText = (r) => (r.domains || []).map((k) => ((DATA.domain_types || []).find((x) => x.key === k) || { label: k }).label.split(/ & |, /)[0]).join(" · ");
function statusSelect(r) {
  const opts = Object.entries(DATA.statuses).map(([k, v]) => `<option value="${k}" ${k === r.my_status ? "selected" : ""}>${esc(v)}</option>`).join("");
  return `<select class="status-select" data-id="${esc(r.id)}" data-v="${esc(r.my_status)}">${opts}</select>`;
}

// ------------------------------------------------------------------ filtering
function resolveDomain(text) {
  const t = (text || "").trim().toLowerCase();
  if (!t) return null;
  const d = (DATA.domain_types || []).find((x) => [x.key, x.label.toLowerCase(), ...x.synonyms].includes(t));
  return d ? d.key : null;
}
function domainMatch(r, text) {
  const t = (text || "").trim().toLowerCase();
  if (!t) return true;
  const key = resolveDomain(t);
  if (key) return (r.domains || []).includes(key);
  return `${r.title} ${r.company} ${r.company_description || ""} ${r.snippet || ""} ${(r.categories || []).join(" ")}`
    .toLowerCase().includes(t);
}

function visibleRows(skip) {
  const q = F.search.trim().toLowerCase();
  let rows = DATA.positions.filter((r) => {
    if (skip !== "role" && F.role && !(r.roles || []).includes(F.role)) return false;
    if (skip !== "domain" && F.domain && !domainMatch(r, F.domain)) return false;
    if (!TABS[F.tab](r)) return false;
    const srcs = (r.sources || []).map((s) => s.source);
    if (F.source === "boards" && !srcs.some((s) => BOARD_SOURCES.includes(s))) return false;
    if (!["any", "boards"].includes(F.source) && !srcs.includes(F.source)) return false;
    const listingFilter = F.tab === "todo" || F.tab === "all";  // never hide applications because a listing closed
    if (listingFilter && F.trackr === "open" && r.trackr_status !== "open") return false;
    if (listingFilter && F.trackr === "open_upcoming" && r.trackr_status === "closed") return false;
    if (F.letter && !r.letter) return false;
    if (F.week && !(r.days_left != null && r.days_left >= 0 && r.days_left <= 7)) return false;
    if (F.new && !r.is_new) return false;
    if (F.min > 0 && (r.score ?? -1) < F.min) return false;
    if (q && !`${r.company} ${r.title} ${(r.categories || []).join(" ")} ${(r.divisions || []).join(" ")} ${(r.locations || []).join(" ")}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const by = {
    score: (a, b) => (b.score ?? -1) - (a.score ?? -1) || (b.rank || 0) - (a.rank || 0) || a.company.localeCompare(b.company),
    closing: (a, b) => (a.closing || "9999").localeCompare(b.closing || "9999") || (b.score ?? -1) - (a.score ?? -1),
    company: (a, b) => a.company.localeCompare(b.company) || a.title.localeCompare(b.title),
    new: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || (b.score ?? -1) - (a.score ?? -1),
  }[F.sort];
  return rows.sort(by);
}

// ------------------------------------------------------------------ render
function renderPeople() {
  const sel = $("#person");
  sel.innerHTML = DATA.people.map((p) => `<option value="${esc(p.slug)}" ${p.slug === DATA.person.slug ? "selected" : ""}>${esc(p.name)}${p.ready ? "" : " (no CV yet)"}</option>`).join("")
    + `<option value="+">+ Add another CV…</option>`;
  const banner = $("#person-banner");
  const err = (DATA.meta || {}).scoring_error;
  const msg = !DATA.person.ready ? `No CV for ${DATA.person.name} yet. Put a PDF at ${DATA.home}/${DATA.person.cv_hint}, then click Refresh.` : "";
  banner.textContent = msg || "";
  banner.classList.toggle("hidden", !msg);
  if (!msg && err) { banner.textContent = err; banner.classList.remove("hidden"); }
}

function renderHeader() {
  const m = DATA.meta || {};
  const merged = m.merged ? ` · ${m.merged} duplicates merged` : "";
  const feeds = m.sources ? Object.keys(m.sources).length : 0;
  $("#meta").textContent = m.fetched_at ? `fetched ${m.fetched_at.replace("T", " ").slice(0, 16)} · ${feeds} feeds${merged}` : "not fetched yet";
  $("#meta").title = m.sources ? Object.entries(m.sources).map(([k, v]) => `${k}: ${v}`).join("\n") : "";
  const c = DATA.counts;
  $("#stats").innerHTML = [
    ["to apply", c.open_todo, ""], ["closing this week", c.closing_week, c.closing_week ? "hot" : ""],
    ["applied", c.applied, ""], ["new", c.new, ""],
  ].map(([l, n, cls]) => `<span class="stat ${cls}"><b>${n}</b>${l}</span>`).join("");
  const srcs = [...new Set(DATA.positions.flatMap((r) => (r.sources || []).map((x) => x.source)))].sort();
  const boards = srcs.some((x) => BOARD_SOURCES.includes(x));
  $("#f-source").innerHTML = `<option value="any">All sources</option>` + (boards ? `<option value="boards">Company job boards</option>` : "")
    + srcs.filter((x) => !BOARD_SOURCES.includes(x)).map((x) => `<option value="${esc(x)}">${esc(sourceLabel(x))}</option>`).join("");
  $("#f-source").value = [...$("#f-source").options].some((o) => o.value === F.source) ? F.source : (F.source = "any");
  for (const btn of document.querySelectorAll(".tab")) {
    const n = DATA.positions.filter(TABS[btn.dataset.tab]).length;
    btn.dataset.label = btn.dataset.label || btn.textContent.trim();
    btn.innerHTML = `${esc(btn.dataset.label)}<span class="n">${n}</span>`;
    btn.classList.toggle("active", btn.dataset.tab === F.tab);
  }
}

function renderFinder() {
  const roleRows = visibleRows("role"), domainRows = visibleRows("domain");
  const roleCount = (k) => roleRows.filter((r) => (r.roles || []).includes(k)).length;
  const chip = (group, key, label, n, on) =>
    `<button type="button" class="chip ${on ? "on" : ""}" data-group="${group}" data-key="${esc(key)}" aria-pressed="${on}">${esc(label)}${n === null ? "" : `<span class="n">${n}</span>`}</button>`;
  $("#role-chips").innerHTML = chip("role", "", "All roles", roleRows.length, !F.role) +
    DATA.role_types.map((x) => [x, roleCount(x.key)]).filter(([x, n]) => n || F.role === x.key)
      .map(([x, n]) => chip("role", x.key, x.label, n, F.role === x.key)).join("");
  const active = resolveDomain(F.domain);
  $("#domain-chips").innerHTML = chip("domain", "", "Any", domainRows.length, !F.domain) +
    DATA.domain_types.map((x) => [x, domainRows.filter((r) => (r.domains || []).includes(x.key)).length])
      .filter(([x, n]) => n || active === x.key)
      .map(([x, n]) => chip("domain", x.key, x.label.split(/ & |, /)[0], n, active === x.key)).join("");
  $("#domain-list").innerHTML = DATA.domain_types.map((x) => `<option value="${esc(x.key)}">${esc(x.label)}</option>`).join("");
  if (document.activeElement !== $("#f-domain")) $("#f-domain").value = active ? "" : F.domain;
}

function renderRows() {
  renderFinder();
  const rows = ROWS = visibleRows();
  $("#count").textContent = `${rows.length} of ${DATA.positions.length}`;
  $("#empty").classList.toggle("hidden", rows.length > 0);
  if (!rows.length) {
    $("#empty").innerHTML = BUSY && !DATA.positions.length
      ? `<b>Fetching roles from the job boards…</b>This takes about a minute the first time. The list appears here when it is done.`
      : !DATA.fetched ? `<b>No roles fetched yet.</b>Click Refresh (or press r). The first fetch takes about a minute.`
      : !DATA.positions.length ? `<b>Nothing to show.</b>The last fetch returned no roles. Check your connection and refresh.`
      : `<b>Nothing matches these filters.</b>Try "Open + upcoming", clear the search, or pick "All" on the left.`;
  }
  if (!rows.some((r) => r.id === SEL_ID)) SEL_ID = null;
  $("#rows").innerHTML = rows.map((r) => `
    <tr data-id="${esc(r.id)}" class="${["rejected", "skip"].includes(r.my_status) ? "done" : ""}${r.id === SEL_ID ? " sel" : ""}">
      <td><span class="score ${scoreClass(r.score)}" title="Click the row for the breakdown">${r.score ?? "–"}</span></td>
      <td><div class="pos-company">${esc(r.company)}${r.is_new ? '<span class="badge new">new</span>' : ""}${sourceChips(r)}</div>
          <div class="pos-title">${esc(r.title)}</div></td>
      <td class="area">${esc(roleText(r))}${(r.domains || []).length ? `<div class="dom">${esc(domainText(r))}</div>` : ""}</td>
      <td>${closesCell(r)}</td>
      <td>${letterBadge(r)}</td>
      <td>${visaBadge(r.sponsors_visa)}</td>
      <td>${trackrBadge(r.trackr_status, r.removed)}</td>
      <td>${statusSelect(r)}</td>
    </tr>`).join("");
}

function render() {
  const setup = !!DATA.needs_setup;
  $("#setup").classList.toggle("hidden", !setup);
  $("#app").classList.toggle("hidden", setup);
  for (const el of ["#person", "#stats", "#btn-weights", "#btn-refresh"]) $(el).classList.toggle("hidden", setup);
  if (setup) { $("#meta").textContent = ""; $("#setup-home").textContent = DATA.home; return; }
  renderPeople(); renderHeader(); renderRows(); if (OPEN_ID) renderDrawer(OPEN_ID);
}

// ------------------------------------------------------------------ drawer
function partsHtml(r) {
  if (r.score == null) return `<p class="muted">Not scored yet (closed positions are not scored). Refresh to score new ones.</p>`;
  const dims = r.parts.filter((p) => p.kind === "dim").map((p) => `
    <div class="part"><div class="lbl">${esc(p.label)}<small>weight ${p.weight}%</small></div>
      <div class="bar"><i style="width:${p.value}%"></i></div><div class="pts">${p.value}</div>
      <div class="why">${esc(p.why)}</div></div>`).join("");
  const adj = r.parts.filter((p) => p.kind === "adj").map((p) => `
    <div class="part"><div class="lbl">${esc(p.label)}</div><div class="why" style="grid-column:auto;margin:0">${esc(p.why)}</div>
      <div class="pts ${p.points < 0 ? "neg" : p.points > 0 ? "pos" : ""}">${p.points > 0 ? "+" : ""}${p.points}</div></div>`).join("");
  const caps = (r.caps || []).map((c) => `<div class="cap">Capped at ${c.max}: ${esc(c.why)}</div>`).join("");
  return `<div class="big-score"><span class="score ${scoreClass(r.score)}">${r.score}</span>
      <span class="formula">weighted base ${r.base} ${r.parts.filter((p) => p.kind === "adj").map((p) => (p.points >= 0 ? "+ " : "− ") + Math.abs(p.points)).join(" ")}${r.caps.length ? " → capped" : ""}</span></div>
    ${dims}${adj}${caps}`;
}

function renderDrawer(id) {
  const r = DATA.positions.find((x) => x.id === id);
  if (!r) return closeDrawer();
  $("#d-company").textContent = r.company;
  $("#d-title").textContent = r.title;
  $("#d-body").innerHTML = `
    <div class="section"><div class="row-actions">
      ${r.url ? `<a class="btn" href="${esc(r.url)}" target="_blank" rel="noopener">Open application ↗</a>` : `<span class="muted">No application link yet (opens ${esc(r.opening || "later")})</span>`}
      ${statusSelect(r)}
    </div></div>
    <div class="section"><h3>Why this score</h3>${partsHtml(r)}</div>
    <div class="section"><h3>Cover letter</h3>${r.letter
      ? `<p class="muted">The listing says the cover letter is <b>${esc(r.letter)}</b>. Paste this prompt into the AI assistant you use:</p>
         <div class="row-actions" style="margin-bottom:8px"><button class="btn small" id="copy-prompt" disabled>Copy prompt</button><span class="muted" id="prompt-info">Loading…</span></div>
         <textarea class="prompt-box" id="prompt" readonly></textarea>`
      : `<p class="muted">Not requested for this programme.</p>`}</div>
    <div class="section"><h3>Details</h3><dl class="kv">
      <dt>Listing</dt><dd>${trackrBadge(r.trackr_status, r.removed)}</dd>
      <dt>Found on</dt><dd>${(r.sources || []).map((s) => s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(sourceLabel(s.source))} ↗</a>` : esc(sourceLabel(s.source))).join(" · ")}</dd>
      ${(r.locations || []).length ? `<dt>Locations</dt><dd>${esc(r.locations.join("; "))}</dd>` : ""}
      <dt>Opens / closes</dt><dd>${esc(r.opening || "—")} → ${esc(r.closing || "—")}${r.days_left != null && r.days_left >= 0 ? ` (${r.days_left} days left)` : ""}</dd>
      <dt>Written answers</dt><dd>${esc(r.written_answers)}</dd>
      <dt>Visa sponsorship</dt><dd>${visaBadge(r.sponsors_visa)}</dd>
      <dt>Location (detected)</dt><dd>${esc((r.location || "unknown").replace("_", " "))}</dd>
      <dt>Role type</dt><dd>${esc(roleText(r))}</dd>
      ${(r.domains || []).length ? `<dt>Domain</dt><dd>${esc(domainText(r))}</dd>` : ""}
      <dt>Area</dt><dd>${esc([...new Set([...(r.categories || []), ...(r.divisions || [])])].join(", "))}</dd>
      <dt>First seen</dt><dd>${esc(r.first_seen || "—")}</dd>
      ${r.notes ? `<dt>Listing notes</dt><dd>${esc(r.notes)}</dd>` : ""}
    </dl></div>
    ${r.company_description ? `<div class="section"><h3>About ${esc(r.company)}</h3><p class="desc">${esc(r.company_description)}</p></div>` : ""}
    <div class="section"><h3>My notes</h3><textarea class="note-box" id="note" placeholder="Referral, deadlines, interview dates…">${esc(r.note)}</textarea></div>`;
  if (r.letter) loadPrompt(r.id);
  const note = $("#note");
  note.addEventListener("input", () => {
    clearTimeout(note.t);
    note.t = setTimeout(async () => { await api("/api/note", { id: r.id, note: note.value }); r.note = note.value; toast("Note saved"); }, 600);
  });
}

async function loadPrompt(id) {
  try {
    const { prompt } = await api(`/api/prompt?id=${encodeURIComponent(id)}`);
    if (OPEN_ID !== id || !$("#prompt")) return;
    $("#prompt").value = prompt;
    $("#prompt-info").textContent = `${prompt.split(/\s+/).length} words · built only from your CV`;
    const btn = $("#copy-prompt"); btn.disabled = false;
    btn.onclick = async () => {
      try { await navigator.clipboard.writeText(prompt); } catch { $("#prompt").select(); document.execCommand("copy"); }
      toast("Prompt copied");
    };
  } catch (e) { $("#prompt-info").textContent = "Could not build prompt: " + e.message; }
}

function openDrawer(id) {
  OPEN_ID = id; renderDrawer(id);
  if (OPEN_ID) history.replaceState(null, "", "#" + encodeURIComponent(id));     // a link to this role
  $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false"); $("#scrim").classList.add("on");
}
function closeDrawer() {
  if (OPEN_ID) history.replaceState(null, "", location.pathname + location.search);
  OPEN_ID = null;
  $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); $("#scrim").classList.remove("on");
}

// ------------------------------------------------------------------ actions
async function setStatus(id, status) {
  const r = DATA.positions.find((x) => x.id === id);
  const prev = r.my_status;
  r.my_status = status;
  try {
    await api("/api/status", { id, status });
    toast(`${r.company}: ${DATA.statuses[status]}`);
  } catch (e) { r.my_status = prev; toast("Could not save: " + e.message); }
  const at = ROWS.findIndex((x) => x.id === id);
  render();
  if (SEL_ID === null && at >= 0 && ROWS.length && !OPEN_ID) select(ROWS[Math.min(at, ROWS.length - 1)].id);   // keep your place in the list
}

function openWeights() {
  const w = DATA.weights, L = DATA.weight_labels;
  const groups = [["What the score is made of (relative weights)", ["w_role_fit", "w_competitiveness", "w_career_value"]],
    ["Visa (points added)", ["visa_yes", "visa_unknown", "visa_no"]],
    ["Location (points added)", ["loc_london", "loc_uk_other", "loc_abroad", "loc_unknown"]],
    ["Red flags (maximum score)", ["cap_internship", "cap_early_start", "cap_off_target", "cap_too_senior"]]];
  $("#w-fields").innerHTML = groups.map(([g, keys]) => `<div class="group">${esc(g)}</div>` +
    keys.map((k) => `<label>${esc(L[k])}<input type="number" step="1" min="-100" max="100" name="${k}" value="${w[k]}"></label>`).join("")).join("");
  $("#weights").showModal();
}

async function saveWeights(reset) {
  const body = reset ? { reset: true } : { weights: Object.fromEntries([...document.querySelectorAll("#w-fields input")].map((i) => [i.name, Number(i.value)])) };
  DATA = await api("/api/weights", body);
  render(); toast(reset ? "Weights reset" : "Weights saved, re-ranked");
}

async function refresh() {
  try {
    const st = await api("/api/refresh", {});
    if (!st.running) return toast(st.error || "Refresh already finished");
    pollRefresh();
  } catch (e) { toast("Refresh failed: " + e.message); }
}
let BUSY = false;       // a refresh is running
async function pollRefresh() {
  const st = await api("/api/refresh");
  if (BUSY !== st.running) { BUSY = st.running; if (DATA && !DATA.needs_setup) renderRows(); }
  const banner = $("#refresh-banner");
  $("#btn-refresh").disabled = st.running;
  banner.classList.toggle("hidden", !st.running && !st.error);
  banner.classList.toggle("error", !!st.error);
  banner.textContent = st.error ? `Refresh failed: ${st.error}` : st.message;
  if (st.running) return setTimeout(pollRefresh, 1500);
  if (!st.error) { await load(); toast("Updated from every source"); }
}

async function load() {
  try { DATA = await api("/api/data"); }
  catch (e) {                       // the remembered person no longer exists
    if (!PERSON) throw e;
    PERSON = ""; store.set("person", ""); DATA = await api("/api/data");
  }
  if (DATA.person && PERSON !== DATA.person.slug) { PERSON = DATA.person.slug; store.set("person", PERSON); }
  if (DATA.positions.length && DATA.positions.every((r) => r.is_new)) {   // first fetch: "new" would mean nothing
    for (const r of DATA.positions) r.is_new = false;
    DATA.counts.new = 0;
  }
  render();
}

// ------------------------------------------------------------------ first run: add a CV
let CV_FILE = null;
function pickCv(file) {
  const err = $("#su-error"); err.classList.add("hidden");
  if (!file) return;
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { err.textContent = "That is not a PDF. Export your CV as a PDF and try again."; err.classList.remove("hidden"); return; }
  if (file.size > 15 * 1024 * 1024) { err.textContent = "That PDF is larger than 15 MB."; err.classList.remove("hidden"); return; }
  CV_FILE = file;
  $("#drop").classList.add("has");
  $("#drop-title").textContent = file.name;
  $("#drop-hint").textContent = `${Math.max(1, Math.round(file.size / 1024))} KB · click to choose another`;
}
const toBase64 = (file) => new Promise((ok, bad) => {
  const r = new FileReader();
  r.onload = () => ok(String(r.result).split(",")[1] || ""); r.onerror = () => bad(new Error("could not read the file"));
  r.readAsDataURL(file);
});
async function addPerson(body) {
  const err = $("#su-error"); err.classList.add("hidden");
  for (const b of ["#su-go", "#su-sample"]) $(b).disabled = true;
  try {
    const res = await fetch("/api/people", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(out.error || res.statusText);
    PERSON = out.slug; store.set("person", PERSON);
    CV_FILE = null; $("#setup-form").reset(); $("#drop").classList.remove("has");
    $("#drop-title").textContent = "Drop your CV here (PDF)"; $("#drop-hint").textContent = "or click to choose a file";
    $("#su-cancel").classList.add("hidden");
    await load();
    const st = await api("/api/refresh");
    if (st.running) pollRefresh(); else if (!DATA.fetched) refresh();
  } catch (e) { err.textContent = "Could not add this CV: " + e.message; err.classList.remove("hidden"); }
  finally { for (const b of ["#su-go", "#su-sample"]) $(b).disabled = false; }
}
function showSetup() {          // "+ Add another CV" from the person menu
  closeDrawer();
  $("#setup").classList.remove("hidden"); $("#app").classList.add("hidden"); $("#su-cancel").classList.remove("hidden");
  $("#setup-home").textContent = DATA.home; $("#su-name").focus();
}

// ------------------------------------------------------------------ keyboard and theme
function select(id, scroll) {
  SEL_ID = id;
  for (const tr of document.querySelectorAll("#rows tr")) tr.classList.toggle("sel", tr.dataset.id === id);
  if (scroll) { const tr = document.querySelector("#rows tr.sel"); if (tr) tr.scrollIntoView({ block: "nearest" }); }
}
function move(step) {
  if (!ROWS.length) return;
  const i = ROWS.findIndex((r) => r.id === SEL_ID);
  const next = ROWS[Math.max(0, Math.min(ROWS.length - 1, i < 0 ? 0 : i + step))];
  select(next.id, true);
  if (OPEN_ID) openDrawer(next.id);
}
const THEMES = ["auto", "light", "dark"];
function applyTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  $("#btn-theme").textContent = "Theme: " + t;
}

// ------------------------------------------------------------------ wiring
function bindFilters() {
  const map = { "#f-search": "search", "#f-source": "source", "#f-trackr": "trackr", "#f-sort": "sort", "#f-min": "min" };
  for (const [sel, key] of Object.entries(map)) {
    const el = $(sel); if (key !== "source") el.value = F[key];
    el.addEventListener("input", () => { F[key] = key === "min" ? Number(el.value || 0) : el.value; store.set("filters", F); renderRows(); });
  }
  for (const [sel, key] of [["#f-letter", "letter"], ["#f-week", "week"], ["#f-new", "new"]]) {
    const el = $(sel); el.checked = F[key];
    el.addEventListener("change", () => { F[key] = el.checked; store.set("filters", F); renderRows(); });
  }
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab"); if (!b) return;
    F.tab = b.dataset.tab; store.set("filters", F); render();
  });
}

document.addEventListener("change", (e) => {
  if (e.target.classList.contains("status-select")) setStatus(e.target.dataset.id, e.target.value);
});
$("#rows").addEventListener("click", (e) => {
  if (e.target.closest("select")) return;
  const tr = e.target.closest("tr[data-id]"); if (tr) { select(tr.dataset.id); openDrawer(tr.dataset.id); }
});
$("#d-close").onclick = closeDrawer;
$("#scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (OPEN_ID) closeDrawer(); else if (document.activeElement) document.activeElement.blur(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey || !DATA || DATA.needs_setup || document.querySelector("dialog[open]")) return;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ""))) return;
  const cur = SEL_ID || OPEN_ID;
  const row = cur && DATA.positions.find((r) => r.id === cur);
  const keys = {
    j: () => move(1), ArrowDown: () => move(1), k: () => move(-1), ArrowUp: () => move(-1),
    Enter: () => { if (!SEL_ID && ROWS.length) select(ROWS[0].id, true); if (SEL_ID) openDrawer(SEL_ID); },
    o: () => { if (row && row.url) window.open(row.url, "_blank", "noopener"); },
    a: () => row && setStatus(row.id, "applied"), x: () => row && setStatus(row.id, "skip"), u: () => row && setStatus(row.id, "todo"),
    "/": () => $("#f-search").focus(), r: () => refresh(), "?": () => $("#keys").showModal(),
  };
  if (keys[e.key]) { e.preventDefault(); keys[e.key](); }
});
$("#btn-keys").onclick = () => $("#keys").showModal();
let THEME = new URLSearchParams(location.search).get("theme") || store.get("theme", "auto");
if (!THEMES.includes(THEME)) THEME = "auto";
applyTheme(THEME);
$("#btn-theme").onclick = () => { THEME = THEMES[(THEMES.indexOf(THEME) + 1) % THEMES.length]; store.set("theme", THEME); applyTheme(THEME); };
$("#side-toggle").onclick = () => {
  const open = $("#side").classList.toggle("open"); $("#side-toggle").setAttribute("aria-expanded", String(open));
};
$("#cv-file").addEventListener("change", (e) => pickCv(e.target.files[0]));
const drop = $("#drop");
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => pickCv(e.dataTransfer.files[0]));
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#cv-file").click(); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());
$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!CV_FILE) { const err = $("#su-error"); err.textContent = "Add your CV first (a PDF)."; err.classList.remove("hidden"); return; }
  addPerson({ name: $("#su-name").value, cv_base64: await toBase64(CV_FILE), available_from: $("#su-start").value,
    needs_visa_sponsorship: $("#su-visa").value, looking_for: $("#su-looking").value });
});
$("#su-sample").onclick = () => addPerson({ sample: true });
$("#su-cancel").onclick = () => render();
$("#btn-weights").onclick = openWeights;
$("#weights").addEventListener("close", () => {
  const v = $("#weights").returnValue;
  if (v === "save") saveWeights(false); else if (v === "reset") saveWeights(true);
});
$("#btn-refresh").onclick = refresh;
document.querySelector(".finder").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  if (b.dataset.group === "role") F.role = F.role === b.dataset.key ? "" : b.dataset.key;
  else { F.domain = resolveDomain(F.domain) === b.dataset.key ? "" : b.dataset.key; $("#f-domain").value = ""; }
  store.set("filters", F); renderRows();
});
$("#f-domain").addEventListener("input", (e) => { F.domain = e.target.value; store.set("filters", F); renderRows(); });
$("#person").addEventListener("change", async (e) => {
  if (e.target.value === "+") { e.target.value = DATA.person.slug; return showSetup(); }
  PERSON = e.target.value; store.set("person", PERSON); closeDrawer();
  try { await load(); } catch (err) { toast("Could not load: " + err.message); }
});

bindFilters();
load().then(() => {
  const id = decodeURIComponent(location.hash.slice(1));
  if (id && DATA.positions.some((r) => r.id === id)) { select(id); openDrawer(id); }
}).then(() => api("/api/refresh").then((st) => { if (st.running) pollRefresh(); }))
  .catch((e) => { $("#meta").textContent = "could not load: " + e.message; });
