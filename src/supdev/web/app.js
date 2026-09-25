// Supdev chat + kanban UI. All dynamic text goes through the DOM builder `h` (textContent) — no innerHTML.
const $ = _$;
const st = {
  view: localStorage.getItem("sd_view") || "board",
  mode: localStorage.getItem("sd_mode") || "dev",
  sid: localStorage.getItem("sd_sid") || null,
  busy: false, sessions: [], feed: null, typing: null,
};
const MODE_NAME = { dev: "Development", support: "Support" };
const MODE_COLOR = { dev: "blue", support: "orange" };
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
  $("nav-board").classList.toggle("active", st.view === "board");
  $("nav-chat").classList.toggle("active", st.view === "chat");
  document.querySelectorAll("#modeseg button").forEach((b) => { const on = b.dataset.mode === st.mode; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
}
async function show(view) {
  setState("view", view); renderNav(); renderSidebar();
  document.querySelector(".app").classList.toggle("nosidebar", view === "board"); // work-item list belongs to Chat only
  return view === "board" ? showBoard() : showChat();
}

async function loadSessions() {
  try { st.sessions = await api("/sessions"); } catch (e) { st.sessions = []; toast(e.message, true); }
  renderSidebar();
}
function renderSidebar() {
  const q = ($("search").value || "").toLowerCase();
  $("listtitle").textContent = MODE_NAME[st.mode] + " work items";
  const items = st.sessions.filter((s) => (s.mode === st.mode || s.mode === null) && (!q || s.title.toLowerCase().includes(q) || (s.ref || "").toLowerCase().includes(q)));
  const box = $("wilist"); box.replaceChildren();
  if (!items.length) box.append(h("div", { class: "note", style: "padding:8px;color:var(--sub)" }, "Nothing here yet. Use Create to start."));
  items.forEach((s) => {
    box.append(h("button", { class: "wi" + (s.id === st.sid ? " on" : ""), onclick: () => openSession(s.id) },
      h("span", { class: "t" }, s.title),
      h("span", { class: "m" }, s.mode ? loz(MODE_NAME[s.mode], MODE_COLOR[s.mode]) : loz("not started"),
        s.phase ? loz(`${s.phase}/${s.phase_total} ${s.phase_title}`, "gray") : null, s.pending ? loz("needs approval", "yellow") : null,
        h("span", { class: "k" }, s.ref || shortId(s.id)))));
  });
}

async function openSession(sid, resumeWi) {
  try {
    if (resumeWi) await api(`/sessions/${sid}/resume/${resumeWi}`, { method: "POST" });
    setState("sid", sid); await show("chat"); loadSessions();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------------------------------ board
const rel = { list: [], project: null, configured: null, selected: localStorage.getItem("sd_release") || "", keys: null, loading: false };
let boardData = null;

function tile(card) {
  const meta = h("div", { class: "l" }, h("span", { class: "key" }, card.ref || shortId(card.work_item_id || card.session_id)),
    card.pending ? loz("approval", "yellow") : null, card.status === "parked" ? loz("parked", "gray") : null, card.status === "handed_off" ? loz("handed off", "purple") : null);
  return h("div", { class: "tile", role: "button", tabindex: "0", title: "Open chat", onclick: () => openSession(card.session_id, card.status === "parked" ? card.work_item_id : null),
    onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); } },
    h("span", { class: "t" }, card.title), h("div", { class: "f" }, meta, avatar($("user")?.value || "me")));
}
const col = (title, cards, cls, emptyText) => h("div", { class: "col " + (cls || "") }, h("h4", {}, title, h("span", { class: "count" }, cards.length)),
  cards.length ? cards.map(tile) : h("div", { class: "empty-col" }, emptyText || "—"));

// Cards are kept when no release is selected, or when their Jira ticket key is in the selected release.
const keepCard = (c) => !rel.keys || (c.ref && rel.keys.has(c.ref.toUpperCase()));
function drawColumns() {
  const b = boardData, el = $("boardcols"); if (!b || !el) return;
  const filtering = !!rel.keys;
  const cards = b.cards.filter(keepCard), backlog = filtering ? [] : b.backlog.map((x) => ({ ...x, work_item_id: null, ref: null, status: "new", pending: 0 }));
  el.replaceChildren(col("Backlog", backlog, "backlog", filtering ? "Hidden while filtering" : "No unrouted chats"),
    ...b.phases.map((t, i) => col(`${i + 1}. ${t}`, cards.filter((x) => x.phase === i + 1), "")));
  const sel = rel.list.find((r) => r.id === rel.selected), total = b.cards.length;
  const st_ = $("boardstatus"); if (!st_) return;
  st_.replaceChildren(filtering && sel
    ? h("span", {}, `Showing ${cards.length} of ${total} work item${total === 1 ? "" : "s"} in release `, h("b", {}, sel.name), rel.truncated ? " (first 100 tickets)" : "", " · ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); selectRelease(""); } }, "Clear filter"))
    : `${total} work item${total === 1 ? "" : "s"} · a card moves to the next column when the agent completes that phase — approvals happen in the card's chat.`);
  document.querySelectorAll(".rel").forEach((n) => n.classList.toggle("sel", n.dataset.id === rel.selected));
}

async function selectRelease(id) {
  rel.selected = id; localStorage.setItem("sd_release", id); rel.keys = null; rel.truncated = false;
  if ($("relsel")) $("relsel").value = id;
  if (!id) { drawColumns(); return; }
  try {
    const r = await api(`/releases/${encodeURIComponent(id)}/issues`);
    if (rel.selected !== id) return; // user changed their mind while loading
    rel.keys = new Set(r.keys.map((k) => k.toUpperCase())); rel.truncated = r.truncated;
  } catch (e) { toast("Couldn't load that release's tickets: " + e.message, true); rel.selected = ""; localStorage.setItem("sd_release", ""); if ($("relsel")) $("relsel").value = ""; }
  drawColumns();
}
function fillReleaseSelect() {
  const sel = $("relsel"); if (!sel) return;
  const opt = (r) => h("option", { value: r.id }, r.name + (r.overdue ? " (overdue)" : ""));
  const up = rel.list.filter((r) => !r.released), done = rel.list.filter((r) => r.released);
  sel.replaceChildren(h("option", { value: "" }, "All releases"),
    ...(up.length ? [h("optgroup", { label: "Unreleased" }, up.map(opt))] : []), ...(done.length ? [h("optgroup", { label: "Released" }, done.map(opt))] : []));
  sel.disabled = !rel.configured || !rel.list.length;
  sel.title = rel.configured ? (rel.list.length ? "Show only work items whose Jira ticket is in this release" : "No releases found") : "Connect Jira in Settings to filter by release";
  if (rel.selected && !rel.list.some((r) => r.id === rel.selected)) { rel.selected = ""; localStorage.setItem("sd_release", ""); }
  sel.value = rel.selected;
}

async function showBoard() {
  const c = $("content"); c.replaceChildren(h("div", { class: "page" }, h("div", { class: "note" }, "Loading board…")));
  try { boardData = await api("/board?mode=" + st.mode); } catch (e) { c.replaceChildren(h("div", { class: "page" }, h("div", { class: "toast err" }, e.message))); return; }
  const relsel = h("select", { id: "relsel", "aria-label": "Release filter", disabled: true, onchange: (e) => selectRelease(e.target.value) }, h("option", { value: "" }, "All releases"));
  const rail = h("aside", { class: "rail", "aria-label": "Jira releases" });
  c.replaceChildren(h("div", { class: "page wide" },
    h("div", { class: "crumbs" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); show("board"); } }, "Boards"), " / ", MODE_NAME[st.mode]),
    h("h1", {}, MODE_NAME[st.mode] + " board"),
    h("div", { class: "toolbar" }, h("label", { class: "filter", for: "relsel" }, h("span", {}, "Release"), relsel), h("span", { id: "boardstatus", class: "note" })),
    h("div", { class: "boardlayout" }, h("div", { class: "board", id: "boardcols" }), rail)));
  rel.keys = null; drawColumns();
  loadReleases(rail, false);
}

// ---- Jira releases (project versions), shown at the right of the board
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
    !r.releases.length ? h("div", { class: "msg" }, "No releases in this project yet.") : null].filter(Boolean));
  if (rel.selected) await selectRelease(rel.selected); else drawColumns();
}

// ------------------------------------------------------------------------------------------ chat
function showChat() {
  if (!st.sid) {
    $("content").replaceChildren(h("div", { class: "empty" }, h("h1", {}, "Start a chat"),
      h("p", {}, "Describe the work or the incident. Supdev clarifies, plans and investigates — and stops at every gate for your approval."),
      h("div", { class: "picks" },
        h("button", { class: "pick", onclick: () => openCreate("dev") }, h("b", {}, "Development"), h("span", {}, "Ticket, story or design → clarify → plan → approved code → tests → PR.")),
        h("button", { class: "pick", onclick: () => openCreate("support") }, h("b", {}, "Support"), h("span", {}, "Production issue → triage → evidence → RCA → approved outputs. Production stays read-only.")))));
    return;
  }
  return loadItem(true);
}

async function loadItem(full) {
  let v;
  try { v = await api("/sessions/" + st.sid); } catch (e) { setState("sid", null); toast(e.message, true); return showChat(); }
  if (v.active && v.active.mode !== st.mode) { setState("mode", v.active.mode); renderNav(); renderSidebar(); }
  if (full) buildItem(v);
  drawHeader(v); drawApprovals(v); drawDetails(v);
  return v;
}

function buildItem(v) {
  st.feed = h("div", { class: "feed", "aria-live": "polite" });
  v.messages.forEach((m) => addPost(m.role, m.text, m.ts));
  if (!v.messages.length) st.feed.append(h("div", { class: "event" }, h("span", { class: "dot" }), "No messages yet — describe the work below."));
  const input = h("textarea", { id: "input", rows: "2", placeholder: "Add a comment or answer… (Enter to send, Shift+Enter for a new line; type “approved” at a gate or use the button)" });
  const form = h("form", { class: "composer", onsubmit: (e) => { e.preventDefault(); send(input); } }, avatar($("user")?.value || "me", "sm"),
    h("div", { class: "box" }, input, h("div", { class: "actions" }, h("button", { class: "primary", id: "send", type: "submit" }, "Send"), h("span", { class: "hint" }, "Only a human can approve gates."))));
  input.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } };
  $("content").replaceChildren(h("div", { class: "page" },
    h("div", { id: "hdr" }),
    h("div", { class: "itemcols" },
      h("section", { class: "activity" }, h("h2", {}, "Activity"), st.feed, h("div", { id: "approvals", class: "actionpanel" }), form),
      h("aside", { class: "details", id: "details" }))));
  st.feed.scrollTop = 1e9; window.scrollTo(0, 0);
}

const TAG = /^\s*\[(DEV|SUPPORT|ROUTER)([^\]]*)\]\s*/;
function addPost(role, text, ts) {
  if (role !== "user" && role !== "assistant") return;
  let tag = null, body = text; const m = TAG.exec(text);
  if (m && role === "assistant") { tag = (m[1] + m[2]).replace(/\s*·\s*/g, " · ").trim(); body = text.slice(m[0].length); }
  const me = $("user")?.value || "me";
  st.feed.append(h("div", { class: "post " + role }, role === "user" ? avatar(me) : botAvatar(),
    h("div", { class: "body" }, h("div", { class: "hd" }, h("span", { class: "name" }, role === "user" ? "You" : "Supdev"), ts ? h("span", { class: "ts" }, ago(ts)) : null, tag ? loz(tag, "blue") : null),
      h("div", { class: "txt" }, body))));
  st.feed.parentElement && (document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight);
}
function addEvent(text, cls) { if (!st.feed) return; st.feed.append(h("div", { class: "event " + (cls || "") }, h("span", { class: "dot" }), text)); }
function setTyping(on) {
  if (st.typing) { st.typing.remove(); st.typing = null; }
  if (on && st.feed) { st.typing = h("div", { class: "typing" }, h("i"), h("i"), h("i"), h("span", {}, "Supdev is working…")); st.feed.append(st.typing); }
}

function drawHeader(v) {
  const a = v.active, box = $("hdr"); if (!box) return;
  if (!a) {
    box.replaceChildren(h("div", { class: "crumbs" }, "Chat / ", shortId(v.id)), h("h1", {}, v.awaiting_router ? "Which mode?" : "New chat"),
      h("div", { class: "row" }, v.awaiting_router ? loz("waiting for your choice", "yellow") : loz("not started")),
      h("p", { class: "note" }, v.awaiting_router ? "Reply “dev” or “support” below." : "Describe the work or the incident to begin."));
    return;
  }
  const pend = a.pending_approvals.length;
  box.replaceChildren(
    h("div", { class: "crumbs" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); show("board"); } }, MODE_NAME[a.mode] + " board"), " / ", a.ref || shortId(a.id)),
    h("h1", {}, a.title || a.ref || "Untitled work item"),
    h("div", { class: "row" }, loz(MODE_NAME[a.mode], MODE_COLOR[a.mode]), loz(`Phase ${a.phase} of ${a.phases.length} · ${a.phases[a.phase - 1]}`, "gray"), pend ? loz(pend + " needs approval", "yellow") : null),
    h("div", { class: "workflow", role: "list" }, a.phases.map((t, i) => h("div", { class: "wf " + (i + 1 < a.phase ? "done" : i + 1 === a.phase ? "cur" : ""), role: "listitem", title: t }, `${i + 1}. ${t}`))));
}

function drawApprovals(v) {
  const box = $("approvals"); if (!box) return; box.replaceChildren();
  ((v.active && v.active.pending_approvals) || []).forEach((p) => {
    const btn = h("button", { class: "primary", disabled: !p.can_approve || st.busy, title: p.can_approve ? "" : "Your role cannot approve this action", onclick: async () => {
      btn.disabled = true;
      try { await api(`/sessions/${st.sid}/approvals/${p.id}/grant`, { method: "POST", body: JSON.stringify({ seen_hash: p.hash }) }); addEvent(`You approved “${p.kind}”`); await run(`/sessions/${st.sid}/continue`, {}); }
      catch (e) { toast(e.message, true); btn.disabled = false; }
    } }, "Approve exactly this");
    box.append(h("div", { class: "card" }, h("h3", {}, "Action required", loz(p.kind.replace(/_/g, " "), "yellow")),
      h("div", { class: "note" }, "Review the exact artifact below. Approval is void if it changes."),
      h("pre", {}, typeof p.content === "string" ? p.content : JSON.stringify(p.content, null, 2)),
      h("div", { class: "row" }, btn, h("span", { class: "h" }, `hash ${p.hash.slice(0, 12)} · can approve: ${p.roles.join(", ") || "nobody"}`))));
  });
}

function drawDetails(v) {
  const box = $("details"); if (!box) return; const a = v.active;
  const sec = (title, ...kids) => h("section", {}, h("h3", {}, title), ...kids);
  const kv = (k, val) => h("div", { class: "kv" }, h("span", { class: "k" }, k), h("span", {}, val));
  const list = (items, empty) => h("ul", { class: "list" }, items.length ? items : h("li", { class: "none" }, empty));
  const out = [];
  out.push(sec("Details", kv("Mode", a ? loz(MODE_NAME[a.mode], MODE_COLOR[a.mode]) : "—"), kv("Ticket", a?.ref || "—"), kv("Phase", a ? `${a.phase} / ${a.phases.length}` : "—"), kv("Session", shortId(v.id)),
    kv("Budget", `${v.budget.tool_calls.used} calls · ${v.budget.query_units.used} queries · ${v.budget.tokens.used} tokens`)));
  if (a && a.mode === "dev") out.push(sec("Requirements", list((a.requirements || []).map((r) => h("li", {}, loz(r.status, r.status === "clear" ? "green" : r.status === "assumed" ? "yellow" : "red"), h("span", {}, `${r.id}: ${r.text}`))), "None recorded yet")));
  if (a && a.mode === "support") out.push(sec("Evidence", list((a.evidence || []).map((x) => h("li", {}, loz(x.id, "blue"), h("span", {}, `${x.source_tool} — ${x.finding}`))), "No evidence yet")));
  if (v.parked.length) out.push(sec("Parked work items", h("ul", { class: "list" }, v.parked.map((w) => h("li", {}, loz(MODE_NAME[w.mode] || w.mode, MODE_COLOR[w.mode]), h("span", { style: "flex:1" }, w.title || w.id),
    w.status === "parked" ? h("button", { class: "subtle", onclick: async () => { try { await api(`/sessions/${st.sid}/resume/${w.id}`, { method: "POST" }); await loadItem(true); loadSessions(); } catch (e) { toast(e.message, true); } } }, "Resume") : loz(w.status.replace("_", " "), "purple"))))));
  box.replaceChildren(...out);
}

// ------------------------------------------------------------------------------------------ streaming
async function send(input) {
  const text = input.value.trim(); if (!text || st.busy) return;
  input.value = ""; addPost("user", text, Date.now() / 1000);
  await run(`/sessions/${st.sid}/messages`, { text });
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
  finally { st.busy = false; setTyping(false); const b = $("send"); if (b) b.disabled = false; await loadItem(false); loadSessions(); }
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
  else if (ev === "mode") addEvent(`Started ${MODE_NAME[d.mode] || d.mode} work item`);
  else if (ev === "error") addEvent(d.message, "err");
  if (["tool_call", "assistant", "audit_notice"].includes(ev)) setTyping(true);
}

// ------------------------------------------------------------------------------------------ create
function openCreate(preset) {
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
      const s = await api("/sessions", { method: "POST" }); setState("sid", s.id);
      const title = summary.value.trim().split("\n")[0].slice(0, 80) || key.value.trim();
      if (type.value !== "auto") { await api(`/sessions/${s.id}/mode`, { method: "POST", body: JSON.stringify({ mode: type.value, ref: key.value.trim() || null, title }) }); setState("mode", type.value); }
      close(); await show("chat"); loadSessions();
      if (summary.value.trim()) { addPost("user", summary.value.trim(), Date.now() / 1000); await run(`/sessions/${s.id}/messages`, { text: summary.value.trim(), ref: key.value.trim() || null, ticket_type: tt.value || null }); }
    } catch (e) { err.textContent = e.message; go.disabled = false; }
  } }, "Create");
  const f = (label, el) => h("div", { class: "field" }, h("label", {}, label), el);
  const back = h("div", { class: "modalback", onclick: (e) => { if (e.target === back) close(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": "Create work item" },
      h("div", { class: "mh" }, h("h2", {}, "Create work item")),
      h("div", { class: "mb" }, f("Work type", type), h("div", { class: "grid2" }, f("Ticket key", key), f("Ticket type", tt)), f("Summary", summary), err),
      h("div", { class: "mf" }, h("button", { class: "subtle", onclick: close }, "Cancel"), go)));
  document.body.append(back); summary.focus();
  back.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
}

// ------------------------------------------------------------------------------------------ boot
$("nav-board").onclick = () => show("board");
$("nav-chat").onclick = () => show("chat");
$("create").onclick = () => openCreate();
$("search").oninput = renderSidebar;
document.querySelectorAll("#modeseg button").forEach((b) => (b.onclick = () => { setState("mode", b.dataset.mode); if (st.view === "chat" && st.sid) { setState("sid", null); } show(st.view); }));
initAuthUI(() => { setState("sid", null); loadSessions(); show(st.view); }).then(async () => { await loadSessions(); await show(st.view); });
