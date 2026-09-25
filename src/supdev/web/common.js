// Shared helpers for the chat/board and settings pages.
const _$ = (id) => document.getElementById(id);
let AUTH_MODE = "dev-header";

// DOM builder — every dynamic value goes through textContent/createTextNode (never innerHTML).
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === "class") e.className = v;
    else if (k === "onclick" || k === "onchange" || k === "oninput" || k === "onsubmit" || k === "onkeydown") e[k] = v;
    else if (k === "value") e.value = v;
    else if (k === "checked" || k === "disabled" || k === "hidden") e[k] = !!v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
  });
  kids.flat().forEach((c) => { if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c))); });
  return e;
}
const AV_COLORS = ["#5E4DB2", "#1F845A", "#0C66E4", "#C9372C", "#A54800", "#206A83", "#943D73"];
function initials(name) { const p = String(name || "?").trim().split(/[\s._-]+/).filter(Boolean); return ((p[0] || "?")[0] + (p[1] ? p[1][0] : "")).toUpperCase(); }
function avatar(name, cls) { let n = 0; for (const c of String(name || "")) n = (n * 31 + c.charCodeAt(0)) >>> 0; const a = h("span", { class: "avatar " + (cls || "sm"), title: name || "" }, initials(name)); a.style.background = AV_COLORS[n % AV_COLORS.length]; return a; }
function botAvatar() { return h("span", { class: "avatar sm bot", title: "Supdev" }, "S"); }
function ago(ts) { const s = Math.max(0, Date.now() / 1000 - ts); if (s < 45) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago"; if (s < 86400) return Math.round(s / 3600) + " h ago"; return new Date(ts * 1000).toLocaleDateString(); }
function toast(msg, err) {
  let box = _$("toasts"); if (!box) { box = h("div", { id: "toasts", class: "toasts" }); document.body.append(box); }
  const t = h("div", { class: "toast" + (err ? " err" : ""), role: "status" }, msg); box.append(t); setTimeout(() => t.remove(), err ? 7000 : 3500);
}

function identHeaders() {
  const hd = { "Content-Type": "application/json" };
  if (AUTH_MODE === "token") hd["Authorization"] = "Bearer " + (localStorage.getItem("sd_token") || "");
  else { hd["X-Tenant-Id"] = _$("tenant").value; hd["X-User-Id"] = _$("user").value; hd["X-Role"] = _$("role").value; }
  return hd;
}
async function refreshMe() {
  try {
    const r = await fetch("/api/me", { headers: identHeaders() }); if (!r.ok) throw new Error();
    const me = await r.json(); const a = _$("avatar");
    if (a) { a.textContent = initials(me.user_id); a.title = `${me.user_id} · ${me.role} · ${me.tenant_id}`; }
    if (_$("whoami")) { _$("whoami").textContent = me.user_id; _$("whosub").textContent = `${me.role} · ${me.tenant_id}`; }
    return me;
  } catch (e) { if (_$("avatar")) _$("avatar").textContent = "?"; return null; }
}
async function initAuthUI(onChange) {
  try { AUTH_MODE = (await (await fetch("/api/auth/mode")).json()).mode; } catch (e) {}
  document.querySelectorAll(".devauth").forEach((e) => (e.hidden = AUTH_MODE === "token"));
  document.querySelectorAll(".tokenauth").forEach((e) => (e.hidden = AUTH_MODE !== "token"));
  const changed = () => { refreshMe(); onChange && onChange(); };
  ["tenant", "user", "role"].forEach((k) => {
    const el = _$(k); if (!el) return;
    const v = localStorage.getItem("sd_" + k);
    if (v) el.value = v; else if (k === "tenant") el.value = "demo"; else if (k === "user") el.value = "alice"; else if (k === "role" && location.pathname.startsWith("/settings")) el.value = "admin";
    el.addEventListener("change", () => { localStorage.setItem("sd_" + k, el.value); changed(); });
  });
  const t = _$("token");
  if (t) { t.value = localStorage.getItem("sd_token") || ""; t.addEventListener("change", () => { localStorage.setItem("sd_token", t.value.trim()); changed(); }); }
  const av = _$("avatar"), menu = _$("usermenu");
  if (av && menu) {
    av.onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
    document.addEventListener("click", (e) => { if (!menu.contains(e.target) && e.target !== av) menu.hidden = true; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.hidden = true; });
  }
  if (_$("signout")) _$("signout").onclick = () => { localStorage.removeItem("sd_token"); location.reload(); };
  await refreshMe();
}
