// SplitFlapGatewayCompanion — SPA (vanilla JS, no build step).
// Live preview + click-to-type compose grid + settings.

// Lowercase r/o/y/g/b/p/w are COLOUR flaps; uppercase letters are letters.
// Case-sensitive in the preview: 'y' = yellow tile, 'Y' = the letter Y.
const COLOR_CODES = ["r", "o", "y", "g", "b", "p", "w"];
const $ = (id) => document.getElementById(id);

let GRID = { rows: 3, cols: 15, module_count: 45, styles: [] };

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

function buildBoard(el, count, cols) {
  el.style.gridTemplateColumns = `repeat(${cols}, auto)`;
  el.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const cell = document.createElement("div");
    cell.className = "flap";
    el.appendChild(cell);
  }
}

// ---- live preview ----------------------------------------------------------
async function pollState() {
  try {
    const st = await api("/api/current_state");
    const board = $("preview");
    if (board.children.length !== st.chars.length) buildBoard(board, st.chars.length, GRID.cols);
    st.chars.forEach((ch, i) => {
      const cell = board.children[i];
      if (!cell) return;
      cell.className = classForChar(ch);
      cell.textContent = glyph(ch);
    });
    $("previewMeta").textContent = `${GRID.rows}×${GRID.cols} · ${st.module_count} modules`;
    if (APPS.length) updateActiveUI(st.active_app, st.active_playlist);
  } catch (e) { /* transient */ }
}

async function pollStatus() {
  // Nothing is shown while the display is reachable -- only a red banner if the
  // connection drops. No transport/technical wording surfaces in the UI.
  const badge = $("statusBadge");
  const down = () => { badge.className = "badge err"; badge.textContent = "⚠ Display offline"; };
  const ok = () => { badge.className = "badge hidden"; badge.textContent = ""; };
  try {
    const t = (await api("/api/current_state")).transport;
    if (t.connected || t.type === "sim") ok(); else down();
  } catch { down(); }
}

// ---- boot ------------------------------------------------------------------
async function bootGrid() {
  GRID = await api("/api/grid");
  buildBoard($("preview"), GRID.module_count, GRID.cols);
}

function wireTabs() {
  // Only local button-tabs switch panes; the gateway link-tabs (.tab.gw)
  // navigate to the gateway via their href.
  document.querySelectorAll(".tab[data-tab]").forEach((t) =>
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      const tab = t.dataset.tab;
      ["apps", "playlists", "triggers"]
        .forEach((p) => $("page-" + p).classList.toggle("hidden", p !== tab));
      const loaders = { apps: loadApps, playlists: loadPlaylists, triggers: loadTriggers };
      if (loaders[tab]) loaders[tab]();
    })
  );
}

// ---- apps ------------------------------------------------------------------
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
let APPS = [];

function appFits(a) {
  return (!a.min_rows || GRID.rows >= a.min_rows) &&
         (!a.min_cols || GRID.cols >= a.min_cols) &&
         (!a.min_modules || GRID.rows * GRID.cols >= a.min_modules);
}

// Short human label for an app's display requirement (shown on a disabled tile).
function appReq(a) {
  if (a.min_modules) return `${a.min_modules} modules`;
  return `${a.min_rows || 1}×${a.min_cols || 1}`;
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
    if (!fits) tile.title = `Needs at least ${appReq(a)}`;
    tile.innerHTML =
      `<div class="app-icon">${a.icon || "🧩"}</div>` +
      `<div class="app-name">${a.name}</div>` +
      `<div class="app-desc">${a.description || ""}</div>` +
      (a.has_settings ? `<button class="app-gear" title="Settings">⚙</button>` : "") +
      `<div class="app-foot">` +
        (a.i18n ? `<span class="app-i18n" title="Multilingual — adapts to the global Language">🌐</span>` : "") +
        `<span class="app-badge"></span>` +
        (fits ? "" : `<span class="app-req">${appReq(a)}</span>`) +
      `</div>`;
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
  if (f.shared) { const b = el("span", "shared-badge"); b.textContent = "shared"; b.title = "Global setting — shared across apps"; label.appendChild(b); }
  if (f.note) { const nt = el("small", "field-note"); nt.textContent = f.note; wrap.appendChild(nt); }

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
    // The results dropdown floats as a fixed overlay anchored under the input, so
    // it doesn't inflate the modal body's scroll height (which caused a second,
    // nested scrollbar). Position it on show and reposition on scroll/resize.
    const placeResults = () => {
      const r = search.getBoundingClientRect();
      results.style.left = r.left + "px";
      results.style.top = r.bottom + 2 + "px";
      results.style.width = r.width + "px";
    };
    const onReposition = () => placeResults();
    const showResults = () => {
      results.style.display = ""; placeResults();
      window.addEventListener("scroll", onReposition, true);
      window.addEventListener("resize", onReposition);
    };
    const hideResults = () => {
      results.style.display = "none";
      window.removeEventListener("scroll", onReposition, true);
      window.removeEventListener("resize", onReposition);
    };
    let timer;
    search.addEventListener("input", () => {
      clearTimeout(timer); const q = search.value.trim();
      if (!q) { hideResults(); return; }
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
              search.value = ""; hideResults();
            };
            results.appendChild(d);
          });
          if (items.length) showResults(); else hideResults();
        } catch { hideResults(); }
      }, 250);
    });
    search.addEventListener("blur", () => setTimeout(hideResults, 150));
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

// Per-playlist-entry settings: reuse the app's own settings form, but save the
// values into this entry's `overrides` (only what differs from the app's config),
// so the same app can appear multiple times configured differently.
async function openEntrySettings(entry) {
  const app = APPS.find((a) => a.id === entry.app);
  const schema = await api(`/api/apps/${entry.app}/settings`);
  const base = Object.assign({}, schema.values, entry.overrides || {});   // entry values win in the form
  const form = el("div");
  const note = el("p", "hint");
  note.textContent = "These apply to this playlist entry only. Unchanged fields follow the app's own settings.";
  form.appendChild(note);
  _formFields = [];
  schema.fields.forEach((f) => {
    if (f.key && f.key.startsWith("_globals_note_")) return;   // the shared-globals hint isn't overridable per entry
    const w = mkField(f, base); _formFields.push(w); form.appendChild(w);
    if (f.inline_toggle) {
      const it = f.inline_toggle;
      const tw = mkField({ key: it.key, type: "toggle", label: "", options: it.options }, base);
      _formFields.push(tw); form.appendChild(tw);
    }
  });
  onFormChange();
  const save = el("button", "btn primary"); save.textContent = "Save for this entry";
  const clear = el("button", "btn ghost"); clear.textContent = "Clear";
  clear.title = "Remove all per-entry overrides (follow the app's settings)";
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  clear.addEventListener("click", () => { entry.overrides = {}; closeModal(); plRender(); });
  save.addEventListener("click", () => {
    const ov = {};
    _formFields.forEach((w) => {
      const v = w._getValue && w._getValue();
      if (v === undefined) return;
      const k = w._field.key;
      if (String(v) !== String(schema.values[k] ?? "")) ov[k] = v;   // store only genuine overrides
    });
    entry.overrides = ov;
    closeModal(); plRender();
  });
  openModal(`${app ? app.icon + " " : ""}${app ? app.name : entry.app} — entry settings`, form, [msg, clear, save]);
}

async function openGlobalSettings() {
  const schema = await api("/api/global-settings");
  const form = el("div", "gsettings");
  if (!schema.fields.length) {
    const p = el("p", "hint"); p.textContent = "No global settings yet — install apps that use shared settings (weather, stocks, …).";
    form.appendChild(p);
  }
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
    try { await post("/api/global-settings", { values }); closeModal(); }
    catch (e) { msg.textContent = "Error: " + e.message; }
  });
  const close = el("button", "btn ghost"); close.textContent = "Close"; close.addEventListener("click", closeModal);
  openModal("⚙ Global settings", form, [msg, close, save]);
}

// ---- app library -----------------------------------------------------------
async function uploadApp(fileInput, msgEl) {
  const f = fileInput.files[0];
  msgEl.style.whiteSpace = "pre-wrap";   // keep line breaks in rejection reasons
  if (!f) { msgEl.textContent = "Choose a .zip first."; return; }
  msgEl.textContent = "Uploading…";
  try {
    const fd = new FormData(); fd.append("file", f);
    const r = await fetch("/api/apps/upload", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { msgEl.textContent = "Error: " + (j.detail || r.status); return; }
    fileInput.value = "";
    openLibrary(); loadApps();
  } catch (e) { msgEl.textContent = "Error: " + e.message; }
}

async function openLibrary() {
  const data = await api("/api/apps/available");
  const wrap = el("div");

  // Upload a new app
  const box = el("div", "upload-box");
  box.innerHTML = '<div class="lib-name">Upload an app</div>' +
    '<div class="hint" style="margin:3px 0 8px">A <b>.zip</b> containing the app folder ' +
    '(<code>manifest.json</code> + <code>app.py</code>, or <code>data.json</code> for a channel app). ' +
    'Functional apps run Python on this host — only upload apps you trust.</div>';
  const inp = el("input"); inp.type = "file"; inp.accept = ".zip"; inp.style.width = "auto";
  const ub = el("button", "btn btn-sm primary"); ub.textContent = "Upload";
  const um = el("span", "hint");
  const urow = el("div", "inline-row"); urow.append(inp, ub, um);
  ub.addEventListener("click", () => uploadApp(inp, um));
  box.appendChild(urow); wrap.appendChild(box);

  // Library list
  const list = el("div"); list.style.marginTop = "12px";
  data.apps.forEach((a) => {
    const row = el("div", "lib-row");
    const tag = a.builtin ? "" : ' <small style="color:var(--brand)">· uploaded</small>';
    const i18nTag = a.i18n ? ' <span title="Multilingual — adapts to the global Language">🌐</span>' : "";
    row.innerHTML = `<span class="app-icon" style="font-size:20px">${a.icon || "🧩"}</span>` +
      `<div class="lib-meta"><div class="lib-name">${a.name}${i18nTag}${tag}</div><div class="lib-desc">${a.description || ""}</div></div>`;
    const btn = el("button", "btn btn-sm " + (a.installed ? "ghost" : "primary"));
    btn.textContent = a.installed ? "Remove" : "Add";
    btn.addEventListener("click", async () => {
      await post(`/api/apps/${a.id}/install`, { installed: !a.installed });
      openLibrary(); loadApps();
    });
    row.appendChild(btn);
    if (!a.builtin) {
      const dl = el("button", "btn btn-sm"); dl.textContent = "🗑"; dl.title = "Delete uploaded app";
      dl.style.background = "var(--hi)";
      dl.addEventListener("click", async () => {
        if (!confirm(`Delete "${a.name}"? This removes the uploaded app for good.`)) return;
        await fetch(`/api/apps/${encodeURIComponent(a.id)}`, { method: "DELETE" });
        openLibrary(); loadApps();
      });
      row.appendChild(dl);
    }
    list.appendChild(row);
  });
  wrap.appendChild(list);

  const close = el("button", "btn ghost"); close.textContent = "Close"; close.addEventListener("click", closeModal);
  openModal("App Library", wrap, [close]);
}

// ---- playlists -------------------------------------------------------------
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
      APPS.forEach((a) => { const o = el("option"); o.value = a.id; o.textContent = `${a.icon} ${a.name}${a.i18n ? " 🌐" : ""}`; if (a.id === e.app) o.selected = true; sel.appendChild(o); });
      if (!e.app && APPS[0]) e.app = APPS[0].id;
      sel.onchange = () => { e.app = sel.value; e.overrides = {}; plRender(); }; row.appendChild(sel);
      // Per-entry settings: override this entry's config (location/units/language…)
      // independently, so the same app can appear more than once configured differently.
      const nOv = Object.keys(e.overrides || {}).length;
      const cfg = el("button", "del"); cfg.textContent = nOv ? `⚙ ${nOv}` : "⚙";
      cfg.title = nOv ? `${nOv} setting(s) overridden for this entry` : "Settings for this entry";
      if (nOv) cfg.style.color = "var(--brand)";
      cfg.onclick = () => openEntrySettings(e);
      row.appendChild(cfg);
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

// ---- gateway link-tabs (unified nav) ---------------------------------------
async function setupGatewayTabs() {
  let url = "";
  try { url = (await api("/api/gateway/status")).url || ""; } catch {}
  const base = url.replace(/\/$/, "");
  document.querySelectorAll(".tab.gw").forEach((a) => {
    if (base) a.href = `${base}/#${a.dataset.gw}`;
    else a.classList.add("disabled");
  });
}

// Deep-link: open the local tab named in the URL hash (e.g. companion/#playlists).
function openTabFromHash() {
  const h = (location.hash || "").replace("#", "");
  const btn = document.querySelector(`.tab[data-tab="${h}"]`);
  if (btn) btn.click();
}

// ---- developer mode (env-gated: COMPANION_DEV_MODE) ------------------------
function updateDevBtn(st) {
  const b = $("devBtn");
  b.textContent = st && st.sim_mode ? "⚙ Dev · SIM" : "⚙ Dev";
  b.classList.toggle("warn", !!(st && st.sim_mode));
}

async function refreshGridUI() {
  await bootGrid();
  const active = document.querySelector(".tab.active");
  if (!active || active.dataset.tab === "apps") loadApps();
}

async function openDevMenu() {
  const wrap = el("div", "gsettings");

  const render = (st) => {
    updateDevBtn(st);
    wrap.innerHTML = "";

    // 1) Simulation mode
    const simF = el("div", "field");
    const simLbl = el("label"); simLbl.style.cssText = "display:flex;align-items:center;gap:8px;font-weight:600";
    const sim = el("input"); sim.type = "checkbox"; sim.checked = st.sim_mode; sim.style.width = "auto";
    simLbl.appendChild(sim);
    simLbl.appendChild(document.createTextNode("Simulation mode"));
    simF.appendChild(simLbl);
    const simNote = el("small", "field-note");
    simNote.textContent = st.sim_mode
      ? "Simulating — nothing is sent to the physical display (the live preview still updates)."
      : "Live — frames are sent to the display. Turn on to test apps without touching the wall.";
    simF.appendChild(simNote);
    sim.addEventListener("change", async () => {
      sim.disabled = true;
      try { render(await post("/api/dev/sim", { on: sim.checked })); await refreshGridUI(); }
      catch (e) { simNote.textContent = "Failed: " + e.message; sim.disabled = false; }
    });
    wrap.appendChild(simF);

    // 2) Force resync with the gateway
    const reF = el("div", "field");
    const reLbl = el("span"); reLbl.textContent = "Gateway sync"; reLbl.style.fontWeight = "600"; reF.appendChild(reLbl);
    const reRow = el("div"); reRow.style.cssText = "display:flex;align-items:center;gap:10px;margin-top:6px";
    const reBtn = el("button", "btn ghost btn-sm"); reBtn.textContent = "↻ Force resync";
    const reMsg = el("small", "field-note"); reMsg.textContent = "Pull grid geometry + MQTT settings from the gateway now.";
    reBtn.addEventListener("click", async () => {
      reBtn.disabled = true; reMsg.textContent = "Syncing…";
      try { const r = await post("/api/dev/resync", {}); reMsg.textContent = r.ok ? "Resynced ✓" : "Failed: " + (r.error || "unknown"); }
      catch (e) { reMsg.textContent = "Failed: " + e.message; }
      reBtn.disabled = false;
      render(await api("/api/dev")); await refreshGridUI();
    });
    reRow.appendChild(reBtn); reRow.appendChild(reMsg); reF.appendChild(reRow);
    wrap.appendChild(reF);

    // 3) Grid geometry override (simulation only)
    const gF = el("div", "field");
    const gLbl = el("span"); gLbl.textContent = "Grid geometry override"; gLbl.style.fontWeight = "600"; gF.appendChild(gLbl);
    const gNote = el("small", "field-note");
    gNote.textContent = st.sim_mode
      ? `Now ${st.grid.rows}×${st.grid.cols}` + (st.grid_overridden ? " (overridden)" : "") + ` · gateway is ${st.gateway_grid.rows}×${st.gateway_grid.cols}.`
      : "Turn on simulation mode to override rows/cols (the real display's geometry is never touched).";
    gF.appendChild(gNote);
    const gRow = el("div"); gRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:6px";
    const rows = el("input"); rows.type = "number"; rows.min = 1; rows.max = 20; rows.value = st.grid.rows; rows.style.width = "64px";
    const cols = el("input"); cols.type = "number"; cols.min = 1; cols.max = 40; cols.value = st.grid.cols; cols.style.width = "64px";
    const applyBtn = el("button", "btn ghost btn-sm"); applyBtn.textContent = "Apply";
    [rows, cols, applyBtn].forEach((x) => (x.disabled = !st.sim_mode));
    gRow.appendChild(document.createTextNode("Rows")); gRow.appendChild(rows);
    gRow.appendChild(document.createTextNode("Cols")); gRow.appendChild(cols);
    gRow.appendChild(applyBtn);
    applyBtn.addEventListener("click", async () => {
      applyBtn.disabled = true;
      try { render(await post("/api/dev/grid", { rows: Number(rows.value), cols: Number(cols.value) })); await refreshGridUI(); }
      catch (e) { gNote.textContent = "Failed: " + e.message; applyBtn.disabled = false; }
    });
    gF.appendChild(gRow);
    wrap.appendChild(gF);
  };

  render(await api("/api/dev"));
  const close = el("button", "btn"); close.textContent = "Close"; close.addEventListener("click", closeModal);
  openModal("Developer", wrap, [close]);
}

async function init() {
  const h = await api("/api/health"); $("version").textContent = "v" + h.version;
  wireTabs();
  await bootGrid();
  $("stopAppBtn").addEventListener("click", stopApp);
  $("manageAppsBtn").addEventListener("click", openLibrary);
  $("globalSettingsBtn").addEventListener("click", openGlobalSettings);
  // Developer menu — only when the companion was started with COMPANION_DEV_MODE.
  try {
    const dev = await api("/api/dev");
    if (dev.enabled) {
      updateDevBtn(dev);
      $("devBtn").classList.remove("hidden");
      $("devBtn").addEventListener("click", openDevMenu);
    }
  } catch { /* dev endpoint unavailable */ }
  $("modalClose").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  // playlists
  $("plAddApp").addEventListener("click", () => { PL_ENTRIES.push({ type: "app", app: APPS[0]?.id || "", duration: 30 }); plRender(); });
  $("plAddMsg").addEventListener("click", () => { PL_ENTRIES.push({ type: "compose", text: "", duration: 15 }); plRender(); });
  $("plRun").addEventListener("click", runPlaylistNow);
  $("plSave").addEventListener("click", savePlaylist);
  // triggers
  $("trigAdd").addEventListener("click", addTrigger);
  $("trigSave").addEventListener("click", saveTriggers);
  await loadApps();
  setupGatewayTabs();
  openTabFromHash();
  window.addEventListener("hashchange", openTabFromHash);
  pollState(); pollStatus();
  setInterval(pollState, 300);
  setInterval(pollStatus, 3000);
}

init();
