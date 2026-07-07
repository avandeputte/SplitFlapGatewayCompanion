// SplitFlapGatewayCompanion — Phase 1 SPA (vanilla).
// Live preview + click-to-type compose grid + settings.

// Lowercase r/o/y/g/b/p/w are COLOUR flaps; uppercase letters are letters.
// This is case-sensitive: 'y' = yellow tile, 'Y' = the letter Y.
const COLOR_CODES = ["r", "o", "y", "g", "b", "p", "w"];
const CODE_TO_EMOJI = { r: "🟥", o: "🟧", y: "🟨", g: "🟩", b: "🟦", p: "🟪", w: "⬜" };
const $ = (id) => document.getElementById(id);

let GRID = { rows: 3, cols: 15, module_count: 45, flap_chars: "", styles: [] };
let focusedCell = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.status === 204 ? null : r.json();
}
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

// ---- rendering helpers -----------------------------------------------------
function classForChar(ch) {
  return COLOR_CODES.includes(ch) ? `flap color-${ch}` : "flap";
}
function glyph(ch) {
  // colour codes (lowercase) render as an empty coloured tile
  return COLOR_CODES.includes(ch) ? "" : (ch || "");
}

function buildBoard(el, count, cols, editable) {
  el.style.gridTemplateColumns = `repeat(${cols}, auto)`;
  el.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const cell = document.createElement("div");
    cell.className = "flap";
    if (editable) {
      const inp = document.createElement("input");
      inp.maxLength = 1;
      inp.dataset.idx = i;
      inp.addEventListener("input", onCellInput);
      inp.addEventListener("focus", () => { focusedCell = inp; markFocus(el); });
      inp.addEventListener("keydown", onCellKey);
      cell.appendChild(inp);
    }
    el.appendChild(cell);
  }
}

function markFocus(el) {
  el.querySelectorAll(".flap").forEach((f) => f.classList.remove("focused"));
  if (focusedCell) focusedCell.parentElement.classList.add("focused");
}

function onCellInput(e) {
  const inp = e.target;
  inp.value = (inp.value || "").toUpperCase();
  applyCellColor(inp);
  if (inp.value) {
    const next = inp.parentElement.nextElementSibling?.querySelector("input");
    if (next) { next.focus(); focusedCell = next; }
  }
}
function onCellKey(e) {
  const inp = e.target;
  const idx = +inp.dataset.idx;
  const grid = $("composeGrid");
  const goto = (j) => grid.querySelectorAll("input")[j]?.focus();
  if (e.key === "Backspace" && !inp.value) { e.preventDefault(); if (idx > 0) { goto(idx - 1); grid.querySelectorAll("input")[idx-1].value=""; applyCellColor(grid.querySelectorAll("input")[idx-1]); } }
  else if (e.key === "ArrowRight") goto(idx + 1);
  else if (e.key === "ArrowLeft") goto(idx - 1);
  else if (e.key === "ArrowDown") goto(idx + GRID.cols);
  else if (e.key === "ArrowUp") goto(idx - GRID.cols);
}
function applyCellColor(inp) {
  const cell = inp.parentElement;
  cell.className = classForChar(inp.value);
  cell.classList.toggle("focused", inp === focusedCell);
  // colour tiles (lowercase code) show no glyph
  inp.style.color = COLOR_CODES.includes(inp.value) ? "transparent" : "";
}

function composeString() {
  // Colour cells hold a lowercase code (r/o/y/g/b/p/w); send them as the emoji
  // tile so the server's uppercase→colour-code mapping preserves the colour.
  const inputs = $("composeGrid").querySelectorAll("input");
  let s = "";
  inputs.forEach((inp) => {
    const v = inp.value || " ";
    s += CODE_TO_EMOJI[v] || v;
  });
  return s;
}

// ---- live preview ----------------------------------------------------------
async function pollState() {
  try {
    const st = await api("/api/current_state");
    const board = $("preview");
    if (board.children.length !== st.chars.length) buildBoard(board, st.chars.length, GRID.cols, false);
    st.chars.forEach((ch, i) => {
      const cell = board.children[i];
      if (!cell) return;
      cell.className = classForChar(ch);
      cell.textContent = glyph(ch);
    });
    $("previewMeta").textContent =
      `${GRID.rows}×${GRID.cols} · ${st.module_count} modules · ${st.transport.type}` +
      (st.transport.last_error ? ` · ${st.transport.last_error}` : "");
    if (APPS.length) updateActiveUI(st.active_app, st.active_playlist);
  } catch (e) { /* transient */ }
}

async function pollStatus() {
  const dot = $("statusDot"), txt = $("statusText");
  try {
    const st = await api("/api/current_state");
    const t = st.transport;
    if (t.type === "sim") { dot.className = "dot warn"; txt.textContent = "simulation"; return; }
    if (t.connected) { dot.className = "dot ok"; txt.textContent = `${t.type} connected`; }
    else { dot.className = "dot err"; txt.textContent = `${t.type} offline`; }
  } catch { dot.className = "dot err"; txt.textContent = "companion error"; }
}

// ---- compose actions -------------------------------------------------------
async function send() {
  const btn = $("sendBtn"); btn.disabled = true;
  try {
    await post("/api/compose/send", {
      text: composeString(),
      style: $("styleSelect").value,
      speed: +$("speedInput").value,
    });
  } finally { setTimeout(() => (btn.disabled = false), 200); }
}
function clearGrid() {
  $("composeGrid").querySelectorAll("input").forEach((inp) => { inp.value = ""; applyCellColor(inp); });
  $("quickInput").value = "";
}
function quickFill() {
  const text = $("quickInput").value.toUpperCase();
  const inputs = $("composeGrid").querySelectorAll("input");
  inputs.forEach((inp, i) => { inp.value = text[i] || ""; applyCellColor(inp); });
}
function paintSwatch(code) {
  if (!focusedCell) focusedCell = $("composeGrid").querySelector("input");
  if (!focusedCell) return;
  focusedCell.value = code === " " ? "" : code;
  applyCellColor(focusedCell);
  const next = focusedCell.parentElement.nextElementSibling?.querySelector("input");
  if (next) { next.focus(); focusedCell = next; }
}

// ---- boot ------------------------------------------------------------------
async function bootGrid() {
  GRID = await api("/api/grid");
  buildBoard($("composeGrid"), GRID.module_count, GRID.cols, true);
  buildBoard($("preview"), GRID.module_count, GRID.cols, false);
  // style select
  const sel = $("styleSelect");
  sel.innerHTML = "";
  GRID.styles.forEach((s) => { const o = document.createElement("option"); o.value = s; o.textContent = s; sel.appendChild(o); });
  sel.value = GRID.display?.transition_style || "ltr";
  $("speedInput").value = GRID.display?.transition_speed ?? 15;
}

function buildPalette() {
  const pal = $("palette");
  COLOR_CODES.forEach((code) => {
    const b = document.createElement("button");
    b.className = "swatch"; b.style.background = `var(--${code})`;
    b.title = code.toUpperCase(); b.dataset.code = code;
    b.addEventListener("click", () => paintSwatch(code));
    pal.insertBefore(b, pal.querySelector(".swatch.blank"));
  });
  pal.querySelector(".swatch.blank").addEventListener("click", () => paintSwatch(" "));
}

function wireTabs() {
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      const tab = t.dataset.tab;
      ["apps", "compose", "playlists", "schedules", "triggers", "display"]
        .forEach((p) => $("page-" + p).classList.toggle("hidden", p !== tab));
      const loaders = { apps: loadApps, playlists: loadPlaylists, schedules: loadSchedules,
                        triggers: loadTriggers, display: loadDisplay };
      if (loaders[tab]) loaders[tab]();
    })
  );
}

// ---- apps ------------------------------------------------------------------
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
let APPS = [];

function appFits(a) {
  return (!a.min_rows || GRID.rows >= a.min_rows) && (!a.min_cols || GRID.cols >= a.min_cols);
}

async function loadApps() {
  const data = await api("/api/apps");
  APPS = data.apps;
  const grid = $("appsGrid");
  grid.innerHTML = "";
  APPS.forEach((a) => {
    const fits = appFits(a);
    const tile = el("div", "app-tile" + (fits ? "" : " disabled"));
    tile.dataset.appId = a.id;
    if (!fits) tile.title = `Needs at least ${a.min_rows || 1}×${a.min_cols || 1}`;
    tile.innerHTML =
      `<div class="app-icon">${a.icon || "🧩"}</div>` +
      `<div class="app-name">${a.name}</div>` +
      `<div class="app-desc">${a.description || ""}</div>` +
      (a.has_settings ? `<button class="app-gear" title="Settings">⚙</button>` : "") +
      `<span class="app-badge"></span>` +
      (fits ? "" : `<span class="app-req">${a.min_rows || 1}×${a.min_cols || 1}</span>`);
    tile.addEventListener("click", (e) => {
      if (e.target.closest(".app-gear")) { openAppSettings(a.id, a.name); return; }
      if (!fits) return;   // too big for this panel
      runApp(a.id);
    });
    grid.appendChild(tile);
  });
  updateActiveUI(data.active_app, data.active_playlist);
}

function updateActiveUI(activeApp, activePlaylist) {
  const banner = $("activeBanner");
  if (activeApp) {
    const a = APPS.find((x) => x.id === activeApp);
    $("activeAppName").textContent = a ? a.name : activeApp;
    banner.classList.remove("hidden");
  } else if (activePlaylist) {
    $("activeAppName").textContent = "Playlist · " + activePlaylist;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
  document.querySelectorAll(".app-tile").forEach((t) => {
    const on = t.dataset.appId === activeApp;
    t.classList.toggle("running", on);
    const badge = t.querySelector(".app-badge");
    if (badge) badge.textContent = on ? "▶ RUNNING" : "";
  });
}

async function runApp(id) { await post("/api/apps/run", { app: id }); updateActiveUI(id, null); }
async function stopApp() { await post("/api/apps/stop"); updateActiveUI(null, null); }

// ---- modal -----------------------------------------------------------------
function openModal(title, bodyEl, footButtons) {
  $("modalTitle").textContent = title;
  const body = $("modalBody"); body.innerHTML = ""; body.appendChild(bodyEl);
  const foot = $("modalFoot"); foot.innerHTML = "";
  footButtons.forEach((b) => foot.appendChild(b));
  $("modal").classList.remove("hidden");
}
function closeModal() { $("modal").classList.add("hidden"); }

// ---- app settings form -----------------------------------------------------
let _formFields = [];
function normOpts(options) {
  return (options || []).map((o) => (typeof o === "object" ? o : { value: o, label: String(o) }));
}
function findField(key) { return _formFields.find((w) => w._field.key === key); }
function currentValues() {
  const c = {};
  _formFields.forEach((w) => { const v = w._getValue && w._getValue(); if (v !== undefined) c[w._field.key] = v; });
  return c;
}
function chipLabel(v) { return String(v).includes("|") ? String(v).split("|").pop() : v; }

const COMPUTES = {
  polling_usage_estimate(cur) {
    const k = Object.keys(cur).find((x) => x.endsWith("polling_rate"));
    const r = Number(cur[k]) || 0;
    if (!r) return "Set a polling rate to estimate API usage.";
    const d = Math.round(86400 / r);
    return `≈ ${d.toLocaleString()} requests/day · ${(d * 30 / 1000).toFixed(1)}k/month`;
  },
};

function onFormChange() { applyVisibility(); recompute(); }
function recompute() {
  const cur = currentValues();
  _formFields.forEach((w) => {
    if (w._field.type === "computed" && w._computeEl) {
      const fn = COMPUTES[w._field.compute];
      w._computeEl.textContent = fn ? fn(cur, w._field) : "";
    }
  });
}
function applyVisibility() {
  const cur = currentValues();
  _formFields.forEach((w) => {
    const f = w._field;
    if (f.visible_when) {
      const show = Object.entries(f.visible_when).every(([k, v]) => String(cur[k]) === String(v));
      w.style.display = show ? "" : "none";
    }
    if (f.disabled_when) {
      const dis = Object.entries(f.disabled_when).every(([k, v]) => String(cur[k]) === String(v));
      const ctrl = w.querySelector("input,select,textarea");
      if (ctrl) ctrl.disabled = dis;
    }
  });
}

function applySync(f, newVal) {
  if (f.sync_values && f.sync_values[newVal]) {
    Object.entries(f.sync_values[newVal]).forEach(([tk, tv]) => {
      const tw = findField(tk); if (tw && tw._setValue) tw._setValue(tv);
    });
  }
  if (f.sync_parent) {
    const pw = findField(f.sync_parent);
    if (pw && pw._setValue) pw._setValue(f.sync_parent_custom_value || "custom");
  }
}

function mkField(f, values) {
  const wrap = el("div", "field"); wrap._field = f; wrap._getValue = () => undefined;
  const val = values[f.key];
  if (f.type === "notice") { const n = el("div", "notice"); n.textContent = f.label || f.text || ""; wrap.appendChild(n); return wrap; }
  if (f.type === "computed") {
    if (f.label) { const l = el("span"); l.textContent = f.label; wrap.appendChild(l); }
    const n = el("div", "notice"); wrap.appendChild(n); wrap._computeEl = n; return wrap;
  }
  const label = el("span"); label.innerHTML = f.label || f.key; wrap.appendChild(label);

  if (f.type === "toggle" || f.type === "select") {
    const opts = normOpts(f.options);
    if (f.type === "toggle") {
      const seg = el("div", "seg");
      const setOn = (v) => [...seg.children].forEach((c) => c.classList.toggle("on", c.dataset.value === String(v)));
      opts.forEach((o) => {
        const b = el("button"); b.type = "button"; b.textContent = o.label; b.dataset.value = o.value;
        b.addEventListener("click", () => { seg.dataset.value = o.value; setOn(o.value); applySync(f, o.value); onFormChange(); });
        seg.appendChild(b);
      });
      seg.dataset.value = val != null ? val : (opts[0]?.value ?? ""); setOn(seg.dataset.value);
      wrap.appendChild(seg);
      wrap._getValue = () => seg.dataset.value;
      wrap._setValue = (v) => { seg.dataset.value = v; setOn(v); };
    } else {
      const sel = el("select");
      opts.forEach((o) => { const op = el("option"); op.value = o.value; op.textContent = o.label; sel.appendChild(op); });
      sel.value = val != null ? val : (opts[0]?.value ?? "");
      sel.addEventListener("change", () => { applySync(f, sel.value); onFormChange(); });
      wrap.appendChild(sel);
      wrap._getValue = () => sel.value;
      wrap._setValue = (v) => { sel.value = v; };
    }
  } else if (f.type === "textarea") {
    const ta = el("textarea"); ta.rows = 3; ta.value = val ?? "";
    ta.addEventListener("input", onFormChange); wrap.appendChild(ta);
    wrap._getValue = () => ta.value; wrap._setValue = (v) => { ta.value = v; };
  } else if (f.type === "search_chips") {
    const box = el("div", "chip-search");
    const chipsDiv = el("div", "chips");
    let chips = val ? String(val).split(",").filter(Boolean).map((v) => ({ value: v, label: chipLabel(v) })) : [];
    const maxItems = f.maxItems || 99;
    const draw = () => {
      chipsDiv.innerHTML = "";
      chips.forEach((c, i) => {
        const ch = el("span", "chip"); ch.textContent = c.label;
        const x = el("button"); x.textContent = "✕"; x.onclick = () => { chips.splice(i, 1); draw(); onFormChange(); };
        ch.appendChild(x); chipsDiv.appendChild(ch);
      });
    };
    const search = el("input"); search.placeholder = "Search…";
    const results = el("div", "chip-results"); results.style.display = "none";
    let timer;
    search.addEventListener("input", () => {
      clearTimeout(timer); const q = search.value.trim();
      if (!q) { results.style.display = "none"; return; }
      timer = setTimeout(async () => {
        try {
          const data = await api(`${f.searchUrl}?q=${encodeURIComponent(q)}`);
          const items = data[f.resultKey] || [];
          results.innerHTML = "";
          items.forEach((it) => {
            const d = el("div"); d.textContent = it.label || it.name || it.value;
            d.onclick = () => {
              if (maxItems === 1) chips = [];
              if (chips.length < maxItems) { chips.push({ value: it.value ?? it.abbr ?? it.id, label: it.label || it.name || it.value }); draw(); onFormChange(); }
              search.value = ""; results.style.display = "none";
            };
            results.appendChild(d);
          });
          results.style.display = items.length ? "" : "none";
        } catch { results.style.display = "none"; }
      }, 250);
    });
    box.appendChild(chipsDiv); box.appendChild(search); box.appendChild(results); wrap.appendChild(box); draw();
    wrap._getValue = () => chips.map((c) => c.value).join(",");
    wrap._setValue = (v) => { chips = v ? String(v).split(",").filter(Boolean).map((x) => ({ value: x, label: chipLabel(x) })) : []; draw(); };
  } else {
    // text / number / password (+ optional stepper)
    const inp = el("input");
    inp.type = f.type === "password" ? "password" : f.type === "number" ? "number" : "text";
    if (f.min != null) inp.min = f.min;
    if (f.max != null) inp.max = f.max;
    if (f.step != null) inp.step = f.step;
    if (f.ph) inp.placeholder = f.ph;
    inp.value = val != null && val !== "" ? val : "";
    inp.addEventListener("input", onFormChange);
    wrap._getValue = () => (f.type === "number" ? Number(inp.value) : inp.value);
    wrap._setValue = (v) => { inp.value = v; };
    if (f.stepper) {
      const step = Number(f.step) || 1;
      const s = el("div", "stepper");
      const minus = el("button"); minus.type = "button"; minus.textContent = "−";
      const plus = el("button"); plus.type = "button"; plus.textContent = "+";
      minus.onclick = () => { inp.value = (Number(inp.value) || 0) - step; onFormChange(); };
      plus.onclick = () => { inp.value = (Number(inp.value) || 0) + step; onFormChange(); };
      s.appendChild(minus); s.appendChild(inp); s.appendChild(plus); wrap.appendChild(s);
    } else {
      wrap.appendChild(inp);
    }
  }
  return wrap;
}

async function openAppSettings(id, name) {
  const schema = await api(`/api/apps/${id}/settings`);
  const form = el("div");
  _formFields = [];
  schema.fields.forEach((f) => {
    const w = mkField(f, schema.values); _formFields.push(w); form.appendChild(w);
    if (f.inline_toggle) {
      const it = f.inline_toggle;
      const tw = mkField({ key: it.key, type: "toggle", label: "", options: it.options }, schema.values);
      _formFields.push(tw); form.appendChild(tw);
    }
  });
  onFormChange();
  const save = el("button", "btn primary"); save.textContent = "Save";
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  save.addEventListener("click", async () => {
    const values = {};
    _formFields.forEach((w) => { const v = w._getValue && w._getValue(); if (v !== undefined) values[w._field.key] = v; });
    msg.textContent = "Saving…";
    try { await post(`/api/apps/${id}/settings`, { values }); closeModal(); }
    catch (e) { msg.textContent = "Error: " + e.message; }
  });
  const close = el("button", "btn ghost"); close.textContent = "Close"; close.addEventListener("click", closeModal);
  openModal(`${schema.icon} ${name || schema.name}`, form, [msg, close, save]);
}

// ---- app library -----------------------------------------------------------
async function openLibrary() {
  const data = await api("/api/apps/available");
  const list = el("div");
  data.apps.forEach((a) => {
    const row = el("div", "lib-row");
    const btn = el("button", "btn btn-sm " + (a.installed ? "ghost" : "primary"));
    btn.textContent = a.installed ? "Remove" : "Add";
    btn.addEventListener("click", async () => {
      await post(`/api/apps/${a.id}/install`, { installed: !a.installed });
      openLibrary(); loadApps();
    });
    row.innerHTML = `<span class="app-icon" style="font-size:20px">${a.icon || "🧩"}</span>` +
      `<div class="lib-meta"><div class="lib-name">${a.name}</div><div class="lib-desc">${a.description || ""}</div></div>`;
    row.appendChild(btn);
    list.appendChild(row);
  });
  const close = el("button", "btn ghost"); close.textContent = "Close"; close.addEventListener("click", closeModal);
  openModal("App Library", list, [close]);
}

// ---- playlists -------------------------------------------------------------
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const rid = (p) => p + Math.random().toString(36).slice(2, 8);
let PL_ENTRIES = [];
let SAVED_PL = {};

function plRender() {
  const box = $("plEntries"); box.innerHTML = "";
  if (!PL_ENTRIES.length) box.innerHTML = '<span class="hint">Add an app or message.</span>';
  PL_ENTRIES.forEach((e, i) => {
    const row = el("div", "row-card");
    const tag = el("span", "handle"); tag.textContent = e.type === "app" ? "▸ App" : "▸ Msg"; row.appendChild(tag);
    if (e.type === "app") {
      const sel = el("select"); sel.className = "grow";
      APPS.forEach((a) => { const o = el("option"); o.value = a.id; o.textContent = `${a.icon} ${a.name}`; if (a.id === e.app) o.selected = true; sel.appendChild(o); });
      if (!e.app && APPS[0]) e.app = APPS[0].id;
      sel.onchange = () => (e.app = sel.value); row.appendChild(sel);
    } else {
      const inp = el("input"); inp.className = "grow"; inp.placeholder = "MESSAGE"; inp.value = e.text || "";
      inp.oninput = () => (e.text = inp.value); row.appendChild(inp);
    }
    const dur = el("input"); dur.type = "number"; dur.min = 1; dur.style.width = "70px"; dur.title = "seconds";
    dur.value = e.duration || 30; dur.oninput = () => (e.duration = Number(dur.value)); row.appendChild(dur);
    const del = el("button", "del"); del.textContent = "✕"; del.onclick = () => { PL_ENTRIES.splice(i, 1); plRender(); }; row.appendChild(del);
    box.appendChild(row);
  });
}
async function loadPlaylists() {
  if (!APPS.length) await loadApps();
  SAVED_PL = (await api("/api/playlists")).playlists || {};
  const saved = $("plSaved"); saved.innerHTML = "";
  const names = Object.keys(SAVED_PL);
  if (!names.length) { saved.innerHTML = '<span class="hint">None yet.</span>'; }
  names.forEach((n) => {
    const row = el("div", "saved-row");
    const nm = el("span", "grow"); nm.textContent = n; row.appendChild(nm);
    const run = el("button", "btn btn-sm primary"); run.textContent = "Run"; run.onclick = () => post("/api/playlists/run", { entries: SAVED_PL[n].entries, loop: SAVED_PL[n].loop !== false, name: n }); row.appendChild(run);
    const load = el("button", "btn btn-sm ghost"); load.textContent = "Load"; load.onclick = () => { PL_ENTRIES = JSON.parse(JSON.stringify(SAVED_PL[n].entries)); $("plLoop").checked = SAVED_PL[n].loop !== false; plRender(); }; row.appendChild(load);
    const del = el("button", "btn btn-sm ghost"); del.textContent = "Delete"; del.onclick = async () => { await fetch("/api/playlists/" + encodeURIComponent(n), { method: "DELETE" }); loadPlaylists(); }; row.appendChild(del);
    saved.appendChild(row);
  });
  if (!PL_ENTRIES.length) plRender();
}
async function runPlaylistNow() {
  if (!PL_ENTRIES.length) { $("plSaved"); return; }
  await post("/api/playlists/run", { entries: PL_ENTRIES, loop: $("plLoop").checked, name: "(unsaved)" });
}
async function savePlaylist() {
  const name = prompt("Playlist name:"); if (!name) return;
  await post("/api/playlists", { name, entries: PL_ENTRIES, loop: $("plLoop").checked }); loadPlaylists();
}

// ---- schedules -------------------------------------------------------------
let SCHEDS = [];
function buildDayPicker(container, selected) {
  container.innerHTML = "";
  DAYS.forEach((day) => {
    const l = el("label"); const cb = el("input"); cb.type = "checkbox"; cb.dataset.day = day; cb.checked = selected.includes(day);
    l.appendChild(cb); l.appendChild(document.createTextNode(day)); container.appendChild(l);
  });
}
const readDays = (c) => [...c.querySelectorAll("input")].filter((cb) => cb.checked).map((cb) => cb.dataset.day);

function schRender() {
  const box = $("schList"); box.innerHTML = "";
  if (!SCHEDS.length) box.innerHTML = '<span class="hint">No schedules.</span>';
  SCHEDS.forEach((s, i) => {
    const row = el("div", "row-card");
    const en = el("input"); en.type = "checkbox"; en.title = "enabled"; en.checked = s.enabled !== false; en.onchange = () => (s.enabled = en.checked); row.appendChild(en);
    const nm = el("input"); nm.className = "grow"; nm.placeholder = "Name"; nm.value = s.name || ""; nm.oninput = () => (s.name = nm.value); row.appendChild(nm);
    const st = el("input"); st.type = "time"; st.value = s.start_time || "08:00"; st.oninput = () => (s.start_time = st.value); row.appendChild(st);
    const et = el("input"); et.type = "time"; et.value = s.end_time || "17:00"; et.oninput = () => (s.end_time = et.value); row.appendChild(et);
    s.action = s.action || { type: "app", value: "" };
    const at = el("select"); ["off", "app", "playlist"].forEach((tp) => { const o = el("option"); o.value = tp; o.textContent = tp; if (s.action.type === tp) o.selected = true; at.appendChild(o); }); row.appendChild(at);
    const av = el("select");
    const fillAv = () => {
      av.innerHTML = ""; const tp = at.value;
      if (tp === "app") APPS.forEach((a) => { const o = el("option"); o.value = a.id; o.textContent = a.name; av.appendChild(o); });
      else if (tp === "playlist") Object.keys(SAVED_PL).forEach((n) => { const o = el("option"); o.value = n; o.textContent = n; av.appendChild(o); });
      av.style.display = tp === "off" ? "none" : "";
      if (s.action.value) av.value = s.action.value;
    };
    at.onchange = () => { s.action = { type: at.value, value: "" }; fillAv(); s.action.value = av.value; };
    av.onchange = () => (s.action = { type: at.value, value: av.value }); fillAv(); row.appendChild(av);
    const del = el("button", "del"); del.textContent = "✕"; del.onclick = () => { SCHEDS.splice(i, 1); schRender(); }; row.appendChild(del);
    const days = el("div", "days"); days.style.flexBasis = "100%";
    DAYS.forEach((d) => { const l = el("label"); const cb = el("input"); cb.type = "checkbox"; cb.checked = (s.days || []).includes(d); cb.onchange = () => { s.days = s.days || []; if (cb.checked) { if (!s.days.includes(d)) s.days.push(d); } else s.days = s.days.filter((x) => x !== d); }; l.appendChild(cb); l.appendChild(document.createTextNode(d)); days.appendChild(l); });
    row.appendChild(days); box.appendChild(row);
  });
}
async function loadSchedules() {
  if (!APPS.length) await loadApps();
  SAVED_PL = (await api("/api/playlists")).playlists || {};
  const d = await api("/api/schedules");
  $("qhEnabled").checked = d.quiet_hours_enabled; $("qhStart").value = d.quiet_hours_start; $("qhEnd").value = d.quiet_hours_end;
  buildDayPicker($("qhDays"), d.quiet_hours_days || []);
  SCHEDS = d.schedules || []; schRender();
}
function addSchedule() {
  SCHEDS.push({ id: rid("sch_"), name: "New", start_time: "08:00", end_time: "17:00", days: ["mon", "tue", "wed", "thu", "fri"], action: { type: "app", value: APPS[0]?.id || "" }, enabled: true });
  schRender();
}
async function saveSchedules() {
  SCHEDS.forEach((s) => { if (!s.id) s.id = rid("sch_"); });
  await post("/api/schedules", { schedules: SCHEDS, quiet_hours_enabled: $("qhEnabled").checked, quiet_hours_start: $("qhStart").value, quiet_hours_end: $("qhEnd").value, quiet_hours_days: readDays($("qhDays")) });
  $("schMsg").textContent = "Saved ✓"; setTimeout(() => ($("schMsg").textContent = ""), 2000);
}

// ---- triggers --------------------------------------------------------------
let TRIGS = [], TRIG_APPS = [];
function trigRender() {
  const box = $("trigList"); box.innerHTML = "";
  if (!TRIGS.length) box.innerHTML = '<span class="hint">No triggers yet.</span>';
  TRIGS.forEach((t, i) => {
    const row = el("div", "row-card");
    const en = el("input"); en.type = "checkbox"; en.checked = t.enabled !== false; en.onchange = () => (t.enabled = en.checked); row.appendChild(en);
    const app = TRIG_APPS.find((a) => a.id === t.app);
    const info = el("span", "grow"); info.textContent = app ? `${app.icon} ${app.name}` : t.app; row.appendChild(info);
    const nm = el("input"); nm.placeholder = "Label"; nm.value = t.name || ""; nm.oninput = () => (t.name = nm.value); row.appendChild(nm);
    const cd = el("input"); cd.type = "number"; cd.style.width = "76px"; cd.title = "cooldown (s)"; cd.value = t.cooldown || 300; cd.oninput = () => (t.cooldown = Number(cd.value)); row.appendChild(cd);
    const ds = el("input"); ds.type = "number"; ds.style.width = "68px"; ds.title = "show (s)"; ds.value = t.display_seconds || 30; ds.oninput = () => (t.display_seconds = Number(ds.value)); row.appendChild(ds);
    const del = el("button", "del"); del.textContent = "✕"; del.onclick = () => { TRIGS.splice(i, 1); trigRender(); }; row.appendChild(del);
    box.appendChild(row);
  });
}
async function loadTriggers() {
  const d = await api("/api/triggers");
  TRIGS = d.triggers || []; TRIG_APPS = d.trigger_apps || [];
  $("trigEnabled").checked = d.triggers_enabled !== false;
  const sel = $("trigAddApp"); sel.innerHTML = "";
  TRIG_APPS.forEach((a) => { const o = el("option"); o.value = a.id; o.textContent = `${a.icon} ${a.name}`; sel.appendChild(o); });
  trigRender();
}
function addTrigger() {
  const app = $("trigAddApp").value; if (!app) return;
  const meta = TRIG_APPS.find((a) => a.id === app) || {};
  TRIGS.push({ id: rid("trig_"), app, name: meta.name || app, enabled: true, cooldown: meta.trigger_cooldown || 300, display_seconds: meta.trigger_display_seconds || 30, conditions: {} });
  trigRender();
}
async function saveTriggers() {
  await post("/api/triggers", { triggers: TRIGS, triggers_enabled: $("trigEnabled").checked });
  $("trigMsg").textContent = "Saved ✓"; setTimeout(() => ($("trigMsg").textContent = ""), 2000);
}

// ---- display (proxied gateway UI) ------------------------------------------
async function loadDisplay() {
  const frame = $("gatewayFrame");
  if (!frame.src || frame.src.endsWith("about:blank")) frame.src = "/display/";
  try {
    const s = await api("/api/gateway/status");
    if (s.ok) { const d = s.data || {}; $("gwStatus").textContent = `online · ${d.modules ?? "?"} modules${d.ip ? " · " + d.ip : ""}`; }
    else $("gwStatus").textContent = "offline" + (s.error ? ": " + s.error : "");
  } catch { $("gwStatus").textContent = "error"; }
}

async function init() {
  const h = await api("/api/health"); $("version").textContent = "v" + h.version;
  wireTabs();
  buildPalette();
  await bootGrid();
  $("sendBtn").addEventListener("click", send);
  $("clearGridBtn").addEventListener("click", clearGrid);
  $("clearDisplayBtn").addEventListener("click", () => post("/api/display/clear"));
  $("quickInput").addEventListener("input", quickFill);
  $("stopAppBtn").addEventListener("click", stopApp);
  $("manageAppsBtn").addEventListener("click", openLibrary);
  $("modalClose").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  // playlists
  $("plAddApp").addEventListener("click", () => { PL_ENTRIES.push({ type: "app", app: APPS[0]?.id || "", duration: 30 }); plRender(); });
  $("plAddMsg").addEventListener("click", () => { PL_ENTRIES.push({ type: "compose", text: "", duration: 15 }); plRender(); });
  $("plRun").addEventListener("click", runPlaylistNow);
  $("plSave").addEventListener("click", savePlaylist);
  // schedules
  $("schAdd").addEventListener("click", addSchedule);
  $("schSave").addEventListener("click", saveSchedules);
  // triggers
  $("trigAdd").addEventListener("click", addTrigger);
  $("trigSave").addEventListener("click", saveTriggers);
  // display
  $("gwRefresh").addEventListener("click", loadDisplay);
  $("gwFullscreen").addEventListener("click", () => { const f = $("gatewayFrame"); if (f.requestFullscreen) f.requestFullscreen(); });
  await loadApps();
  pollState(); pollStatus();
  setInterval(pollState, 300);
  setInterval(pollStatus, 3000);
}

init();
