// Settings page. All dynamic content is built with textContent/DOM APIs (no innerHTML) — values from the server
// or from integrations can never inject markup. Secret fields are write-only: the server never returns values.
const $ = _$;
let CFG = null;
let TAB = "llm";

const el = h; // shared DOM builder (textContent only)
const field = (label, input, hint) => el("div", { class: "field" }, el("label", {}, label), input, hint ? el("div", { class: "note" }, hint) : null);
const inp = (v, ph, type = "text") => el("input", { type, value: v ?? "", placeholder: ph || "" });
const secretInp = (configured, ph) => el("input", { type: "password", autocomplete: "new-password", placeholder: configured ? "configured ✓ — leave blank to keep" : ph || "paste value" });
const status = (txt, cls) => el("span", { class: "status " + cls }, txt);

function banner(msg, ok) { toast(msg, !ok); }
async function api(path, opts = {}) {
  const r = await fetch("/api/admin" + path, { ...opts, headers: { ...identHeaders(), ...(opts.headers || {}) } });
  const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) {}
  if (!r.ok) throw new Error((j && (j.detail?.[0]?.msg || j.detail)) || t || r.statusText);
  return j;
}
async function save(fn, okMsg) { try { CFG = await fn(); banner(okMsg || "Saved", true); render(); } catch (e) { banner(e.message, false); } }
const put = (path, body) => () => api(path, { method: "PUT", body: JSON.stringify(body) });

// ------------------------------------------------------------------------------------------------ tabs
const NAV = [
  ["AI", [["llm", "LLM provider", "Which model the agent uses and the API key it runs with."]]],
  ["Connections", [["integrations", "Integrations", "Ticketing, source control, observability and design tools."], ["mcp", "MCP servers", "Connect MCP servers and choose exactly which tools the agent may use."],
    ["board", "Board", "Which Jira issue types each board shows."], ["sync", "Jira status sync", "Move the Jira ticket automatically when the agent changes phase."]]],
  ["Agent", [["policy", "Policy", "Limits and restrictions. A tenant policy can only tighten the platform baseline."]]],
  ["Workspace", [["general", "General", "Name, environments and context the agent should know."], ["tokens", "Users & tokens", "API tokens for your team."]]],
];
const TABS = NAV.flatMap(([, items]) => items);
function render() {
  const nav = $("snav"); nav.replaceChildren();
  NAV.forEach(([group, items]) => { nav.append(h("h4", {}, group)); items.forEach(([k, t]) => nav.append(h("button", { class: k === TAB ? "on" : "", onclick: () => { TAB = k; render(); } }, t))); });
  const p = $("panel"); p.replaceChildren();
  const meta = TABS.find((t) => t[0] === TAB);
  $("pagehead").replaceChildren(h("div", { class: "crumbs" }, "Settings / ", meta[1]), h("h1", {}, meta[1]), h("p", { class: "note" }, meta[2]));
  if (!CFG) return;
  ({ llm: tabLLM, integrations: tabIntegrations, mcp: tabMCP, board: tabBoard, sync: tabSync, policy: tabPolicy, general: tabGeneral, tokens: tabTokens })[TAB](p);
}
async function load() {
  try { CFG = await api("/config"); render(); }
  catch (e) { CFG = null; render(); $("panel").replaceChildren(h("div", { class: "panel-card" }, h("h3", {}, "Can't load settings"), h("p", {}, e.message), h("p", { class: "note" }, "Settings need the admin role (edit) or lead role (view). Use the avatar menu to change identity or paste a token."))); }
}

// -------------------------------------------------------------------------------------------------- LLM
function tabLLM(p) {
  const prov = el("select", {}, el("option", { value: "" }, "Platform default"), ...CFG.options.llm_providers.map((x) => el("option", { value: x }, x)));
  prov.value = CFG.tenant.llm.provider || "";
  const model = inp(CFG.tenant.llm.model, "e.g. claude-sonnet-5 (blank = provider default)");
  const key = secretInp(CFG.llm_key_configured, "API key");
  const res = el("div", { class: "result" });
  p.append(el("div", { class: "panel-card" },
    el("h3", {}, "LLM provider", CFG.llm_key_configured ? status("key configured ✓", "ok") : status("no key", "warn")),
    el("div", { class: "note" }, "Your key is encrypted at rest and is never shown again. The agent uses only this tenant's key; if you pick a provider without a key, requests fail rather than using anyone else's."),
    el("div", { class: "grid2" }, field("Provider", prov), field("Model", model)),
    field("API key (write-only)", key),
    el("div", { class: "actions" },
      el("button", { class: "primary", onclick: () => save(put("/llm", { provider: prov.value || null, model: model.value || null, api_key: key.value || null })) }, "Save"),
      el("button", { onclick: async () => { res.textContent = "testing…"; res.className = "result"; try { const r = await api("/llm/test", { method: "POST" }); res.textContent = r.ok ? `OK — model replied “${r.detail}” (${r.tokens} tokens)` : "Failed: " + r.detail; res.className = "result " + (r.ok ? "ok" : "err"); } catch (e) { res.textContent = e.message; res.className = "result err"; } } }, "Test connection"),
      CFG.llm_key_configured ? el("button", { class: "danger", onclick: () => save(() => api("/llm/key", { method: "DELETE" }), "Key removed") }, "Remove key") : null),
    res));
}

// ------------------------------------------------------------------------------------------ integrations
const FORMS = {
  jira: { title: "Jira (ticketing)", fields: [["base_url", "Base URL", "https://acme.atlassian.net"], ["project", "Default project key (releases + follow-up tickets)", "e.g. SCRUM — the KEY shown before ticket numbers"]], secrets: [["token", "API token"], ["email", "Account email"]] },
  github: { title: "GitHub (source control)", fields: [["repo", "Repository", "org/name or https://github.com/org/name"], ["default_branch", "Default branch", "main"], ["clone_url", "Clone URL (optional, https)", ""]], secrets: [["token", "Access token"]] },
  figma: { title: "Figma (design)", fields: [], secrets: [["token", "Personal access token"]] },
  local_exec: { title: "Local test runner (execution)", fields: [["commands.tests", "Test command", "pytest -q"], ["commands.lint", "Lint command", "ruff check ."], ["commands.typecheck", "Type-check command", "mypy ."]], secrets: [], gate: "local_exec_allowed", gateNote: "Runs repository code on the platform host. The platform operator must enable it (SUPDEV_ALLOW_LOCAL_EXEC=1) and run the platform in a sandbox. It uses the clone made by the GitHub integration." },
  grafana: { title: "Grafana (metrics)", instances: [["url", "URL", "https://grafana.acme.io"], ["datasource_uid", "Prometheus datasource UID", "prom"]] },
  loki: { title: "Loki (logs)", instances: [["url", "URL", "https://loki.acme.io"]] },
  rancher: { title: "Rancher / Kubernetes (cluster events)", instances: [["url", "URL", "https://rancher.acme.io"], ["cluster_id", "Cluster ID", "c-abc12"]] },
};
const getPath = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);

function integrationCard(name, form) {
  const saved = CFG.integrations[name];
  const cfg = saved ? saved.config : {};
  const missing = saved ? saved.missing_secrets : [];
  const inputs = {}, secretInputs = {}, res = el("div", { class: "result" });
  const gated = form.gate && !CFG.platform[form.gate];
  const card = el("div", { class: "panel-card" }, el("h3", {}, form.title,
    !saved ? status("not configured", "warn") : missing.length ? status("missing: " + missing.join(", "), "bad") : status("configured ✓", "ok")));
  if (form.gateNote) card.append(el("div", { class: "note" }, form.gateNote));
  (form.fields || []).forEach(([k, label, ph]) => { inputs[k] = inp(getPath(cfg, k), ph); if (gated) inputs[k].disabled = true; card.append(field(label, inputs[k])); });
  (form.secrets || []).forEach(([k, label]) => { secretInputs[k] = secretInp(saved && !missing.includes(name + "_" + k), label); card.append(field(label + " (write-only)", secretInputs[k])); });
  let instBox = null, rows = [];
  if (form.instances) {
    instBox = el("div", {});
    const addRow = (inst) => {
      const r = { env: inp(inst?.env, "prod"), tok: secretInp(inst && saved && !missing.includes(inst.secret), "token"), f: {} };
      const box = el("div", { class: "inst" }, el("div", { class: "grid2" }, field("Environment", r.env), field("Token (write-only)", r.tok)),
        ...form.instances.map(([k, label, ph]) => { r.f[k] = inp(inst?.[k], ph); return field(label, r.f[k]); }));
      box.append(el("button", { class: "danger", onclick: () => { rows = rows.filter((x) => x !== r); box.remove(); } }, "Remove instance"));
      r.box = box; rows.push(r); instBox.append(box);
    };
    (cfg.instances || [{}]).forEach(addRow);
    card.append(instBox, el("button", { onclick: () => addRow() }, "+ Add environment"));
  }
  const collect = () => {
    const config = {}, secrets = {};
    Object.entries(inputs).forEach(([k, i]) => { if (!i.value.trim()) return; const parts = k.split("."); let o = config; parts.slice(0, -1).forEach((q) => (o = o[q] = o[q] || {})); o[parts.at(-1)] = i.value.trim(); });
    Object.entries(secretInputs).forEach(([k, i]) => { if (i.value) secrets[k] = i.value; });
    if (form.instances) { config.instances = rows.map((r) => ({ env: r.env.value.trim(), ...Object.fromEntries(Object.entries(r.f).map(([k, i]) => [k, i.value.trim()])) })); rows.forEach((r) => { if (r.tok.value) secrets[r.env.value.trim()] = r.tok.value; }); }
    return { config, secrets };
  };
  const err = el("div", { class: "result err" });
  const runTest = async () => {   // tests the form AS TYPED (nothing is saved); blank secret boxes fall back to the stored secret
    res.textContent = "testing…"; res.className = "result"; res.style.whiteSpace = "pre-wrap";
    try {
      const r = await api(`/integrations/${name}/test`, { method: "POST", body: JSON.stringify(collect()) });
      res.textContent = r.results.map((x) => `${x.adapter ? x.adapter + ": " : ""}${x.ok ? "✓ OK" : "✗ FAILED"} — ${x.detail}`).join("\n"); res.className = "result " + (r.ok ? "ok" : "err");
    } catch (e) { res.textContent = "✗ " + e.message; res.className = "result err"; }
  };
  const doSave = async () => {
    err.textContent = ""; saveBtn.disabled = true;
    try { CFG = await api("/integrations/" + name, { method: "PUT", body: JSON.stringify(collect()) }); banner(form.title + " saved", true); render(); }
    catch (e) { err.textContent = "Not saved: " + e.message; banner(e.message, false); saveBtn.disabled = false; }   // inline + toast: the reason stays visible
  };
  const saveBtn = el("button", { class: "primary", disabled: gated, onclick: doSave }, "Save");
  card.append(err, el("div", { class: "actions" }, saveBtn,
    el("button", { disabled: gated, title: "Check the credentials and settings shown above — without saving", onclick: runTest }, "Test connection"),
    saved ? el("button", { class: "danger", onclick: () => save(() => api("/integrations/" + name, { method: "DELETE" }), "Removed") }, "Remove") : null), res);
  return card;
}
function tabIntegrations(p) {
  p.append(el("div", { class: "note" }, "Credentials are encrypted at rest and write-only. Anything tagged production is read-only for the agent, always."));
  Object.entries(FORMS).forEach(([n, f]) => p.append(integrationCard(n, f)));
  Object.keys(CFG.integrations).filter((n) => !FORMS[n] && n !== "mcp").forEach((n) => p.append(el("div", { class: "panel-card" }, el("h3", {}, n, status("built-in / seeded", "warn")),
    el("div", { class: "actions" }, el("button", { class: "danger", onclick: () => save(() => api("/integrations/" + n, { method: "DELETE" }), "Removed") }, "Remove")))));
}

// ------------------------------------------------------------------------------------------------- MCP
function tabMCP(p) {
  const saved = (CFG.integrations.mcp?.config.servers || []);
  p.append(el("div", { class: "note" }, "Only tools you tag below are ever exposed to the agent (untagged = denied). Production tools can only be 'read'; write tools need an approval kind, so they always wait for a human."));
  if (!CFG.platform.stdio_mcp_allowed) p.append(el("div", { class: "note" }, "Local-command (stdio) servers are disabled by the platform operator because they run code on the host. Remote (URL) servers are available."));
  saved.forEach((s) => p.append(mcpCard(s, saved)));
  const add = el("button", { class: "primary", onclick: () => { const c = mcpCard(null, saved); add.replaceWith(c); } }, "+ Add MCP server");
  p.append(add);
}
function mcpCard(srv, all) {
  const isNew = !srv; srv = srv || { name: "", tools: {} };
  const name = inp(srv.name, "e.g. grafana-mcp"); if (!isNew) name.disabled = true;
  const transport = el("select", {}, el("option", { value: "url" }, "Remote (streamable HTTP URL)"), CFG.platform.stdio_mcp_allowed ? el("option", { value: "command" }, "Local command (stdio)") : null);
  transport.value = srv.command ? "command" : "url";
  const url = inp(srv.url, "https://mcp.example.com/mcp"), cmd = inp(srv.command, "uvx"), args = inp((srv.args || []).join(" "), "mcp-server-foo --flag");
  const secretRows = []; const secBox = el("div", {});
  const addSecret = (k, configured) => { const key = inp(k, transport.value === "url" ? "Header, e.g. Authorization" : "ENV_VAR_NAME"); const val = secretInp(configured, "value"); secretRows.push({ key, val }); const row = el("div", { class: "grid2" }, field(transport.value === "url" ? "Header name" : "Env var", key), field("Value (write-only)", val)); secBox.append(row); };
  Object.keys(srv.header_secrets || srv.env_secrets || {}).forEach((k) => addSecret(k, !(CFG.integrations.mcp?.missing_secrets || []).includes((srv.header_secrets || srv.env_secrets)[k])));
  const urlBox = el("div", {}, field("Server URL", url)), cmdBox = el("div", {}, field("Command", cmd, "Must be in the operator's allowlist."), field("Arguments (space separated)", args));
  const sync = () => { urlBox.hidden = transport.value !== "url"; cmdBox.hidden = transport.value !== "command"; }; transport.onchange = sync; sync();
  let tools = { ...srv.tools }; let remote = []; const tbl = el("div", {}); const res = el("div", { class: "result" });
  const drawTools = () => {
    const names = [...new Set([...remote.map((t) => t.name), ...Object.keys(tools)])];
    if (!names.length) { tbl.replaceChildren(el("div", { class: "note" }, "No tools yet — click “Discover tools”.")); return; }
    const rows = names.map((n) => {
      const t = tools[n], r = remote.find((x) => x.name === n);
      const on = el("input", { type: "checkbox", checked: !!t });
      const cap = el("select", {}, ...CFG.options.capabilities.map((c) => el("option", { value: c }, c))); cap.value = t?.capability || "logs";
      const acc = el("select", {}, ...CFG.options.access.map((c) => el("option", { value: c }, c))); acc.value = t?.access || "read";
      const env = inp(t?.environment, "prod"), bnd = el("input", { type: "checkbox", checked: !!t?.bounded });
      const ap = el("select", {}, el("option", { value: "" }, "—"), ...CFG.options.approval_kinds.map((c) => el("option", { value: c }, c))); ap.value = t?.approval_kind || "";
      const row = { n, on, cap, acc, env, bnd, ap, r, t };
      return [row, el("tr", {}, el("td", {}, on), el("td", {}, el("div", { class: "mono" }, n), el("div", { class: "note" }, (r?.description || t?.description || "").slice(0, 120))), el("td", {}, cap), el("td", {}, acc), el("td", {}, env), el("td", {}, bnd), el("td", {}, ap))];
    });
    tbl.replaceChildren(el("table", { class: "tags" }, el("thead", {}, el("tr", {}, ["Expose", "Tool", "Capability", "Access", "Environment", "Bounded", "Approval kind (writes)"].map((h) => el("th", {}, h)))), el("tbody", {}, rows.map((x) => x[1]))));
    tbl._rows = rows.map((x) => x[0]);
  };
  drawTools();
  const collectTools = () => Object.fromEntries((tbl._rows || []).filter((r) => r.on.checked).map((r) => [r.n, { capability: r.cap.value, access: r.acc.value, environment: r.env.value.trim() || null, bounded: r.bnd.checked, approval_kind: r.ap.value || null, description: (r.r?.description || r.t?.description || "").slice(0, 300), input_schema: r.r?.input_schema || r.t?.input_schema || null }]));
  const draft = () => {
    const s = { name: name.value.trim() }; const secrets = {};
    if (transport.value === "url") { s.url = url.value.trim(); s.header_secrets = {}; } else { s.command = cmd.value.trim(); s.args = args.value.trim() ? args.value.trim().split(/\s+/) : []; s.env_secrets = {}; }
    secretRows.forEach(({ key, val }) => { const k = key.value.trim(); if (!k) return; (s.header_secrets || s.env_secrets)[k] = true; if (val.value) secrets[k] = val.value; });
    return { s, secrets };
  };
  const card = el("div", { class: "panel-card" }, el("h3", {}, isNew ? "New MCP server" : "MCP: " + srv.name),
    el("div", { class: "grid2" }, field("Name", name), field("Transport", transport)), urlBox, cmdBox,
    el("h4", {}, "Credentials"), secBox, el("button", { onclick: () => addSecret("", false) }, "+ Add credential"),
    el("h4", {}, "Tools"), tbl,
    el("div", { class: "actions" },
      el("button", { onclick: async () => { res.textContent = "connecting…"; res.className = "result"; try { const d = draft(); const r = await api("/mcp/discover", { method: "POST", body: JSON.stringify({ server: d.s, secrets: d.secrets }) }); remote = r.tools; drawTools(); res.textContent = `${r.tools.length} tools found. Tag the ones the agent may use, then save.`; res.className = "result ok"; } catch (e) { res.textContent = e.message; res.className = "result err"; } } }, "Discover tools"),
      el("button", { class: "primary", onclick: () => { const d = draft(); d.s.tools = collectTools(); const servers = all.filter((x) => x.name !== d.s.name).concat([d.s]); const secrets = {}; Object.entries(d.secrets).forEach(([k, v]) => (secrets[`${d.s.name}.${k}`] = v)); save(put("/integrations/mcp", { config: { servers }, secrets }), "MCP server saved"); } }, "Save"),
      !isNew ? el("button", { onclick: async () => { res.textContent = "testing…"; try { const r = await api("/integrations/mcp/test", { method: "POST" }); res.textContent = r.results.map((x) => `${x.adapter}: ${x.ok ? "OK" : "FAILED"} — ${x.detail}`).join("\n"); res.style.whiteSpace = "pre-wrap"; res.className = "result " + (r.ok ? "ok" : "err"); } catch (e) { res.textContent = e.message; res.className = "result err"; } } }, "Test connection") : null,
      !isNew ? el("button", { class: "danger", onclick: () => save(put("/integrations/mcp", { config: { servers: all.filter((x) => x.name !== srv.name) }, secrets: {} }), "Server removed") }, "Remove") : null), res);
  return card;
}

// ------------------------------------------------------------------------------------------ what your Jira really has
let JIRA_META = null;   // {configured, types, statuses:[{name, category}]} — fetched from Jira; nothing is assumed about your project
async function jiraMeta(force) {
  if (JIRA_META && !force) return JIRA_META;
  try { JIRA_META = await api("/jira/meta"); } catch (e) { JIRA_META = { configured: false, types: [], statuses: [], error: e.message }; }
  return JIRA_META;
}
const notConnected = (m) => el("div", { class: "result err" }, m.error ? "Couldn't read your Jira project: " + m.error : "Connect the Jira integration (with a project key) first — Settings → Integrations.");

// ------------------------------------------------------------------------------------------ Board (issue types per board)
async function tabBoard(p) {
  p.append(el("div", { class: "note" }, "Loading your Jira project…"));
  const meta = await jiraMeta(true), labels = CFG.options.mode_labels, cfg = (CFG.tenant.board || {}).types || {};
  p.replaceChildren();
  if (!meta.configured || !meta.types.length) { p.append(el("div", { class: "panel-card" }, notConnected(meta))); return; }
  const picks = {};
  Object.keys(labels).forEach((mode) => {
    const chosen = cfg[mode] == null ? null : cfg[mode].map((t) => t.toLowerCase());
    const all = el("input", { type: "checkbox", checked: chosen === null });
    const boxes = meta.types.map((t) => ({ t, c: el("input", { type: "checkbox", checked: chosen === null || chosen.includes(t.toLowerCase()) }) }));
    const sync = () => boxes.forEach((b) => { b.c.disabled = all.checked; if (all.checked) b.c.checked = true; }); all.onchange = sync; sync();
    picks[mode] = { all, boxes };
    p.append(el("div", { class: "panel-card" }, el("h3", {}, labels[mode] + " board"),
      el("div", { class: "note" }, `Issue types found in Jira project ${meta.project}. Epics and sub-tasks are never shown. People can still toggle types on the board itself; this sets what is on by default.`),
      el("label", { class: "checks", style: "margin:8px 0" }, all, el("b", {}, " Every type (recommended — follows your Jira automatically)")),
      el("div", { class: "checks" }, boxes.map((b) => el("label", {}, b.c, " ", b.t)))));
  });
  const err = el("div", { class: "result err" });
  p.append(err, el("div", { class: "actions" }, el("button", { class: "primary", onclick: async () => {
    const types = {}; Object.entries(picks).forEach(([mode, k]) => { types[mode] = k.all.checked ? null : k.boxes.filter((b) => b.c.checked).map((b) => b.t); });
    err.textContent = "";
    try { CFG = await api("/board", { method: "PUT", body: JSON.stringify({ types }) }); banner("Board settings saved", true); render(); }
    catch (e) { err.textContent = "Not saved: " + e.message; banner(e.message, false); }
  } }, "Save")));
}

// ------------------------------------------------------------------------------------------ Jira status sync
async function tabSync(p) {
  p.append(el("div", { class: "note" }, "Loading your Jira statuses…"));
  const meta = await jiraMeta(true), cur = CFG.tenant.jira_sync || {}, phases = CFG.options.phases, labels = CFG.options.mode_labels;
  const map = cur.map || {}, inputs = {};
  p.replaceChildren();
  const on = el("input", { type: "checkbox", checked: !!cur.enabled });
  const list = el("datalist", { id: "jira-statuses" }, meta.statuses.map((s) => el("option", { value: s.name }, s.category)));
  p.append(el("div", { class: "panel-card" }, el("h3", {}, "Automatic Jira status", cur.enabled ? status("on", "ok") : status("off", "warn")),
    el("div", { class: "note" }, "When on, Supdev itself (not the model) moves the linked Jira ticket the moment the agent moves a work item FORWARD into a phase you map below. It uses your Jira integration's credentials, only performs transitions your Jira workflow allows, never moves a ticket backwards or reopens a Done ticket, only acts for users whose role may change ticket status, never blocks the phase change if Jira refuses, and every move is written to the audit log. Off by default, and nothing is assumed about your statuses — you choose them."),
    meta.configured ? el("div", { class: "note" }, "Your Jira statuses: " + meta.statuses.map((s) => `${s.name} (${s.category})`).join(" · ")) : notConnected(meta),
    el("label", { class: "checks", style: "margin:10px 0" }, el("span", { class: "checks" }, on, el("b", {}, " Move the Jira ticket automatically when the agent changes phase"))), list));
  Object.entries(phases).forEach(([mode, rows]) => {
    const trs = rows.map((ph, i) => {
      const inp_ = el("input", { type: "text", list: "jira-statuses", value: ((map[mode] || {})[String(i + 1)] || []).join(", "), placeholder: "no change" }); inputs[`${mode}:${i + 1}`] = inp_;
      return el("tr", {}, el("td", {}, `${i + 1}`), el("td", {}, ph.title, ph.hint ? el("span", { class: "note", style: "margin-left:6px" }, ph.hint === "work" ? "(work begins)" : "(out for review)") : null), el("td", {}, inp_));
    });
    p.append(el("div", { class: "panel-card" }, el("h3", {}, labels[mode] + " phases"),
      el("div", { class: "note" }, "Type or pick real Jira status names; several can be listed, separated by commas — the first one your workflow allows wins. Blank = no change."),
      el("table", { class: "tags" }, el("thead", {}, el("tr", {}, ["#", "Phase", "Move ticket to"].map((x) => el("th", {}, x)))), el("tbody", {}, trs))));
  });
  // Suggest by status CATEGORY (never by name): "work begins" -> first in-progress status; "out for review" -> the last one, if there are two or more.
  const suggest = () => {
    const prog = meta.statuses.filter((s) => s.category === "indeterminate");
    Object.entries(phases).forEach(([mode, rows]) => rows.forEach((ph, i) => {
      const inp_ = inputs[`${mode}:${i + 1}`]; if (!inp_ || !ph.hint) return;
      inp_.value = ph.hint === "work" ? (prog[0] ? prog[0].name : "") : (prog.length > 1 ? prog[prog.length - 1].name : "");
    }));
  };
  const err = el("div", { class: "result err" });
  p.append(err, el("div", { class: "actions" }, el("button", { class: "primary", onclick: async () => {
    const m = {}; Object.entries(inputs).forEach(([k, i]) => { const [mode, ph] = k.split(":"); const names = i.value.split(",").map((x) => x.trim()).filter(Boolean); (m[mode] = m[mode] || {})[ph] = names; });
    err.textContent = "";
    try { CFG = await api("/jira-sync", { method: "PUT", body: JSON.stringify({ enabled: on.checked, map: m }) }); banner(on.checked ? "Jira status sync is ON" : "Jira status sync is off", true); render(); }
    catch (e) { err.textContent = "Not saved: " + e.message; banner(e.message, false); }
  } }, "Save"), el("button", { class: "subtle", disabled: !meta.configured, title: "Fill the “work begins” / “out for review” phases from your Jira statuses, by category", onclick: suggest }, "Suggest from my Jira")));
}

// ----------------------------------------------------------------------------------------------- policy
function tabPolicy(p) {
  const pol = CFG.tenant.policy, o = CFG.options;
  const checks = (items, selected, all) => { const boxes = items.map((i) => ({ i, c: el("input", { type: "checkbox", checked: selected == null ? true : selected.includes(i) }) })); return { boxes, node: el("div", { class: "checks" }, boxes.map((b) => el("label", {}, b.c, " ", b.i))), value: () => { const v = boxes.filter((b) => b.c.checked).map((b) => b.i); return v.length === items.length && all ? null : v; } }; };
  const modes = checks(o.modes, pol.enabled_modes, true), envs = checks(CFG.tenant.environments, pol.allowed_environments, true);
  const deniedAcc = checks(o.access, pol.denied_access, false); deniedAcc.boxes.forEach((b) => (b.c.checked = pol.denied_access.includes(b.i)));
  const tools = el("textarea", { rows: 3, placeholder: "one per line, e.g. source_control.commit" }); tools.value = pol.denied_tools.join("\n");
  const num = (v, ph) => inp(v ?? "", ph, "number");
  const b = { tool_calls: num(pol.budget.tool_calls, "60"), query_units: num(pol.budget.query_units, "40"), tokens: num(pol.budget.tokens, "400000") };
  const pi = num(pol.max_plan_iterations, "3"), hr = num(pol.max_hypothesis_rounds, "3"), lb = inp(pol.default_lookback, "2h"), ft = el("textarea", { rows: 3 }); ft.value = pol.free_text;
  p.append(el("div", { class: "panel-card" }, el("h3", {}, "Policy"),
    el("div", { class: "note" }, "Tenant policy can only tighten the platform baseline: limits are capped at platform values, restrictions add up. It can never grant more."),
    field("Enabled modes", modes.node), field("Environments the agent may read (prod is always read-only)", envs.node), field("Denied access levels", deniedAcc.node), field("Denied tools", tools),
    el("div", { class: "grid2" }, field("Max tool calls / session", b.tool_calls), field("Max query units / session", b.query_units), field("Max tokens / session", b.tokens), field("Default lookback", lb), field("Max plan iterations", pi), field("Max hypothesis rounds", hr)),
    field("Instructions shown to the agent (no authority over platform rules)", ft),
    el("div", { class: "actions" }, el("button", { class: "primary", onclick: () => {
      const bud = {}; Object.entries(b).forEach(([k, i]) => { if (i.value) bud[k] = parseInt(i.value, 10); });
      save(put("/policy", { enabled_modes: modes.value(), allowed_environments: envs.value(), denied_access: deniedAcc.value(), denied_tools: tools.value.split("\n").map((x) => x.trim()).filter(Boolean), budget: bud, max_plan_iterations: pi.value ? parseInt(pi.value, 10) : null, max_hypothesis_rounds: hr.value ? parseInt(hr.value, 10) : null, default_lookback: lb.value || null, free_text: ft.value }), "Policy saved");
    } }, "Save policy"))));
}

// ---------------------------------------------------------------------------------------------- general
function tabGeneral(p) {
  const t = CFG.tenant, name = inp(t.name), envs = inp(t.environments.join(", "), "dev, staging, prod"), tz = inp(t.timezone, "UTC");
  const area = (v, ph) => { const a = el("textarea", { rows: 3, placeholder: ph || "" }); a.value = v || ""; return a; };
  const repo = area(t.repo_context, "Repo layout, languages, how to run things"), conv = area(t.conventions, "Branch naming, commit style, test layout"), cat = area(t.service_catalog, "service — owner/on-call — runbook");
  p.append(el("div", { class: "panel-card" }, el("h3", {}, "General"),
    el("div", { class: "grid2" }, field("Tenant name", name), field("Timezone", tz)),
    field("Environments (comma separated)", envs, "Agent tools tagged with an environment not listed here are not offered."),
    field("Repository context", repo), field("Project conventions", conv), field("Service catalog", cat),
    el("div", { class: "actions" }, el("button", { class: "primary", onclick: () => save(put("/general", { name: name.value, timezone: tz.value || "UTC", environments: envs.value.split(",").map((x) => x.trim()).filter(Boolean), repo_context: repo.value, conventions: conv.value, service_catalog: cat.value })) }, "Save"))));
}

// ------------------------------------------------------------------------------------------------ tokens
async function tabTokens(p) {
  const box = el("div", {}); p.append(box);
  const draw = async () => {
    let list = []; try { list = await api("/tokens"); } catch (e) { box.replaceChildren(el("div", { class: "note" }, e.message)); return; }
    const user = inp("", "user id"), role = el("select", {}, ...CFG.options.roles.map((r) => el("option", { value: r }, r))); role.value = "developer";
    const reveal = el("div", {});
    box.replaceChildren(
      el("div", { class: "panel-card" }, el("h3", {}, "Create API token"), el("div", { class: "note" }, "Tokens are shown once and stored only as a hash. Users paste the token into the “API token” box at the top of the page."),
        el("div", { class: "grid2" }, field("User", user), field("Role", role)),
        el("div", { class: "actions" }, el("button", { class: "primary", onclick: async () => { try { const r = await api("/tokens", { method: "POST", body: JSON.stringify({ user_id: user.value.trim(), role: role.value }) }); reveal.replaceChildren(el("div", { class: "reveal" }, el("b", {}, "Copy this token now — it will not be shown again"), el("div", { class: "mono" }, r.token))); draw2(); } catch (e) { banner(e.message, false); } } }, "Create token")), reveal),
      el("div", { class: "panel-card" }, el("h3", {}, "Existing tokens"), el("table", { class: "tags" }, el("tbody", {}, list.map((t) => el("tr", {}, el("td", {}, t.user_id), el("td", {}, t.role), el("td", {}, new Date(t.created_at * 1000).toLocaleString()), el("td", {}, t.revoked ? status("revoked", "bad") : el("button", { class: "danger", onclick: async () => { await api("/tokens/" + t.id, { method: "DELETE" }); draw(); } }, "Revoke"))))))));
    const draw2 = async () => { const keep = reveal.firstChild; await draw(); if (keep) box.firstChild.lastChild.replaceWith(keep); };
  };
  draw();
}

initAuthUI(load).then(load);
