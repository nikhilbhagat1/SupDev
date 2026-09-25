// Supdev chat + kanban UI. All dynamic text goes through the DOM builder `h` (textContent) — no innerHTML.
const $ = _$;
const st = {
  mode: localStorage.getItem("sd_mode") || "dev", sid: localStorage.getItem("sd_sid") || null,
  busy: false, modes: [], feed: null, typing: null, ctx: null, starting: false, v: null,
};
// Nothing about the workflow is hardcoded here: mode names come from /api/modes, issue types and statuses from Jira,
// and colours are derived from the name so any type / mode (including plugins) gets a stable colour.
const PALETTE = ["blue", "green", "orange", "purple", "red", "gray"];
const colorFor = (name) => { let n = 0; for (const c of String(name || "").toLowerCase()) n = (n * 31 + c.charCodeAt(0)) >>> 0; return PALETTE[n % PALETTE.length]; };
const modeLabel = (m) => (st.modes.find((x) => x.name === m) || {}).label || m;
const shortId = (id) => (id || "").replace(/^(s_|wi_)/, "").slice(0, 6).toUpperCase();

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { ...opts, headers: { ...identHeaders(), ...(opts.headers || {}) } });
  const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) {}
  if (!r.ok) throw new Error((j && (j.detail?.[0]?.msg || j.detail)) || t || r.statusText);
  return j;
}
const loz = (text, color) => h("span", { class: "lozenge " + (color || "") }, text);

// ------------------------------------------------------------------------------------------ shell
function setState(k, v) { st[k] = v; localStorage.setItem("sd_" + k, v ?? ""); }
function renderNav() {
  $("nav-board").classList.add("active");
  const seg = $("modeseg");
  if (seg.children.length !== st.modes.length) seg.replaceChildren(...st.modes.map((m) => h("button", { "data-mode": m.name, role: "tab", onclick: () => { setState("mode", m.name); closePanel(); show(); } }, m.label)));
  document.querySelectorAll("#modeseg button").forEach((b) => { const on = b.dataset.mode === st.mode; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
}
async function show() { renderNav(); return showBoard(); }
async function refreshBoard() { try { boardData = await api("/board?mode=" + st.mode); drawColumns(); } catch (e) { /* keep what we have */ } }

const CAT_COLOR = { new: "gray", indeterminate: "blue", done: "green" };
const rel = { list: [], project: null, configured: null, selected: "", keys: null, truncated: false,
  types: null, group: localStorage.getItem("sd_group") || "jira" };
let boardData = null;
let jira = { configured: null, project: null, columns: [], issues: [], epics: [], can_move: false, error: null, syncedAt: 0 };
let syncTimer = null, syncTick = 0;
const useJira = () => rel.group === "jira" && jira.configured && jira.columns.length > 0;

// ---- items: Jira tickets (tasks/stories/bugs) merged with Supdev work items and chats
function buildItems() {
  const epicKeys = new Set((jira.epics || []).map((k) => k.toUpperCase()));           // epics are never shown — nor work items opened on them
  const cards = (boardData ? boardData.cards : []).filter((c) => !(c.ref && epicKeys.has(c.ref.toUpperCase()))), chats = boardData ? boardData.backlog : [];
  const byKey = new Map(); cards.forEach((c) => c.ref && byKey.set(c.ref.toUpperCase(), c));
  const active = activeTypes(), tickets = jira.issues.filter((i) => active.has(i.type.toLowerCase()));
  const linked = new Set(), out = [];
  tickets.forEach((i) => {
    const wi = byKey.get(i.key.toUpperCase()) || null; if (wi) linked.add(wi.work_item_id);
    out.push({ key: i.key, title: i.summary, type: i.type, status: i.status, status_id: i.status_id, cat: i.status_category, assignee: i.assignee, url: i.url, issue: i, wi });
  });
  cards.filter((c) => !linked.has(c.work_item_id)).forEach((c) => out.push({ key: c.ref, title: c.title, type: "", wi: c, issue: null }));
  chats.forEach((c) => out.push({ key: null, title: c.title, type: "", chat: c, issue: null }));
  return out;
}
const keepItem = (x) => {
  if (rel.keys && !(x.key && rel.keys.has(x.key.toUpperCase()))) return false;            // release filter (by ticket key)
  return true;
};
function jiraColumns(items) {
  const cols = jira.columns.map((c, i) => ({ id: "j" + i, title: c.name, status_ids: c.status_ids, items: [], droppable: jira.can_move }));
  const other = { id: "jo", title: "Other statuses", items: [] }, local = { id: "jl", title: "Supdev only", cls: "backlog", items: [], hint: "Chats and work items without a Jira ticket" };
  items.forEach((x) => { if (!x.issue) { local.items.push(x); return; } (cols.find((c) => c.status_ids.includes(x.status_id)) || other).items.push(x); });
  return [...cols, ...(other.items.length ? [other] : []), ...(local.items.length ? [local] : [])];   // Jira columns lead, To Do first
}
function phaseColumns(items) {
  const backlog = { id: "pb", title: "Backlog", cls: "backlog", items: [] }, cols = boardData.phases.map((t, i) => ({ id: "p" + i, title: `${i + 1}. ${t}`, items: [] }));
  items.forEach((x) => { if (x.wi && x.wi.phase) cols[x.wi.phase - 1].items.push(x); else backlog.items.push(x); });
  return [backlog, ...cols];
}

function tile(x, col, cols) {
  const wi = x.wi, canDrag = !!(x.issue && col.droppable);
  const keyEl = x.url ? h("a", { class: "key", href: x.url, target: "_blank", rel: "noopener noreferrer", title: "Open in Jira" }, x.key)
    : h("span", { class: "key" }, x.key || shortId(x.chat ? x.chat.session_id : wi && wi.session_id));
  const meta = [];
  if (!useJira() && x.issue) meta.push(loz(x.status, CAT_COLOR[x.cat] || "gray"));       // in the phase view show the Jira status
  if (useJira() && wi) meta.push(loz(`Phase ${wi.phase}/${boardData.phases.length}`, "purple"));
  if (wi && wi.pending) meta.push(loz("approval", "yellow"));
  if (wi && wi.status === "parked") meta.push(loz("parked", "gray"));
  if (wi && wi.status === "handed_off") meta.push(loz("handed off", "purple"));
  const extra = [];
  if (x.issue && !wi) extra.push(h("button", { class: "primary small", title: "Start working on this ticket with Supdev", onclick: (e) => { e.stopPropagation(); startTicket(x); } }, "Start"));
  else if (wi) extra.push(h("button", { class: "subtle small", title: "Open this ticket's chat", onclick: (e) => { e.stopPropagation(); openCard(x); } }, "Continue"));
  if (x.issue && jira.can_move && useJira()) extra.push(h("button", { class: "subtle small movebtn", title: "Move to another column", "aria-label": `Move ${x.key}`, onclick: (e) => { e.stopPropagation(); openMoveMenu(x, cols, e.currentTarget); } }, "⋯"));
  const who = x.assignee ? avatar(x.assignee) : x.issue ? h("span", { class: "avatar sm unassigned", title: "Unassigned" }, "?") : avatar($("user")?.value || "me");
  const isSel = st.ctx && ((x.key && x.key === st.ctx.key) || (wi && wi.session_id === st.ctx.sid) || (x.chat && x.chat.session_id === st.ctx.sid));
  const el = h("div", { class: "tile" + (canDrag ? " draggable" : "") + (isSel ? " sel" : ""), role: "button", tabindex: "0", "data-key": x.key || "", title: "Open chat and details" },
    h("div", { class: "row" }, x.type ? loz(x.type, colorFor(x.type)) : null, keyEl),
    h("span", { class: "t" }, x.title),
    h("div", { class: "f" }, h("div", { class: "l" }, ...meta, ...extra), who));
  el.addEventListener("click", (e) => { if (e.target.closest("a,button")) return; openCard(x); });
  el.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === el) el.click(); });
  if (canDrag) {
    el.draggable = true;
    el.addEventListener("dragstart", (e) => { st.drag = x; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", x.key); el.classList.add("dragging"); });
    el.addEventListener("dragend", () => { st.drag = null; el.classList.remove("dragging"); document.querySelectorAll(".col.over").forEach((c) => c.classList.remove("over")); });
  }
  return el;
}
function renderCol(col, cols) {
  const el = h("div", { class: "col " + (col.cls || ""), "data-col": col.id }, h("h4", {}, col.title, h("span", { class: "count" }, col.items.length)),
    col.hint ? h("div", { class: "note colhint" }, col.hint) : null,
    h("div", { class: "colbody" }, col.items.length ? col.items.map((x) => tile(x, col, cols)) : h("div", { class: "empty-col" }, col.droppable ? "Drop here" : "—")));
  if (col.droppable) {
    el.addEventListener("dragover", (e) => { if (!st.drag) return; e.preventDefault(); e.dataTransfer.dropEffect = "move"; el.classList.add("over"); });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("over"); });
    el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("over"); if (st.drag) moveIssue(st.drag, col); });
  }
  return el;
}
function drawColumns() {
  const box = $("boardcols"); if (!box || !boardData) return;
  const items = buildItems().filter(keepItem), cols = useJira() ? jiraColumns(items) : phaseColumns(items);
  box.replaceChildren(...cols.map((c) => renderCol(c, cols)));
  const sel = rel.list.find((r) => r.id === rel.selected), s = $("boardstatus"); if (!s) return;
  const shown = items.length;
  const parts = [];
  if (rel.keys && sel) parts.push(h("span", {}, `Release `, h("b", {}, sel.name), ` · ${shown} item${shown === 1 ? "" : "s"}`,
    rel.viaEpics && rel.viaEpics.length ? ` (includes work under epic ${rel.viaEpics.join(", ")})` : "",
    shown ? "" : " — no matching tickets. Set “Fix versions” on tickets (or on their epic) to this release in Jira.", " · ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); selectRelease(""); } }, "Clear filter")));
  else if (jira.configured === false) parts.push("Connect Jira in Settings to see your tasks, stories and bugs. Showing Supdev work items by phase.");
  else if (useJira()) parts.push(`${shown} item${shown === 1 ? "" : "s"} from Jira project ${jira.project} · drag a card to change its Jira status${jira.can_move ? "" : " (your role can't move cards)"}`);
  else parts.push("Columns are Supdev phases — a card moves when the agent completes a phase; approvals happen in the card's chat.");
  s.replaceChildren(...parts);
  document.querySelectorAll(".rel").forEach((n) => n.classList.toggle("sel", n.dataset.id === rel.selected));
  const g = $("groupseg"); if (g) { g.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.group === (useJira() ? "jira" : "phase"))); g.querySelector('[data-group="jira"]').disabled = !(jira.configured && jira.columns.length); }
}

// ---- move a ticket (Jira workflow transition)
async function moveIssue(x, col) {
  const iss = x.issue; if (!iss || !col.status_ids || col.status_ids.includes(iss.status_id)) return;
  const prev = { status: iss.status, status_id: iss.status_id };
  iss.status_id = col.status_ids[0]; iss.status = col.title; drawColumns();              // optimistic
  try {
    const r = await api(`/issues/${encodeURIComponent(iss.key)}/move`, { method: "POST", body: JSON.stringify({ status_ids: col.status_ids }) });
    toast(`${iss.key} moved to ${r.status} in Jira`); await refreshJira(true);
  } catch (e) { Object.assign(iss, prev); drawColumns(); toast(e.message, true); }      // Jira said no: put the card back
}
function openMoveMenu(x, cols, btn) {
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const targets = cols.filter((c) => c.droppable && !c.status_ids.includes(x.issue.status_id));
  const menu = h("div", { class: "menu", role: "menu" }, h("div", { class: "menuh" }, `Move ${x.key} to`), ...targets.map((c) => h("button", { class: "subtle", role: "menuitem", onclick: () => { menu.remove(); moveIssue(x, c); } }, c.title)));
  const r = btn.getBoundingClientRect(); menu.style.top = r.bottom + window.scrollY + 4 + "px"; menu.style.left = Math.max(8, r.left + window.scrollX - 120) + "px";
  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", function close(e) { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); } }), 0);
  menu.querySelector("button")?.focus();
}

// ---- sync with Jira (poll while the board is open; also on demand)
async function refreshJira(force) {
  try { const r = await api("/issues" + (force ? "?refresh=true" : "")); jira = { ...jira, ...r, error: null, syncedAt: Date.now() }; }
  catch (e) { jira.error = e.message; if (jira.configured === null) jira.configured = false; }
  if (st.ctx && st.ctx.key && (jira.epics || []).some((k) => k.toUpperCase() === st.ctx.key.toUpperCase())) closePanel();
  fillTypeChips(); drawColumns(); updateSync();
  if (st.ctx) { drawPanelHeader(st.v); drawPanelDetails(st.v); }   // keep the open ticket's Jira status fresh
}
function updateSync() { const el = $("syncinfo"); if (!el) return; el.textContent = jira.error ? "Sync failed: " + jira.error : jira.syncedAt ? "Synced " + ago(jira.syncedAt / 1000) : ""; el.className = jira.error ? "syncinfo bad" : "syncinfo"; }
function startSync() { stopSync(); syncTick = 0; syncTimer = setInterval(() => { syncTick++; updateSync(); if (syncTick % 3 === 0 && !document.hidden) refreshJira(false); }, 15000); }
function stopSync() { if (syncTimer) clearInterval(syncTimer); syncTimer = null; }
const presentTypes = () => [...new Set(jira.issues.map((i) => i.type.toLowerCase()))];
const activeTypes = () => rel.types || new Set((boardData && boardData.types ? boardData.types.map((t) => t.toLowerCase()) : presentTypes()));
function fillTypeChips() {
  const box = $("typechips"); if (!box) return;
  const counts = new Map(); jira.issues.forEach((i) => counts.set(i.type.toLowerCase(), (counts.get(i.type.toLowerCase()) || 0) + 1));
  const names = new Map(); jira.issues.forEach((i) => names.set(i.type.toLowerCase(), i.type));
  (boardData && boardData.types ? boardData.types : []).forEach((t) => { if (!names.has(t.toLowerCase())) names.set(t.toLowerCase(), t); });   // configured but no tickets yet
  const on = activeTypes();
  box.replaceChildren(...[...names.keys()].sort().map((t) => {
    const n = counts.get(t) || 0;
    return h("button", { class: "tchip" + (on.has(t) ? " on" : "") + (n ? "" : " none"), "aria-pressed": on.has(t), disabled: !jira.configured,
      title: n ? `${n} ${names.get(t)} ticket${n === 1 ? "" : "s"} in Jira` : `No ${names.get(t)} tickets in this Jira project`,
      onclick: () => { const next = new Set(activeTypes()); if (next.has(t)) next.delete(t); else next.add(t); rel.types = next; fillTypeChips(); drawColumns(); } },
      names.get(t), h("span", { class: "n" }, n));
  }));
}

async function selectRelease(id) {
  rel.selected = id; rel.keys = null; rel.truncated = false; rel.viaEpics = [];
  if ($("relsel")) $("relsel").value = id;
  if (!id) { drawColumns(); return; }
  try {
    const r = await api(`/releases/${encodeURIComponent(id)}/issues`);
    if (rel.selected !== id) return; // user changed their mind while loading
    rel.keys = new Set(r.keys.map((k) => k.toUpperCase())); rel.truncated = r.truncated; rel.viaEpics = r.via_epics || [];
  } catch (e) { toast("Couldn't load that release's tickets: " + e.message, true); rel.selected = ""; if ($("relsel")) $("relsel").value = ""; }
  drawColumns();
}
function fillReleaseSelect() {
  const sel = $("relsel"); if (!sel) return;
  const opt = (r) => h("option", { value: r.id }, r.name + (r.overdue ? " (overdue)" : ""));
  sel.replaceChildren(h("option", { value: "" }, "All releases"), ...rel.list.map(opt));   // plain list, no group headings (status lives in the side panel)
  sel.disabled = !rel.configured || !rel.list.length;
  sel.title = rel.configured ? (rel.list.length ? "Show only work items whose Jira ticket is in this release" : "No releases found") : "Connect Jira in Settings to filter by release";
  if (rel.selected && !rel.list.some((r) => r.id === rel.selected)) { rel.selected = ""; }
  sel.value = rel.selected;
}

async function showBoard() {
  const c = $("content"); c.replaceChildren(h("div", { class: "page" }, h("div", { class: "note" }, "Loading board…")));
  try { boardData = await api("/board?mode=" + st.mode); } catch (e) { c.replaceChildren(h("div", { class: "page" }, h("div", { class: "toast err" }, e.message))); return; }
  const relsel = h("select", { id: "relsel", "aria-label": "Release filter", disabled: true, onchange: (e) => selectRelease(e.target.value) }, h("option", { value: "" }, "All releases"));
  const seg = h("div", { class: "seg", id: "groupseg", role: "group", "aria-label": "Group columns by" },
    h("button", { "data-group": "jira", onclick: () => { rel.group = "jira"; localStorage.setItem("sd_group", "jira"); drawColumns(); } }, "Jira status"),
    h("button", { "data-group": "phase", onclick: () => { rel.group = "phase"; localStorage.setItem("sd_group", "phase"); drawColumns(); } }, "Supdev phase"));
  const rail = h("aside", { class: "rail", "aria-label": "Jira releases" });
  c.replaceChildren(h("div", { class: "page wide" },
    h("div", { class: "crumbs" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); show("board"); } }, "Boards"), " / ", modeLabel(st.mode)),
    h("h1", {}, modeLabel(st.mode) + " board"),
    h("div", { class: "toolbar" }, h("label", { class: "filter", for: "relsel" }, h("span", {}, "Release"), relsel), h("div", { class: "filter" }, h("span", {}, "Types"), h("div", { class: "tchips", id: "typechips", role: "group", "aria-label": "Issue types to show" })),
      h("label", { class: "filter" }, h("span", {}, "Group by"), seg),
      h("button", { class: "subtle", title: "Sync with Jira now", onclick: () => refreshJira(true) }, "↻ Sync"), h("span", { id: "syncinfo", class: "syncinfo" })),
    h("div", { id: "boardstatus", class: "note", style: "margin:2px 0 0" }),
    h("div", { class: "boardlayout" }, h("div", { class: "board", id: "boardcols" }), rail)));
  rel.keys = null; rel.types = null; fillTypeChips(); drawColumns();
  loadReleases(rail, false); refreshJira(false); startSync();
}

const fmtDate = (d) => (d ? new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");
async function loadReleases(rail, refresh) {
  const head = (project) => h("div", { class: "rh" }, h("h3", {}, "Releases"), project ? loz(project, "blue") : null,
    h("button", { class: "subtle", title: "Refresh from Jira", "aria-label": "Refresh releases", onclick: () => loadReleases(rail, true) }, "↻"));
  rail.replaceChildren(head(), h("div", { class: "msg" }, "Loading releases from Jira…"));
  let r;
  try { r = await api("/releases" + (refresh ? "?refresh=true" : "")); }
  catch (e) { rel.list = []; rel.configured = false; fillReleaseSelect(); rail.replaceChildren(head(), h("div", { class: "msg" }, "Couldn't load releases: " + e.message)); return; }
  rel.configured = r.configured && !!r.project; rel.list = r.releases || []; rel.project = r.project;
  fillReleaseSelect();
  if (!r.configured) { rail.replaceChildren(head(), h("div", { class: "msg" }, "Connect Jira to see releases here. ", h("a", { href: "/settings" }, "Settings → Integrations"))); return; }
  if (!r.project) { rail.replaceChildren(head(), h("div", { class: "msg" }, "Add a default project key to your Jira integration to list its releases. ", h("a", { href: "/settings" }, "Settings → Integrations"))); return; }
  const item = (v) => {
    const status = v.released ? loz("released", "green") : v.overdue ? loz("overdue", "red") : loz("unreleased", "blue");
    const pct = v.total ? Math.round((v.done / v.total) * 100) : null;
    const box = h("div", { class: "rel", "data-id": v.id },
      h("div", { class: "n" }, h("a", { href: v.url, target: "_blank", rel: "noopener noreferrer", title: v.name + " — open in Jira" }, v.name), status),
      (v.release_date || v.start_date) ? h("div", { class: "d" }, v.released ? "Released " + fmtDate(v.release_date) : v.release_date ? "Due " + fmtDate(v.release_date) : "Starts " + fmtDate(v.start_date)) : null,
      v.description ? h("div", { class: "desc" }, v.description) : null,
      pct !== null ? h("div", { class: "pg" }, h("div", { class: "bar", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": "0", "aria-valuemax": "100" }, h("i", { class: pct === 100 ? "done" : "", style: `width:${pct}%` })), `${v.done} of ${v.total} done`) : null,
      h("button", { class: "subtle relfilter", onclick: () => selectRelease(rel.selected === v.id ? "" : v.id) }, "Filter board"));
    return box;
  };
  const up = r.releases.filter((v) => !v.released), done = r.releases.filter((v) => v.released);
  rail.replaceChildren(...[head(r.project),
    up.length ? h("h4", {}, "Unreleased") : null, ...up.map(item),
    done.length ? h("h4", {}, "Recently released") : null, ...done.map(item),
    !r.releases.length ? h("div", { class: "msg" }, `No releases in ${r.project} yet. Create one in Jira (Project → Releases), then press ↻. `, h("a", { href: r.manage_url, target: "_blank", rel: "noopener noreferrer" }, "Open Jira releases")) : null].filter(Boolean));
  if (rel.selected) await selectRelease(rel.selected); else drawColumns();
}

// ------------------------------------------------------------------------------------------ ticket chat (left panel)
// Each ticket / work item has its OWN chat session and its own details; the panel shows one at a time next to the board.
const panelEl = () => $("chatpanel");
const issueOf = (key) => (key ? jira.issues.find((i) => i.key === key) || null : null);

function closePanel() {
  st.ctx = null; st.v = null; st.feed = null; st.typing = null; setState("sid", null);
  panelEl().hidden = true; panelEl().replaceChildren();
  document.querySelectorAll(".tile.sel").forEach((t) => t.classList.remove("sel"));
}
// Open a card in the panel. Started tickets open their chat; not-started ones open details with a Start button.
async function openCard(x, sidOverride) {
  const wi = x.wi, sid = sidOverride || (wi ? wi.session_id : x.chat ? x.chat.session_id : null);
  try { if (wi && wi.status === "parked") await api(`/sessions/${sid}/resume/${wi.work_item_id}`, { method: "POST" }); } catch (e) { toast(e.message, true); return; }
  await openPanel({ key: x.key || null, title: x.title, type: x.type }, sid);
}
async function openPanel(ctx, sid) {
  st.ctx = { ...ctx, sid: sid || null }; setState("sid", sid || null);
  panelEl().hidden = false; buildPanel();
  document.querySelectorAll(".tile.sel").forEach((t) => t.classList.remove("sel"));
  document.querySelectorAll(".tile").forEach((t) => { if (ctx.key && t.dataset.key === ctx.key) t.classList.add("sel"); });
  if (sid) await loadPanel(true); else drawPanelHeader(null), drawPanelDetails(null), drawApprovals(null);
}

function buildPanel() {
  const sid = st.ctx.sid;
  st.feed = h("div", { class: "feed", "aria-live": "polite" });
  const input = h("textarea", { id: "input", rows: "2", placeholder: "Message about this ticket… (Enter to send, Shift+Enter for a new line; type “approved” at a gate)" });
  input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } };
  panelEl().replaceChildren(
    h("div", { class: "phead" }, h("div", { id: "phdr" })),
    h("div", { class: "pbody", id: "pbody" },
      // ticket details sit above the conversation: open until work starts, then out of the way
      h("details", { class: "pdetails", id: "pdet", open: !sid }, h("summary", {}, "Ticket details"), h("div", { id: "pdetails", class: "details" })),
      st.feed, h("div", { id: "approvals", class: "actionpanel" })),
    h("form", { class: "composer", id: "pcomposer", hidden: !sid, onsubmit: (e) => { e.preventDefault(); send(input); } }, avatar($("user")?.value || "me", "sm"),
      h("div", { class: "box" }, input, h("div", { class: "actions" }, h("button", { class: "primary", id: "send", type: "submit" }, "Send"), h("span", { class: "hint" }, "Only a human can approve gates.")))));
  if (!sid) st.feed.append(h("div", { class: "pempty" }, h("b", {}, "Not started yet"), h("div", {}, "Press Start and Supdev will read the ticket, ask what it needs to know, and plan before any code is written."), startBtn()));
}
function startBtn() { return h("button", { class: "primary", onclick: () => startTicket(st.ctx) }, "Start"); }

async function loadPanel(full) {
  if (!st.ctx || !st.ctx.sid) return null;
  let v; try { v = await api("/sessions/" + st.ctx.sid); } catch (e) { toast(e.message, true); closePanel(); return null; }
  st.v = v;
  if (full) {
    st.feed.replaceChildren(); v.messages.forEach((m) => addPost(m.role, m.text, m.ts));
    if (!v.messages.length) st.feed.append(h("div", { class: "event ph" }, h("span", { class: "dot" }), "No messages yet — say what you need below."));
    $("pbody").scrollTop = 1e9;
  }
  drawPanelHeader(v); drawApprovals(v); drawPanelDetails(v);
  return v;
}

const TAG = /^\s*\[([A-Z][A-Z0-9_]*)([^\]]*)\]\s*/;   // any mode tag, incl. plugin modes
function addPost(role, text, ts) {
  if (role !== "user" && role !== "assistant") return;
  let tag = null, body = text; const m = TAG.exec(text);
  if (m && role === "assistant") { tag = (m[1] + m[2]).replace(/\s*·\s*/g, " · ").trim(); body = text.slice(m[0].length); }
  const me = $("user")?.value || "me";
  st.feed.querySelectorAll(".event.ph").forEach((e) => e.remove());   // drop the "no messages yet" hint
  st.feed.append(h("div", { class: "post " + role }, role === "user" ? avatar(me) : botAvatar(),
    h("div", { class: "body" }, h("div", { class: "hd" }, h("span", { class: "name" }, role === "user" ? "You" : "Supdev"), ts ? h("span", { class: "ts" }, ago(ts)) : null, tag ? loz(tag, "blue") : null),
      h("div", { class: "txt" }, body))));
  const pb = $("pbody"); if (pb) pb.scrollTop = pb.scrollHeight;
}
function addEvent(text, cls) { if (!st.feed) return; st.feed.append(h("div", { class: "event " + (cls || "") }, h("span", { class: "dot" }), text)); const pb = $("pbody"); if (pb) pb.scrollTop = pb.scrollHeight; }
function setTyping(on) {
  if (st.typing) { st.typing.remove(); st.typing = null; }
  if (on && st.feed) { st.typing = h("div", { class: "typing" }, h("i"), h("i"), h("i"), h("span", {}, "Supdev is working…")); st.feed.append(st.typing); }
}

function drawPanelHeader(v) {
  const box = $("phdr"); if (!box || !st.ctx) return;
  const c = st.ctx, iss = issueOf(c.key), a = v && v.active, pend = a ? a.pending_approvals.length : 0;
  const type = iss ? iss.type : c.type;
  box.replaceChildren(...[
    h("div", { class: "toprow" }, type ? loz(type, colorFor(type)) : null,
      c.key ? (iss ? h("a", { class: "key", href: iss.url, target: "_blank", rel: "noopener noreferrer", title: "Open in Jira" }, c.key) : h("span", { class: "key" }, c.key)) : h("span", { class: "key" }, shortId(c.sid)),
      h("span", { class: "sp" }), !c.sid ? startBtn() : null,
      h("button", { class: "subtle", title: "Close", "aria-label": "Close chat panel", onclick: closePanel }, "✕")),
    h("h2", {}, (a && a.title) || c.title || "New chat"),
    h("div", { class: "row" }, iss ? loz(iss.status, CAT_COLOR[iss.status_category] || "gray") : null,
      a ? loz(modeLabel(a.mode), colorFor(a.mode)) : null, a ? loz(`Phase ${a.phase}/${a.phases.length} · ${a.phases[a.phase - 1]}`, "purple") : null, pend ? loz(pend + " needs approval", "yellow") : null,
      v && v.awaiting_router ? loz("reply “dev” or “support”", "yellow") : null),
    a ? h("div", { class: "workflow", role: "list" }, a.phases.map((t, i) => h("div", { class: "wf " + (i + 1 < a.phase ? "done" : i + 1 === a.phase ? "cur" : ""), role: "listitem", title: t }, String(i + 1)))) : null].filter(Boolean));
}

function drawApprovals(v) {
  const box = $("approvals"); if (!box) return; box.replaceChildren();
  ((v && v.active && v.active.pending_approvals) || []).forEach((p) => {
    const btn = h("button", { class: "primary", disabled: !p.can_approve || st.busy, title: p.can_approve ? "" : "Your role cannot approve this action", onclick: async () => {
      btn.disabled = true;
      try { await api(`/sessions/${st.ctx.sid}/approvals/${p.id}/grant`, { method: "POST", body: JSON.stringify({ seen_hash: p.hash }) }); addEvent(`You approved “${p.kind}”`); await run(`/sessions/${st.ctx.sid}/continue`, {}); }
      catch (e) { toast(e.message, true); btn.disabled = false; }
    } }, "Approve exactly this");
    box.append(h("div", { class: "card" }, h("h3", {}, "Action required", loz(p.kind.replace(/_/g, " "), "yellow")),
      h("div", { class: "note" }, "Review the exact artifact below. Approval is void if it changes."),
      h("pre", {}, typeof p.content === "string" ? p.content : JSON.stringify(p.content, null, 2)),
      h("div", { class: "row" }, btn, h("span", { class: "h" }, `hash ${p.hash.slice(0, 12)} · can approve: ${p.roles.join(", ") || "nobody"}`))));
  });
}

function drawPanelDetails(v) {
  const box = $("pdetails"); if (!box || !st.ctx) return;
  const a = v && v.active, iss = issueOf(st.ctx.key);
  const sec = (title, ...kids) => h("section", {}, h("h3", {}, title), ...kids);
  const kv = (k, val) => h("div", { class: "kv" }, h("span", { class: "k" }, k), h("span", {}, val));
  const list = (items, empty) => h("ul", { class: "list" }, items.length ? items : h("li", { class: "none" }, empty));
  const out = [];
  if (iss) out.push(sec("Jira ticket", kv("Type", loz(iss.type, colorFor(iss.type))), kv("Status", loz(iss.status, CAT_COLOR[iss.status_category] || "gray")),
    kv("Priority", iss.priority || "—"), kv("Assignee", iss.assignee || "Unassigned"), kv("Release", (iss.fix_versions || []).join(", ") || "—"),
    kv("Link", h("a", { href: iss.url, target: "_blank", rel: "noopener noreferrer" }, "Open in Jira"))));
  else if (st.ctx.key) out.push(sec("Ticket", kv("Key", st.ctx.key), kv("Note", "Not in the current Jira view")));
  out.push(sec("Supdev", kv("Mode", a ? loz(modeLabel(a.mode), colorFor(a.mode)) : "—"), kv("Phase", a ? `${a.phase} / ${a.phases.length} · ${a.phases[a.phase - 1]}` : "Not started"), kv("Session", v ? shortId(v.id) : "—"),
    kv("Budget", v ? `${v.budget.tool_calls.used} calls · ${v.budget.query_units.used} queries · ${v.budget.tokens.used} tokens` : "—")));
  if (a && a.mode === "dev") out.push(sec("Requirements", list((a.requirements || []).map((r) => h("li", {}, loz(r.status, r.status === "clear" ? "green" : r.status === "assumed" ? "yellow" : "red"), h("span", {}, `${r.id}: ${r.text}`))), "None recorded yet")));
  if (a && a.mode === "support") out.push(sec("Evidence", list((a.evidence || []).map((x) => h("li", {}, loz(x.id, "blue"), h("span", {}, `${x.source_tool} — ${x.finding}`))), "No evidence yet")));
  if (v && v.parked.length) out.push(sec("Parked work items", h("ul", { class: "list" }, v.parked.map((w) => h("li", {}, loz((modeLabel(w.mode) || w.mode), colorFor(w.mode)), h("span", { style: "flex:1" }, w.title || w.id),
    w.status === "parked" ? h("button", { class: "subtle", onclick: async () => { try { await api(`/sessions/${st.ctx.sid}/resume/${w.id}`, { method: "POST" }); await loadPanel(true); refreshBoard(); } catch (e) { toast(e.message, true); } } }, "Resume") : loz(w.status.replace("_", " "), "purple"))))));
  if (!st.ctx.sid) out.push(h("div", { class: "actions" }, startBtn()));
  box.replaceChildren(...out);
}

// Start = create this ticket's own work item + chat, then let the agent begin (intake and clarification).
async function startTicket(c) {
  if (st.starting) return; st.starting = true;
  const key = c.key || null, title = c.title || key || "New work item";
  try {
    const s = await api("/sessions", { method: "POST" });
    await api(`/sessions/${s.id}/mode`, { method: "POST", body: JSON.stringify({ mode: st.mode, ref: key, title }) });
    await refreshBoard();
    await openPanel({ key, title, type: c.type }, s.id);
    const text = key ? `Start work on ${key}: ${title}` : `Start work: ${title}`;
    addPost("user", text, Date.now() / 1000);
    await run(`/sessions/${s.id}/messages`, { text, ref: key, ticket_type: (c.type || "").toLowerCase() || null });
  } catch (e) { toast(e.message, true); } finally { st.starting = false; }
}

// ------------------------------------------------------------------------------------------ streaming
async function send(input) {
  const text = input.value.trim(); if (!text || st.busy || !st.ctx || !st.ctx.sid) return;
  input.value = ""; addPost("user", text, Date.now() / 1000);
  await run(`/sessions/${st.ctx.sid}/messages`, { text });
}
async function run(path, body) {
  st.busy = true; const sendBtn = $("send"); if (sendBtn) sendBtn.disabled = true; setTyping(true);
  try {
    const r = await fetch("/api" + path, { method: "POST", headers: identHeaders(), body: JSON.stringify(body) });
    if (!r.ok) throw new Error(await r.text());
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.search(/\r?\n\r?\n/)) >= 0) { const raw = buf.slice(0, i); buf = buf.slice(i).replace(/^\r?\n\r?\n/, ""); handle(raw); }
    }
  } catch (e) { addEvent(e.message, "err"); }
  finally { st.busy = false; setTyping(false); const b = $("send"); if (b) b.disabled = false; await loadPanel(false); refreshBoard(); }
}
function handle(raw) {
  let ev = "message", data = "";
  raw.split(/\r?\n/).forEach((l) => { if (l.startsWith("event:")) ev = l.slice(6).trim(); else if (l.startsWith("data:")) data += l.slice(5).trim(); });
  let d = {}; try { d = JSON.parse(data); } catch (e) {}
  setTyping(false);
  if (ev === "assistant") addPost("assistant", d.text, Date.now() / 1000);
  else if (ev === "audit_notice") addEvent("📝 " + d.line);
  else if (ev === "flag") addEvent("⚠ " + (d.kind || "flag").replace(/_/g, " ") + (d.patterns ? ": " + d.patterns.join(", ") : ""), "flag");
  else if (ev === "tool_result" && d.denied) addEvent(`Blocked ${d.name}: ${d.denied}`, "err");
  else if (ev === "budget_paused") addEvent("Budget reached: " + d.kind + " — a lead/admin must extend it", "flag");
  else if (ev === "stuck") addEvent("Stopped after the same failure 3 times", "flag");
  else if (ev === "phase") addEvent(`Moved to phase ${d.phase}: ${d.title}`);
  else if (ev === "mode") addEvent(`Started ${(modeLabel(d.mode) || d.mode)} work item`);
  else if (ev === "jira_status") { addEvent(d.changed ? `Jira ${d.key}: ${d.from} → ${d.to}` : `Jira ${d.key} stays “${d.from}” (${d.reason})`); if (d.changed) refreshJira(true); }
  else if (ev === "jira_sync_failed") addEvent(`Couldn't update Jira ${d.key}: ${d.reason}`, "flag");
  else if (ev === "jira_sync_skipped") addEvent(`Jira ${d.key} not updated: ${d.reason}`, "flag");
  else if (ev === "error") addEvent(d.message, "err");
  if (["tool_call", "assistant", "audit_notice"].includes(ev)) setTyping(true);
}

// ------------------------------------------------------------------------------------------ create
function openCreate(preset, prefill) {
  const type = h("select", { id: "c-type" }, h("option", { value: "dev" }, "Development — implement a ticket / story / design"),
    h("option", { value: "support" }, "Support — investigate a production issue"), h("option", { value: "auto" }, "Let Supdev decide"));
  type.value = preset || st.mode;
  const key = h("input", { placeholder: "e.g. PROJ-123 (optional)" });
  const tt = h("select", {}, h("option", { value: "" }, "—"), ...["story", "task", "bug", "incident"].map((x) => h("option", { value: x }, x)));
  const summary = h("textarea", { rows: "4", placeholder: "Describe the work or the incident. Links to tickets and designs are fine." });
  const err = h("div", { class: "result err" });
  const close = () => back.remove();
  const go = h("button", { class: "primary", onclick: async () => {
    if (type.value === "auto" && !summary.value.trim()) { err.textContent = "Add a description so Supdev can pick the mode."; return; }
    go.disabled = true;
    try {
      const s = await api("/sessions", { method: "POST" });
      const title = summary.value.trim().split("\n")[0].slice(0, 80) || key.value.trim();
      if (type.value !== "auto") { await api(`/sessions/${s.id}/mode`, { method: "POST", body: JSON.stringify({ mode: type.value, ref: key.value.trim() || null, title }) }); setState("mode", type.value); }
      close(); await refreshBoard(); await openPanel({ key: key.value.trim() || null, title, type: tt.value }, s.id);
      if (summary.value.trim()) { addPost("user", summary.value.trim(), Date.now() / 1000); await run(`/sessions/${s.id}/messages`, { text: summary.value.trim(), ref: key.value.trim() || null, ticket_type: tt.value || null }); }
    } catch (e) { err.textContent = e.message; go.disabled = false; }
  } }, "Create");
  const f = (label, el) => h("div", { class: "field" }, h("label", {}, label), el);
  const back = h("div", { class: "modalback", onclick: (e) => { if (e.target === back) close(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": "Create work item" },
      h("div", { class: "mh" }, h("h2", {}, "Create work item")),
      h("div", { class: "mb" }, f("Work type", type), h("div", { class: "grid2" }, f("Ticket key", key), f("Ticket type", tt)), f("Summary", summary), err),
      h("div", { class: "mf" }, h("button", { class: "subtle", onclick: close }, "Cancel"), go)));
  if (prefill) { key.value = prefill.key || ""; summary.value = `${prefill.key}: ${prefill.summary}`; const t = (prefill.type || "").toLowerCase(); tt.value = [...tt.options].some((o) => o.value === t) ? t : ""; }
  document.body.append(back); summary.focus();
  back.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}

// ------------------------------------------------------------------------------------------ boot
$("nav-board").onclick = () => show();
$("create").onclick = () => openCreate();
async function loadModes() {
  try { st.modes = await api("/modes"); } catch (e) { st.modes = []; toast(e.message, true); }
  if (st.modes.length && !st.modes.some((m) => m.name === st.mode)) setState("mode", st.modes[0].name);
}
initAuthUI(() => { closePanel(); loadModes().then(show); }).then(async () => {
  await loadModes(); await show();
  if (st.sid && boardData) {  // reopen the ticket chat you had open
    const c = boardData.cards.find((k) => k.session_id === st.sid), ch = boardData.backlog.find((k) => k.session_id === st.sid);
    if (c) openCard({ key: c.ref, title: c.title, wi: c, type: "" }); else if (ch) openCard({ key: null, title: ch.title, chat: ch, type: "" }); else setState("sid", null);
  }
});
