// SplitFlapGatewayCompanion — SPA (vanilla JS, no build step).
// Live preview + click-to-type compose grid + settings.

// Lowercase r/o/y/g/b/p/w are COLOUR flaps; uppercase letters are letters.
// Case-sensitive in the preview: 'y' = yellow tile, 'Y' = the letter Y.
// A colour flap is its own codepoint (U+E000..U+E006), not the letter r/o/y/g/b/p/w.
// It has to be: a wall that can show lowercase can show the LETTER r, and colouring every
// `o` was how "Hello" came out with an orange flap in the middle of it. The server sends
// the same sentinel it sends the wall (renderer.COLOR_PUA).
const COLOR_CODES = ["r", "o", "y", "g", "b", "p", "w"];
const COLOR_PUA = {};
COLOR_CODES.forEach((c, i) => { COLOR_PUA[String.fromCharCode(0xe000 + i)] = c; });
const $ = (id) => document.getElementById(id);

let GRID = { rows: 3, cols: 15, module_count: 45, styles: [] };

// Every server URL goes through url(). As a Home Assistant add-on the SPA is served
// under an ingress prefix (/api/hassio_ingress/<token>/), which the server stamps
// into the shell as window.__BASE__ — a bare "/api/..." would resolve against the HA
// root and 404. Empty (so a no-op) everywhere else.
const BASE = window.__BASE__ || "";
// ---- which wall are we driving? ---------------------------------------------
// The client-side twin of the server's display_for(): every /api/ call carries the
// active display, so switching walls is ONE variable rather than a change at each of
// the ~40 call sites. Anything not under /api/ is left alone — the gateway proxy
// addresses a display by PATH (/gw/<id>/), because it rewrites the proxied page's own
// links and a query param would be lost on the first click inside it.
let DISPLAY = "";                 // active display id ("" until we've loaded them)
let RICH = false;                 // can THIS wall show lowercase? (a Matrix Portal can)
let DISPLAYS = [];                // [{id, name, grid, module_count, ...}]
let DEFAULT_DISPLAY = "default";

const url = (path) => {
  const u = BASE + path;
  if (!DISPLAY || !path.startsWith("/api/")) return u;
  return u + (u.includes("?") ? "&" : "?") + "display=" + encodeURIComponent(DISPLAY);
};

// The gateway proxy for the wall we are on (see gwproxy.py).
const gwUrl = () => url("/gw/" + (DISPLAY && DISPLAY !== DEFAULT_DISPLAY ? DISPLAY + "/" : ""));

// ---- i18n (chrome language) --------------------------------------------------
// The server resolves the viewer's UI language per request (uilang.py: URL param >
// explicitly-saved Language setting > COMPANION_UI_LANGUAGE > Accept-Language) and
// stamps it as window.__LANG__. The English string IS the catalog key: t("Save")
// returns "Enregistrer" when /i18n/fr.json carries it and "Save" itself otherwise,
// so a missing translation can never break the UI. Catalogs degrade exact locale
// -> base language ("fr-BE" -> fr.json); English skips the fetch entirely.
// The server picked this from the URL param, an explicitly-saved Language setting,
// the ui_language option, or (failing all three) the browser. __LOCKED__ says one of
// the first three decided it, so nothing here may override it.
let LANG = window.__LANG__ || "en-US";
const LANGS = window.__LANGS__ || [];
let STR = {};

// Home Assistant's own language, for the signed-in HA user.
//
// HA never gives an add-on the user's profile language: no Supervisor endpoint has
// it, no ingress header carries it, and the core API knows only the *system*
// language. But the ingress page is served from HA's own origin, so the parent
// document (the HA frontend) is same-origin and readable — and it advertises the
// active language on <html lang> (and keeps it in localStorage). That is the real,
// per-user answer. Everything here is guarded: outside HA there is no parent frame,
// and a future HA that isolates the iframe just throws, which lands us back on the
// browser's language.
function haLanguage() {
  try {
    if (window.parent === window) return "";        // not embedded — nothing to ask
    const doc = window.parent.document;             // throws if not same-origin
    const attr = doc.documentElement.getAttribute("lang");
    if (attr) return attr;
    const saved = window.parent.localStorage.getItem("selectedLanguage");
    return saved ? String(JSON.parse(saved)) : "";
  } catch {
    return "";                                      // cross-origin / blocked: fine
  }
}

// Map any language code onto one we actually offer: exact match first, then the base
// language (HA's "fr" -> our "fr"; HA's "pt-BR" -> "pt-BR"; HA's "nb" -> nothing).
function offered(code) {
  if (!code) return "";
  const c = String(code).replace("_", "-").toLowerCase();
  const exact = LANGS.find((l) => l.toLowerCase() === c);
  if (exact) return exact;
  const base = c.split("-")[0];
  return LANGS.find((l) => l.toLowerCase().split("-")[0] === base) || "";
}
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function t(s, ...args) {
  let out = STR[s] || s;
  args.forEach((a) => { out = out.replace("%s", a); });
  return out;
}
async function loadI18n() {
  const base = LANG.split("-")[0].toLowerCase();
  if (base === "en") return;
  for (const code of [...new Set([LANG, base])]) {
    try {
      const r = await fetch(url(`/i18n/${code}.json`));
      if (r.ok) { STR = await r.json(); return; }
    } catch { /* try the next */ }
  }
}
// One pass over the static shell: data-i18n (text), data-i18n-title, data-i18n-label.
function translateDom() {
  document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
  document.querySelectorAll("[data-i18n-title]").forEach((n) => { n.title = t(n.dataset.i18nTitle); });
  document.querySelectorAll("[data-i18n-label]").forEach((n) => n.setAttribute("aria-label", t(n.dataset.i18nLabel)));
  document.querySelectorAll("[data-i18n-placeholder]").forEach((n) => { n.placeholder = t(n.dataset.i18nPlaceholder); });
}

async function api(path, opts) {
  const r = await fetch(url(path), opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.status === 204 ? null : r.json();
}
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

// ---- rendering helpers -----------------------------------------------------
function classForChar(ch) {
  const c = COLOR_PUA[ch];
  return c ? `flap color-${c}` : "flap";
}
function glyph(ch) {
  // colour codes (lowercase) render as an empty coloured tile
  return COLOR_PUA[ch] ? "" : (ch || "");
}

function buildBoard(board, count, cols) {
  // Not named `el`: that is the global element-builder helper, and a parameter of the same
  // name shadows it — harmless here only because nothing inside calls el(), which makes it
  // a trap for whoever writes the next line rather than a bug today.
  //
  // The board lays itself out from --cols: CSS derives one --flap size from it and the
  // width available, so a 15- (or 22-) wide wall fits a phone as well as a desk. See
  // .board / .flap in styles.css.
  board.style.setProperty("--cols", cols);
  board.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const cell = document.createElement("div");
    cell.className = "flap";
    board.appendChild(cell);
  }
}

// ---- live preview ----------------------------------------------------------
async function pollState() {
  try {
    const st = await api("/api/current_state");
    const board = $("preview");
    // The wall can change shape under us: the gateway may have been unreachable at
    // boot, or its Display Layout edited since. Re-read the geometry rather than
    // reusing a stale GRID.cols, which would lay 75 cells out 15-wide by luck alone.
    if (st.module_count && st.module_count !== GRID.module_count) {
      await bootGrid();          // refetches rows/cols and rebuilds both boards
      loadApps();                // min_rows/min_cols gating changes with the grid
    }
    if (board.children.length !== st.chars.length) buildBoard(board, st.chars.length, GRID.cols);
    st.chars.forEach((ch, i) => {
      const cell = board.children[i];
      if (!cell) return;
      cell.className = classForChar(ch);
      cell.textContent = glyph(ch);
    });
    $("previewMeta").textContent = `${GRID.rows}×${GRID.cols} · ${st.module_count} ${t("modules")}`;
    if (APPS.length) updateActiveUI(st.active_app, st.active_playlist);
  } catch (e) { /* transient */ }
}

async function pollStatus() {
  // Nothing is shown while the display is reachable -- only a red banner if the
  // connection drops. No transport/technical wording surfaces in the UI.
  const badge = $("statusBadge");
  const down = () => { badge.className = "badge err"; badge.textContent = t("⚠ Display offline"); };
  const ok = () => { badge.className = "badge hidden"; badge.textContent = ""; };
  try {
    const tr = (await api("/api/current_state")).transport;
    if (tr.connected || tr.type === "sim") ok(); else down();
  } catch { down(); }
}

// ---- compose ---------------------------------------------------------------
// A cell holds one of: "" (blank), a typed character, or a COLOUR EMOJI.
//
// Colours are held as the emoji tile, not as the r/o/y/g/b/p/w code, and that is
// load-bearing. The server's normalize() uppercases the text BEFORE mapping emoji
// to colour codes, so a bare lowercase "r" would come back as the LETTER R and the
// cell would show an R instead of turning red. Going through the emoji is the only
// representation that survives the round trip -- and it is also what lets a typed
// "r" stay a letter, which is the whole point of the case distinction the preview
// relies on ('y' = yellow tile, 'Y' = the letter Y).
let CMP = [];              // one entry per module, row-major
let CMP_AT = 0;            // focused cell
let EMOJI2CODE = {};       // 🟥 -> r      (from /api/grid)
const isEmoji = (v) => Object.prototype.hasOwnProperty.call(EMOJI2CODE, v);

// Uppercase the way the server will (renderer.fold / cp1252_upper): never let a glyph
// expand, so "ß" stays "ß" instead of becoming "SS" and silently eating a cell.
//
// …and ONLY when the wall would. A Matrix Portal has lowercase flaps, so shouting the
// editor back at someone who typed "Hello" made the preview lie about what the wall is
// about to show. RICH is the active display's capability (see loadDisplays).
function cmpUpper(ch) {
  if (RICH) return ch;
  const u = ch.toUpperCase();
  return u.length === 1 ? u : ch;
}

function cmpRender() {
  const board = $("composeBoard");
  CMP.forEach((v, i) => {
    const cell = board.children[i];
    if (!cell) return;
    if (isEmoji(v)) {
      cell.className = `flap color-${EMOJI2CODE[v]}`;
      cell.textContent = "";
    } else {
      cell.className = "flap";
      cell.textContent = v ? cmpUpper(v) : "";
    }
    cell.classList.add("cmp-cell");
    if (i === CMP_AT) cell.classList.add("cmp-at");
  });
}

function cmpFocus(i) {
  const n = GRID.module_count;
  CMP_AT = ((i % n) + n) % n;      // wrap, never go out of range
  cmpRender();
}

function cmpSet(v, advance = true) {
  CMP[CMP_AT] = v;
  if (advance) cmpFocus(CMP_AT + 1); else cmpRender();
}

function cmpKey(e) {
  const cols = GRID.cols;
  const k = e.key;
  if (k === "ArrowRight")      cmpFocus(CMP_AT + 1);
  else if (k === "ArrowLeft")  cmpFocus(CMP_AT - 1);
  else if (k === "ArrowDown")  cmpFocus(CMP_AT + cols);
  else if (k === "ArrowUp")    cmpFocus(CMP_AT - cols);
  else if (k === "Home")       cmpFocus(CMP_AT - (CMP_AT % cols));
  else if (k === "End")        cmpFocus(CMP_AT - (CMP_AT % cols) + cols - 1);
  else if (k === "Enter")      cmpFocus(CMP_AT - (CMP_AT % cols) + cols);
  else if (k === "Backspace") { cmpFocus(CMP_AT - 1); CMP[CMP_AT] = ""; cmpRender(); }
  else if (k === "Delete")     cmpSet("", false);
  else if (k === " ")          cmpSet("");            // space blanks and advances
  else if (k.length === 1)     cmpSet(k);             // any single printable char
  else return;                                        // leave modifiers etc. alone
  e.preventDefault();
}

// Flatten to the flat, row-major string the server expects: normalize() pads or
// truncates to exactly module_count, so the grid maps 1:1 with no wrapping.
function cmpText() {
  const { rows, cols } = GRID;
  const out = [];
  for (let r = 0; r < rows; r++) {
    let row = CMP.slice(r * cols, (r + 1) * cols).map((v) => v || " ");
    if ($("cmpCenter").checked) {
      // Centre what was typed within the row, without disturbing the grid itself.
      let a = 0, b = row.length;
      while (a < b && row[a] === " ") a++;
      while (b > a && row[b - 1] === " ") b--;
      const inner = row.slice(a, b);
      if (inner.length) {
        const pad = Math.floor((cols - inner.length) / 2);
        row = Array(pad).fill(" ").concat(inner, Array(cols - pad - inner.length).fill(" "));
      }
    }
    out.push(row.join(""));
  }
  return out.join("");
}

function cmpBuild() {
  const board = $("composeBoard");
  buildBoard(board, GRID.module_count, GRID.cols);
  CMP = Array(GRID.module_count).fill("");
  CMP_AT = 0;
  [...board.children].forEach((cell, i) =>
    cell.addEventListener("click", () => { board.focus(); cmpFocus(i); }));

  // Swatches come from the server's COLOR_MAP so the two can never drift apart.
  EMOJI2CODE = GRID.color_map || {};
  const sw = $("cmpSwatches");
  sw.innerHTML = "";
  Object.keys(EMOJI2CODE).forEach((emoji) => {
    const code = EMOJI2CODE[emoji];
    const b = el("button", "swatch");
    // The map's blank entry (⬛ -> " ") is a blank flap, not a colour.
    const blank = code.trim() === "";
    b.classList.add(blank ? "swatch-blank" : `color-${code}`);
    b.title = blank ? t("Blank") : t("Colour %s", code);
    b.addEventListener("click", () => {
      $("composeBoard").focus();
      cmpSet(blank ? "" : emoji);
    });
    sw.appendChild(b);
  });

  // Style + speed default to the display's globals; "" means "let the server decide".
  const sel = $("cmpStyle");
  sel.innerHTML = "";
  const d = el("option"); d.value = ""; d.textContent = t("Default (global)");
  sel.appendChild(d);
  (GRID.styles || []).forEach((s) => {
    const o = el("option"); o.value = s; o.textContent = s; sel.appendChild(o);
  });
  const disp = GRID.display || {};
  $("cmpSpeed").value = disp.transition_speed ?? 15;
  sel.addEventListener("change", () => {
    // 'slot' is the spin effect and is paced by its own, much slower global.
    $("cmpSpeed").value = sel.value === "slot"
      ? (disp.slot_speed ?? 80)
      : (disp.transition_speed ?? 15);
  });

  board.addEventListener("keydown", cmpKey);
  cmpRender();
}

async function cmpPush() {
  const msg = $("cmpMsg");
  const btn = $("cmpPush");
  const style = $("cmpStyle").value;
  const speed = parseInt($("cmpSpeed").value, 10);
  btn.disabled = true;
  msg.textContent = t("Sending…");
  try {
    // The engine renders this to frames and pushes the whole page in ONE call to
    // the gateway's /api/rs485/batch, with speed as step_ms. That endpoint exists
    // for exactly this: one request per page, not one per module.
    await post("/api/compose/send", {
      text: cmpText(),
      style: style || null,
      speed: Number.isFinite(speed) ? speed : null,
    });
    msg.textContent = t("Sent.");
  } catch (e) {
    msg.textContent = t("Failed: %s", e.message);
  } finally {
    btn.disabled = false;
    setTimeout(() => { msg.textContent = ""; }, 2500);
  }
}

function cmpClear() {
  CMP = Array(GRID.module_count).fill("");
  cmpFocus(0);
}

// ---- boot ------------------------------------------------------------------
async function bootGrid() {
  GRID = await api("/api/grid");
  buildBoard($("preview"), GRID.module_count, GRID.cols);
  cmpBuild();
}

function wireTabs() {
  const nav = $("nav"), toggle = $("navToggle"), current = $("navCurrent");
  const closeMenu = () => {
    nav.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
  };

  // On a phone the strip is collapsed behind ☰ (styles.css); on a wide screen the
  // button is hidden and this never runs. The label carries the tab you're on, so the
  // collapsed menu still says where you are.
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  // Delegated: the gateway's tabs are appended at runtime, after this wiring. They
  // navigate away, but the menu should still shut behind them.
  nav.addEventListener("click", (e) => { if (e.target.closest(".tab.gw")) closeMenu(); });

  // Only local button-tabs switch panes; the gateway link-tabs (.tab.gw)
  // navigate to the gateway via their href.
  document.querySelectorAll(".tab[data-tab]").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.dataset.tab;
      ["apps", "compose", "playlists", "triggers"]
        .forEach((p) => $("page-" + p).classList.toggle("hidden", p !== tab));
      const loaders = { apps: loadApps, playlists: loadPlaylists, triggers: loadTriggers };
      if (loaders[tab]) loaders[tab]();
      if (tab === "compose") $("composeBoard").focus();
      current.textContent = btn.textContent;
      closeMenu();
    })
  );
  const active = document.querySelector(".tab[data-tab].active");
  if (active) current.textContent = active.textContent;
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
  if (a.min_modules) return t("%s modules", a.min_modules);
  return `${a.min_rows || 1}×${a.min_cols || 1}`;
}

async function loadApps() {
  const data = await api(`/api/apps?lang=${LANG}`);
  APPS = data.apps;
  const grid = $("appsGrid");
  grid.innerHTML = "";
  APPS.forEach((a) => {
    const fits = appFits(a);
    const tile = el("div", "app-tile" + (fits ? "" : " disabled"));
    tile.dataset.appId = a.id;
    if (!fits) tile.title = t("Needs at least %s", appReq(a));
    tile.innerHTML =
      `<div class="app-icon">${a.icon || "🧩"}</div>` +
      `<div class="app-name">${a.name}</div>` +
      `<div class="app-desc">${a.description || ""}</div>` +
      (a.has_settings ? `<button class="app-gear" title="${t("Settings")}">⚙</button>` : "") +
      `<div class="app-foot">` +
        (a.i18n ? `<span class="app-i18n" title="${t("Multilingual — adapts to the global Language")}">🌐</span>` : "") +
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
  // Word order differs per language ("X is running" / "X läuft"), so the whole
  // sentence is one catalog key with the app name spliced in bold.
  const showRunning = (label) => {
    $("activeText").innerHTML = t("▶ %s is running").replace("%s", `<b>${esc(label)}</b>`);
    banner.classList.remove("hidden");
  };
  if (activeApp) {
    const a = APPS.find((x) => x.id === activeApp);
    showRunning(a ? a.name : activeApp);
  } else if (activePlaylist) {
    showRunning(t("Playlist · %s", activePlaylist));
  } else {
    banner.classList.add("hidden");
  }
  document.querySelectorAll(".app-tile").forEach((tile) => {
    const on = tile.dataset.appId === activeApp;
    tile.classList.toggle("running", on);
    const badge = tile.querySelector(".app-badge");
    if (badge) badge.textContent = on ? t("▶ RUNNING") : "";
  });
}

async function runApp(id) { await post("/api/apps/run", { app: id }); updateActiveUI(id, null); }
async function stopApp() { await post("/api/apps/stop"); updateActiveUI(null, null); }

// Physically home every module (stops whatever is playing, blanks the wall).
async function homeAll() {
  const btn = $("homeAllBtn");
  if (!confirm(t("Home all modules?") + "\n\n" + t("This returns the whole display to its blank home position and stops anything currently playing."))) return;
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = t("Homing…");
  let restore = 1500;
  try {
    const r = await post("/api/display/home");
    if (r && r.ok === false) throw new Error(r.error || "home failed");
    updateActiveUI(null, null);
    await pollState();
    btn.textContent = t("Homed ✓");
  } catch (e) {
    btn.textContent = t("Home failed");
    console.error("home all:", e);
    restore = 2500;
  } finally {
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, restore);
  }
}

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
    if (!r) return t("Set a polling rate to estimate API usage.");
    const d = Math.round(86400 / r);
    return t("≈ %s requests/day · %sk/month", d.toLocaleString(), (d * 30 / 1000).toFixed(1));
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
  if (f.type === "notice") { const n = el("div", "notice"); n.textContent = t(f.label || f.text || ""); wrap.appendChild(n); return wrap; }
  if (f.type === "computed") {
    if (f.label) { const l = el("span"); l.textContent = t(f.label); wrap.appendChild(l); }
    const n = el("div", "notice"); wrap.appendChild(n); wrap._computeEl = n; return wrap;
  }
  const label = el("span"); label.innerHTML = t(f.label || f.key); wrap.appendChild(label);
  if (f.shared) { const b = el("span", "shared-badge"); b.textContent = t("shared"); b.title = t("Global setting — shared across apps"); label.appendChild(b); }
  if (f.note) { const nt = el("small", "field-note"); nt.textContent = t(f.note); wrap.appendChild(nt); }

  if (f.type === "toggle" || f.type === "select") {
    const opts = normOpts(f.options);
    if (f.type === "toggle") {
      const seg = el("div", "seg");
      const setOn = (v) => [...seg.children].forEach((c) => c.classList.toggle("on", c.dataset.value === String(v)));
      opts.forEach((o) => {
        const b = el("button"); b.type = "button"; b.textContent = t(o.label); b.dataset.value = o.value;
        b.addEventListener("click", () => { seg.dataset.value = o.value; setOn(o.value); applySync(f, o.value); onFormChange(); });
        seg.appendChild(b);
      });
      seg.dataset.value = val != null ? val : (opts[0]?.value ?? ""); setOn(seg.dataset.value);
      wrap.appendChild(seg);
      wrap._getValue = () => seg.dataset.value;
      wrap._setValue = (v) => { seg.dataset.value = v; setOn(v); };
    } else {
      const sel = el("select");
      opts.forEach((o) => { const op = el("option"); op.value = o.value; op.textContent = t(o.label); sel.appendChild(op); });
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
    const search = el("input"); search.placeholder = t("Search…");
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
    if (f.ph) inp.placeholder = t(f.ph);
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
  const schema = await api(`/api/apps/${id}/settings?lang=${LANG}`);
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
  const save = el("button", "btn primary"); save.textContent = t("Save");
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  save.addEventListener("click", async () => {
    const values = {};
    _formFields.forEach((w) => { const v = w._getValue && w._getValue(); if (v !== undefined) values[w._field.key] = v; });
    msg.textContent = t("Saving…");
    try { await post(`/api/apps/${id}/settings`, { values }); closeModal(); }
    catch (e) { msg.textContent = t("Error: %s", e.message); }
  });
  const close = el("button", "btn ghost"); close.textContent = t("Close"); close.addEventListener("click", closeModal);
  openModal(`${schema.icon} ${name || schema.name}`, form, [msg, close, save]);
}

// Per-playlist-entry settings: reuse the app's own settings form, but save the
// values into this entry's `overrides` (only what differs from the app's config),
// so the same app can appear multiple times configured differently.
async function openEntrySettings(entry) {
  const app = APPS.find((a) => a.id === entry.app);
  const schema = await api(`/api/apps/${entry.app}/settings?lang=${LANG}`);
  const base = Object.assign({}, schema.values, entry.overrides || {});   // entry values win in the form
  const form = el("div");
  const note = el("p", "hint");
  note.textContent = t("These apply to this playlist entry only. Unchanged fields follow the app's own settings.");
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
  const save = el("button", "btn primary"); save.textContent = t("Save for this entry");
  const clear = el("button", "btn ghost"); clear.textContent = t("Clear");
  clear.title = t("Remove all per-entry overrides (follow the app's settings)");
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
  openModal(t("%s — entry settings", `${app ? app.icon + " " : ""}${app ? app.name : entry.app}`), form, [msg, clear, save]);
}

async function openGlobalSettings() {
  const schema = await api(`/api/global-settings?lang=${LANG}`);
  const form = el("div", "gsettings");
  if (!schema.fields.length) {
    const p = el("p", "hint"); p.textContent = t("No global settings yet — install apps that use shared settings (weather, stocks, …).");
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
  const save = el("button", "btn primary"); save.textContent = t("Save");
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  save.addEventListener("click", async () => {
    const values = {};
    _formFields.forEach((w) => { const v = w._getValue && w._getValue(); if (v !== undefined) values[w._field.key] = v; });
    msg.textContent = t("Saving…");
    try {
      await post("/api/global-settings", { values });
      // "Always uppercase" changes what the wall will SHOW, so the Compose editor's preview
      // has to follow it — otherwise it goes on promising lowercase the wall will not give.
      await loadDisplays();
      cmpRender();
      closeModal();
    }
    catch (e) { msg.textContent = t("Error: %s", e.message); }
  });
  const close = el("button", "btn ghost"); close.textContent = t("Close"); close.addEventListener("click", closeModal);
  openModal(t("⚙ Global settings"), form, [msg, close, save]);
}

// ---- app library -----------------------------------------------------------
async function uploadApp(fileInput, msgEl) {
  const f = fileInput.files[0];
  msgEl.style.whiteSpace = "pre-wrap";   // keep line breaks in rejection reasons
  if (!f) { msgEl.textContent = t("Choose a .zip first."); return; }
  msgEl.textContent = t("Uploading…");
  try {
    const fd = new FormData(); fd.append("file", f);
    const r = await fetch(url("/api/apps/upload"), { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { msgEl.textContent = t("Error: %s", j.detail || r.status); return; }
    fileInput.value = "";
    openLibrary(); loadApps();
  } catch (e) { msgEl.textContent = t("Error: %s", e.message); }
}

const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// The category filter and search text the library modal was last left on
// ("" = All), so a reopen after Add/Remove/Upload lands back where you were.
let LIB_CAT = "";
let LIB_Q = "";

function libRow(a, reopen) {
  const row = el("div", "lib-row");

  const icon = el("span", "app-icon"); icon.style.fontSize = "20px"; icon.textContent = a.icon || "🧩";

  const meta = el("div", "lib-meta");
  const name = el("div", "lib-name"); name.textContent = a.name;
  if (a.i18n) {
    const globe = el("span"); globe.textContent = " 🌐";
    globe.title = t("Multilingual — adapts to the global Language");
    name.appendChild(globe);
  }
  if (!a.builtin) {
    const up = el("small", "lib-uploaded"); up.textContent = " · " + t("uploaded");
    name.appendChild(up);
  }
  const desc = el("div", "lib-desc"); desc.textContent = a.description || "";

  // The manifest's own metadata, same as the SplitFlap OS library shows:
  // what kind of app it is, its version, and its category.
  const tags = el("div", "lib-tags");
  const kind = el("span", "lib-tag");
  kind.textContent = t(a.type) + (a.version ? ` · v${a.version}` : "");
  tags.appendChild(kind);
  if (a.category) {
    const cat = el("span", "lib-tag cat"); cat.textContent = t(titleCase(a.category));
    tags.appendChild(cat);
  }
  meta.append(name, desc, tags);

  const btn = el("button", "btn btn-sm " + (a.installed ? "ghost" : "primary"));
  btn.textContent = a.installed ? t("Remove") : t("Add");
  btn.addEventListener("click", async () => {
    await post(`/api/apps/${a.id}/install`, { installed: !a.installed });
    reopen(); loadApps();
  });

  row.append(icon, meta, btn);
  if (!a.builtin) {
    const dl = el("button", "btn btn-sm"); dl.textContent = "🗑"; dl.title = t("Delete uploaded app");
    dl.style.background = "var(--hi)";
    dl.addEventListener("click", async () => {
      if (!confirm(t('Delete "%s"? This removes the uploaded app for good.', a.name))) return;
      await fetch(url(`/api/apps/${encodeURIComponent(a.id)}`), { method: "DELETE" });
      reopen(); loadApps();
    });
    row.appendChild(dl);
  }
  return row;
}

async function openLibrary() {
  const data = await api(`/api/apps/available?lang=${LANG}`);
  const wrap = el("div");

  // Upload a new app
  const box = el("div", "upload-box");
  box.innerHTML = `<div class="lib-name">${t("Upload an app")}</div>` +
    `<div class="hint" style="margin:3px 0 8px">${t("A <b>.zip</b> containing the app folder (<code>manifest.json</code> + <code>app.py</code>, or <code>data.json</code> for a channel app). Functional apps run Python on this host — only upload apps you trust.")}</div>`;
  const inp = el("input"); inp.type = "file"; inp.accept = ".zip"; inp.style.width = "auto";
  const ub = el("button", "btn btn-sm primary"); ub.textContent = t("Upload");
  const um = el("span", "hint");
  const urow = el("div", "inline-row"); urow.append(inp, ub, um);
  ub.addEventListener("click", () => uploadApp(inp, um));
  box.appendChild(urow); wrap.appendChild(box);

  // Search + category filter — the categories are only the ones actually on disk.
  const cats = [...new Set(data.apps.map((a) => a.category).filter(Boolean))].sort();
  if (LIB_CAT && !cats.includes(LIB_CAT)) LIB_CAT = "";
  const search = el("input", "lib-search");
  search.type = "search"; search.placeholder = t("Search apps…"); search.value = LIB_Q;
  const filters = el("div", "lib-filters");
  const list = el("div", "lib-list");

  const matches = (a) => {
    if (LIB_CAT && a.category !== LIB_CAT) return false;
    const q = LIB_Q.trim().toLowerCase();
    if (!q) return true;
    return [a.name, a.description, a.category, a.id].some((f) => (f || "").toLowerCase().includes(q));
  };
  const draw = () => {
    list.innerHTML = "";
    const shown = data.apps.filter(matches);
    shown.forEach((a) => list.appendChild(libRow(a, openLibrary)));
    if (!shown.length) list.innerHTML = `<span class="hint">${t("No apps match.")}</span>`;
    [...filters.children].forEach((f) => f.classList.toggle("active", f.dataset.cat === LIB_CAT));
  };
  [["", t("All")], ...cats.map((c) => [c, t(titleCase(c))])].forEach(([value, label]) => {
    const f = el("button", "lib-filter");
    f.type = "button"; f.dataset.cat = value; f.textContent = label;
    f.addEventListener("click", () => { LIB_CAT = value; draw(); });
    filters.appendChild(f);
  });
  search.addEventListener("input", () => { LIB_Q = search.value; draw(); });
  draw();
  wrap.append(search, filters, list);

  const close = el("button", "btn ghost"); close.textContent = t("Close"); close.addEventListener("click", closeModal);
  openModal(t("App Library"), wrap, [close]);
}

// ---- playlists -------------------------------------------------------------
const rid = (p) => p + Math.random().toString(36).slice(2, 8);
let PL_ENTRIES = [];
let SAVED_PL = {};
// Which saved playlist the editor is EDITING ("" = a new, unsaved one). The editor used
// to be an anonymous scratch buffer: "Load" copied a playlist's entries and forgot where
// they came from, so saving an edit meant retyping the name by hand — and a typo quietly
// made a second playlist instead of updating the one you meant.
let PL_NAME = "";

function plRender() {
  const box = $("plEntries"); box.innerHTML = "";
  if (!PL_ENTRIES.length) box.innerHTML = `<span class="hint">${t("Add an app or message.")}</span>`;
  PL_ENTRIES.forEach((e, i) => {
    const row = el("div", "row-card");
    const tag = el("span", "handle"); tag.textContent = e.type === "app" ? t("▸ App") : t("▸ Msg"); row.appendChild(tag);
    if (e.type === "app") {
      const sel = el("select"); sel.className = "grow";
      APPS.forEach((a) => { const o = el("option"); o.value = a.id; o.textContent = `${a.icon} ${a.name}${a.i18n ? " 🌐" : ""}`; if (a.id === e.app) o.selected = true; sel.appendChild(o); });
      if (!e.app && APPS[0]) e.app = APPS[0].id;
      sel.onchange = () => { e.app = sel.value; e.overrides = {}; plRender(); }; row.appendChild(sel);
      // Per-entry settings: override this entry's config (location/units/language…)
      // independently, so the same app can appear more than once configured differently.
      const nOv = Object.keys(e.overrides || {}).length;
      const cfg = el("button", "del"); cfg.textContent = nOv ? `⚙ ${nOv}` : "⚙";
      cfg.title = nOv ? t("%s setting(s) overridden for this entry", nOv) : t("Settings for this entry");
      if (nOv) cfg.style.color = "var(--brand)";
      cfg.onclick = () => openEntrySettings(e);
      row.appendChild(cfg);
    } else {
      const inp = el("input"); inp.className = "grow"; inp.placeholder = t("MESSAGE"); inp.value = e.text || "";
      inp.oninput = () => (e.text = inp.value); row.appendChild(inp);
    }
    const dur = el("input"); dur.type = "number"; dur.min = 1; dur.style.width = "70px"; dur.title = t("seconds");
    dur.value = e.duration || 30; dur.oninput = () => (e.duration = Number(dur.value)); row.appendChild(dur);
    const del = el("button", "del"); del.textContent = "✕"; del.onclick = () => { PL_ENTRIES.splice(i, 1); plRender(); }; row.appendChild(del);
    box.appendChild(row);
  });
  plSaveLabel();          // an empty editor has nothing to save
}
async function loadPlaylists() {
  if (!APPS.length) await loadApps();
  SAVED_PL = (await api("/api/playlists")).playlists || {};
  const saved = $("plSaved"); saved.innerHTML = "";
  const names = Object.keys(SAVED_PL);
  if (!names.length) { saved.innerHTML = `<span class="hint">${t("None yet.")}</span>`; }
  names.forEach((n) => {
    const row = el("div", "saved-row" + (n === PL_NAME ? " editing" : ""));
    const nm = el("span", "grow"); nm.textContent = n; row.appendChild(nm);
    if (n === PL_NAME) { const tag = el("span", "pill sm"); tag.textContent = t("editing"); row.appendChild(tag); }
    const run = el("button", "btn btn-sm primary"); run.textContent = t("Run"); run.onclick = () => post("/api/playlists/run", { entries: SAVED_PL[n].entries, loop: SAVED_PL[n].loop !== false, name: n }); row.appendChild(run);
    const load = el("button", "btn btn-sm ghost"); load.textContent = t("Edit"); load.onclick = () => plEdit(n); row.appendChild(load);
    const del = el("button", "btn btn-sm ghost"); del.textContent = t("Delete"); del.onclick = async () => { await fetch(url("/api/playlists/" + encodeURIComponent(n)), { method: "DELETE" }); loadPlaylists(); }; row.appendChild(del);
    saved.appendChild(row);
  });
  if (!PL_ENTRIES.length) plRender();
  plSaveLabel();
}
async function runPlaylistNow() {
  if (!PL_ENTRIES.length) { $("plSaved"); return; }
  await post("/api/playlists/run", { entries: PL_ENTRIES, loop: $("plLoop").checked, name: PL_NAME || "(unsaved)" });
}
// The editor's name field IS the identity. Saving writes to whatever it says: unchanged,
// that updates the playlist you loaded; changed, it renames it (and the button says so, so
// nobody renames by accident while reaching for a copy).
function plSaveLabel() {
  const typed = $("plName").value.trim();
  const btn = $("plSave");
  btn.textContent = (PL_NAME && typed && typed !== PL_NAME) ? t("Rename & save") : t("Save");
  btn.disabled = !typed || !PL_ENTRIES.length;
  btn.title = PL_NAME ? t("Update “%s”", PL_NAME) : t("Save as a new playlist");
}

function plEdit(name) {
  PL_ENTRIES = JSON.parse(JSON.stringify(SAVED_PL[name].entries));
  PL_NAME = name;
  $("plName").value = name;
  $("plLoop").checked = SAVED_PL[name].loop !== false;
  plRender();
  loadPlaylists();          // re-render the list so the edited row is marked
}

function plNew() {
  PL_ENTRIES = [];
  PL_NAME = "";
  $("plName").value = "";
  $("plLoop").checked = true;
  plRender();
  loadPlaylists();
}

async function savePlaylist() {
  const name = $("plName").value.trim();
  if (!name) { $("plName").focus(); return; }
  if (!PL_ENTRIES.length) return;
  await post("/api/playlists", { name, entries: PL_ENTRIES, loop: $("plLoop").checked });
  // A rename is a save under the new name plus a delete of the old — otherwise the one
  // you renamed away from lingers as a stale duplicate of what you just edited.
  if (PL_NAME && PL_NAME !== name) {
    await fetch(url("/api/playlists/" + encodeURIComponent(PL_NAME)), { method: "DELETE" });
  }
  PL_NAME = name;
  await loadPlaylists();
}

// ---- triggers --------------------------------------------------------------
let TRIGS = [], TRIG_APPS = [];
function trigRender() {
  const box = $("trigList"); box.innerHTML = "";
  if (!TRIGS.length) box.innerHTML = `<span class="hint">${t("No triggers yet.")}</span>`;
  TRIGS.forEach((trig, i) => {
    const row = el("div", "row-card");
    const en = el("input"); en.type = "checkbox"; en.checked = trig.enabled !== false; en.onchange = () => (trig.enabled = en.checked); row.appendChild(en);
    const app = TRIG_APPS.find((a) => a.id === trig.app);
    const info = el("span", "grow"); info.textContent = app ? `${app.icon} ${app.name}` : trig.app; row.appendChild(info);
    const nm = el("input"); nm.placeholder = t("Label"); nm.value = trig.name || ""; nm.oninput = () => (trig.name = nm.value); row.appendChild(nm);
    const cd = el("input"); cd.type = "number"; cd.style.width = "76px"; cd.title = t("cooldown (s)"); cd.value = trig.cooldown || 300; cd.oninput = () => (trig.cooldown = Number(cd.value)); row.appendChild(cd);
    const ds = el("input"); ds.type = "number"; ds.style.width = "68px"; ds.title = t("show (s)"); ds.value = trig.display_seconds || 30; ds.oninput = () => (trig.display_seconds = Number(ds.value)); row.appendChild(ds);
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
  $("trigMsg").textContent = t("Saved ✓"); setTimeout(() => ($("trigMsg").textContent = ""), 2000);
}

// ---- gateway link-tabs (unified nav) ---------------------------------------
// What a gateway that doesn't advertise its tabs (pre-3.4 firmware) has. Those
// gateways still carry a Backup tab — 3.4 is what folded backup/restore into
// Settings — so the fallback keeps it, and a 3.4+ gateway simply advertises a
// list without it. See backend/app/tabs.py.
const GW_TABS_FALLBACK = [
  { id: "modules", label: "Modules" },
  { id: "display", label: "Display" },
  { id: "provision", label: "Provision" },
  { id: "calibration", label: "Calibration" },
  { id: "monitor", label: "Monitor" },
  { id: "settings", label: "Settings" },
  { id: "backup", label: "Backup" },
  { id: "status", label: "Status" },
];

let GW_SIG = "";     // what the nav currently shows — don't rebuild it for nothing
let GW_TRIES = 0;

async function setupGatewayTabs() {
  // NB: named neither `url` nor `gwUrl` — both are global helpers used below, and a local
  // of the same name shadows them. `gwUrl` did exactly that once: it was a STRING here, so
  // the gwUrl() call below threw "gwUrl is not a function", the whole tab render aborted,
  // and the gateway's tabs simply never appeared.
  let gwAddr = "", tabs = [];
  try {
    const st = await api("/api/gateway/status");
    gwAddr = st.url || "";
    if (Array.isArray(st.tabs)) tabs = st.tabs;
  } catch {}
  const base = gwAddr.replace(/\/$/, "");
  const shown = tabs.length ? tabs : GW_TABS_FALLBACK;

  const sig = base + "|" + JSON.stringify(shown);
  if (sig !== GW_SIG) {
    GW_SIG = sig;
    const nav = $("nav");
    nav.querySelectorAll(".tab.gw").forEach((a) => a.remove());
    shown.forEach((tab) => {
      const a = el("a", "tab gw");
      a.dataset.gw = tab.id;
      // Through the proxy (/gw/), not straight at the gateway's own address. A direct
      // link leaves Home Assistant altogether — HA can only put THIS add-on's port in the
      // sidebar, so the gateway can only appear in there if we serve it. Same origin, so
      // no target="_top" either: that used to break out of the ingress iframe.
      a.textContent = t(tab.label);
      // `base` (the gateway being registered) gates whether the link works; the href is
      // our proxy path, which url() prefixes with the ingress base when under Home Assistant.
      if (base) a.href = `${gwUrl()}#${tab.id}`;
      else { a.href = "#"; a.classList.add("disabled"); }
      nav.appendChild(a);
    });
  }
  // The gateway advertises its tabs in reply to our registration, which races a
  // page opened right after the companion starts. Re-ask a few times so a slow or
  // briefly-unreachable gateway still swaps the fallback list out on its own.
  if (!tabs.length && GW_TRIES < 4) { GW_TRIES++; setTimeout(setupGatewayTabs, 5000); }
}

// Deep-link: open the local tab named in the URL hash (e.g. companion/#playlists).
function openTabFromHash() {
  const h = (location.hash || "").replace("#", "");
  const btn = document.querySelector(`.tab[data-tab="${h}"]`);
  if (btn) btn.click();
}

// ---- the ⚙ tools menu -------------------------------------------------------
// Permanent. COMPANION_DEV_MODE gates exactly one thing inside it: simulation mode
// (and the grid override that belongs to it).
function updateDevBtn(st) {
  const b = $("devBtn");
  b.textContent = st && st.sim_mode ? "⚙ SIM" : "⚙";
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

    // 0) Displays. First, because "which wall" is the outermost question the rest of
    // this dialog is scoped by.
    const dF = el("div", "field");
    const dLbl = el("label"); dLbl.textContent = t("Displays");
    const dBtn = el("button", "btn btn-sm");
    dBtn.textContent = DISPLAYS.length > 1
      ? t("Manage %s displays", String(DISPLAYS.length))
      : t("Add a display");
    dBtn.onclick = () => { closeModal(); openDisplays(); };
    const dNote = el("small", "field-note");
    dNote.textContent = t("Drive several gateways from one companion. Each has its own geometry, apps, playlists and settings.");
    dF.append(dLbl, dBtn, dNote);
    wrap.appendChild(dF);

    // 1) Simulation mode — the one dev-gated control (COMPANION_DEV_MODE).
    if (st.enabled) {
      const simF = el("div", "field");
      const simLbl = el("label"); simLbl.style.cssText = "display:flex;align-items:center;gap:8px;font-weight:600";
      const sim = el("input"); sim.type = "checkbox"; sim.checked = st.sim_mode; sim.style.width = "auto";
      simLbl.appendChild(sim);
      simLbl.appendChild(document.createTextNode(t("Simulation mode")));
      simF.appendChild(simLbl);
      const simNote = el("small", "field-note");
      simNote.textContent = st.sim_mode
        ? t("Simulating — nothing is sent to the physical display (the live preview still updates).")
        : t("Live — frames are sent to the display. Turn on to test apps without touching the wall.");
      simF.appendChild(simNote);
      sim.addEventListener("change", async () => {
        sim.disabled = true;
        try { render(await post("/api/dev/sim", { on: sim.checked })); await refreshGridUI(); }
        catch (e) { simNote.textContent = t("Failed: %s", e.message); sim.disabled = false; }
      });

      // Grid geometry override — belongs to simulation, so it lives right here and
      // only exists while simulating (the real display's geometry is never touched).
      if (st.sim_mode) {
        const gNote = el("small", "field-note");
        gNote.style.marginLeft = "24px";
        gNote.textContent = t("Grid override: now %s", `${st.grid.rows}×${st.grid.cols}`) +
          (st.grid_overridden ? t(" (overridden)") : "") +
          t(" · gateway is %s.", `${st.gateway_grid.rows}×${st.gateway_grid.cols}`);
        simF.appendChild(gNote);
        const gRow = el("div"); gRow.style.cssText = "display:flex;align-items:center;gap:8px;margin:6px 0 0 24px";
        const rows = el("input"); rows.type = "number"; rows.min = 1; rows.max = 20; rows.value = st.grid.rows; rows.style.width = "64px";
        const cols = el("input"); cols.type = "number"; cols.min = 1; cols.max = 40; cols.value = st.grid.cols; cols.style.width = "64px";
        const applyBtn = el("button", "btn ghost btn-sm"); applyBtn.textContent = t("Apply");
        gRow.appendChild(document.createTextNode(t("Rows"))); gRow.appendChild(rows);
        gRow.appendChild(document.createTextNode(t("Cols"))); gRow.appendChild(cols);
        gRow.appendChild(applyBtn);
        applyBtn.addEventListener("click", async () => {
          applyBtn.disabled = true;
          try { render(await post("/api/dev/grid", { rows: Number(rows.value), cols: Number(cols.value) })); await refreshGridUI(); }
          catch (e) { gNote.textContent = t("Failed: %s", e.message); applyBtn.disabled = false; }
        });
        simF.appendChild(gRow);
      }
      wrap.appendChild(simF);
    }

    // 1b) Vestaboard-compatible Local API
    const vbF = el("div", "field");
    const vbLbl = el("label"); vbLbl.style.cssText = "display:flex;align-items:center;gap:8px;font-weight:600";
    const vb = el("input"); vb.type = "checkbox"; vb.checked = !!st.vestaboard; vb.style.width = "auto";
    vbLbl.appendChild(vb);
    vbLbl.appendChild(document.createTextNode(t("Vestaboard API"))); 
    vbF.appendChild(vbLbl);
    const vbNote = el("small", "field-note");
    vbF.appendChild(vbNote);

    // Details (the key + endpoint) only mean anything while it's on.
    const showVb = async () => {
      if (!vb.checked) {
        vbNote.textContent = t("Off. Turn on to accept Vestaboard Local API calls (Home Assistant, scripts) at /local-api/message — this display then answers like a Vestaboard.");
        return;
      }
      vbNote.textContent = t("Loading…");
      try {
        const d = await api("/api/dev/vestaboard");
        vbNote.innerHTML = "";
        // d.url is the address a client OUTSIDE the browser must use. As an add-on our
        // own origin is Home Assistant's (ingress), which does not reach this endpoint.
        const endpoint = d.url || `${location.origin}${d.path}`;
        const l1 = el("div"); l1.textContent = `POST ${endpoint}`;
        const l2 = el("div"); l2.style.marginTop = "2px";
        l2.textContent = `X-Vestaboard-Local-Api-Key: ${d.key}`;
        const l3 = el("div"); l3.style.marginTop = "2px";
        l3.textContent = d.env_key
          ? t("Key pinned by COMPANION_VESTABOARD_KEY.")
          : t("Key generated and stored with your settings. Pin your own with COMPANION_VESTABOARD_KEY.");
        vbNote.append(l1, l2, l3);
      } catch (e) { vbNote.textContent = t("Failed: %s", e.message); }
    };
    vb.addEventListener("change", async () => {
      vb.disabled = true;
      try { render(await post("/api/dev/vestaboard", { on: vb.checked })); }
      catch (e) { vbNote.textContent = t("Failed: %s", e.message); vb.disabled = false; }
    });
    showVb();
    wrap.appendChild(vbF);

    // 1c) MCP server — same shape as the Vestaboard switch above.
    const mcF = el("div", "field");
    const mcLbl = el("label"); mcLbl.style.cssText = "display:flex;align-items:center;gap:8px;font-weight:600";
    const mc = el("input"); mc.type = "checkbox"; mc.checked = !!st.mcp; mc.style.width = "auto";
    mcLbl.appendChild(mc);
    mcLbl.appendChild(document.createTextNode(t("MCP server")));
    mcF.appendChild(mcLbl);
    const mcNote = el("small", "field-note");
    mcF.appendChild(mcNote);

    // The token + endpoint only mean anything while it's on.
    const showMcp = async () => {
      if (!mc.checked) {
        mcNote.textContent = t("Off. Turn on to let an LLM client (Claude, an agent) drive the display as tools at /mcp — show a message, run an app, read the board.");
        return;
      }
      mcNote.textContent = t("Loading…");
      try {
        const d = await api("/api/dev/mcp");
        mcNote.innerHTML = "";
        const l1 = el("div"); l1.textContent = d.url || `${location.origin}${d.path}`;
        const l2 = el("div"); l2.style.marginTop = "2px";
        l2.textContent = `Authorization: Bearer ${d.token}`;
        const l3 = el("div"); l3.style.marginTop = "2px";
        l3.textContent = d.env_token
          ? t("Token pinned by COMPANION_MCP_TOKEN.")
          : t("Token generated and stored with your settings. Pin your own with COMPANION_MCP_TOKEN.");
        mcNote.append(l1, l2, l3);
      } catch (e) { mcNote.textContent = t("Failed: %s", e.message); }
    };
    mc.addEventListener("change", async () => {
      mc.disabled = true;
      try { render(await post("/api/dev/mcp", { on: mc.checked })); }
      catch (e) { mcNote.textContent = t("Failed: %s", e.message); mc.disabled = false; }
    });
    showMcp();
    wrap.appendChild(mcF);

    // 2) Force resync with the gateway
    const reF = el("div", "field");
    const reLbl = el("span"); reLbl.textContent = t("Gateway sync"); reLbl.style.fontWeight = "600"; reF.appendChild(reLbl);
    const reRow = el("div"); reRow.style.cssText = "display:flex;align-items:center;gap:10px;margin-top:6px";
    const reBtn = el("button", "btn ghost btn-sm"); reBtn.textContent = t("↻ Force resync");
    const reMsg = el("small", "field-note"); reMsg.textContent = t("Pull grid geometry + MQTT settings from the gateway now.");
    reBtn.addEventListener("click", async () => {
      reBtn.disabled = true; reMsg.textContent = t("Syncing…");
      try { const r = await post("/api/dev/resync", {}); reMsg.textContent = r.ok ? t("Resynced ✓") : t("Failed: %s", r.error || "unknown"); }
      catch (e) { reMsg.textContent = t("Failed: %s", e.message); }
      reBtn.disabled = false;
      render(await api("/api/dev")); await refreshGridUI();
    });
    reRow.appendChild(reBtn); reRow.appendChild(reMsg); reF.appendChild(reRow);
    wrap.appendChild(reF);

    // 2b) Gateway-stored settings (Gateway 3.1+)
    const gsF = el("div", "field");
    const gsLbl = el("span"); gsLbl.textContent = t("Gateway-stored settings"); gsLbl.style.fontWeight = "600"; gsF.appendChild(gsLbl);
    const gsRow = el("div"); gsRow.style.cssText = "display:flex;align-items:center;gap:10px;margin-top:6px;flex-wrap:wrap";
    const pullBtn = el("button", "btn ghost btn-sm"); pullBtn.textContent = t("⭳ Retrieve from gateway");
    const pushBtn = el("button", "btn ghost btn-sm"); pushBtn.textContent = t("⭱ Write to gateway");
    const gsMsg = el("small", "field-note"); gsMsg.textContent = t("Manually sync this companion's settings with the gateway (needs Gateway 3.1+).");
    pullBtn.addEventListener("click", async () => {
      pullBtn.disabled = pushBtn.disabled = true; gsMsg.textContent = t("Retrieving…");
      try { const r = await post("/api/dev/settings/pull", {}); gsMsg.textContent = r.ok ? t("Retrieved ✓ (%s apps)", r.installed) : t("Failed: %s", r.error || "unknown"); if (r.ok) await loadApps(); }
      catch (e) { gsMsg.textContent = t("Failed: %s", e.message); }
      pullBtn.disabled = pushBtn.disabled = false;
    });
    pushBtn.addEventListener("click", async () => {
      pullBtn.disabled = pushBtn.disabled = true; gsMsg.textContent = t("Writing…");
      try { const r = await post("/api/dev/settings/push", {}); gsMsg.textContent = r.ok ? t("Written ✓") : t("Failed: %s", r.error || "unknown"); }
      catch (e) { gsMsg.textContent = t("Failed: %s", e.message); }
      pullBtn.disabled = pushBtn.disabled = false;
    });
    gsRow.appendChild(pullBtn); gsRow.appendChild(pushBtn); gsF.appendChild(gsRow); gsF.appendChild(gsMsg);
    wrap.appendChild(gsF);

  };

  render(await api("/api/dev"));
  const close = el("button", "btn"); close.textContent = t("Close"); close.addEventListener("click", closeModal);
  openModal(t("Tools"), wrap, [close]);
}

// ---- displays (the wall switcher) -------------------------------------------
// Loaded before anything else, because DISPLAY decides what every /api/ call below is
// even ABOUT. The chosen wall is remembered per browser: someone who works on the
// office display does not want to re-pick it on every page load.
async function loadDisplays() {
  let doc;
  try {
    doc = await api("/api/displays");
  } catch {
    return;                       // an older server: one wall, no switcher, carry on
  }
  DISPLAYS = doc.displays || [];
  DEFAULT_DISPLAY = doc.default || "default";

  const remembered = localStorage.getItem("splitflap.display");
  const known = (id) => DISPLAYS.some((d) => d.id === id && d.enabled !== false);
  DISPLAY = known(remembered) ? remembered : DEFAULT_DISPLAY;

  const me = DISPLAYS.find((d) => d.id === DISPLAY);
  RICH = !!(me && me.rich);

  const sel = $("displaySel");
  sel.innerHTML = "";
  DISPLAYS.filter((d) => d.enabled !== false).forEach((d) => {
    const o = el("option");
    o.value = d.id;
    o.textContent = d.name + (d.module_count ? ` · ${d.module_count}` : "");
    sel.appendChild(o);
  });
  sel.value = DISPLAY;
  // One wall is the overwhelmingly common case, and it should look exactly as it did
  // before any of this existed.
  sel.classList.toggle("hidden", DISPLAYS.length < 2);
  sel.onchange = () => switchDisplay(sel.value);
}

async function switchDisplay(id) {
  if (!id || id === DISPLAY) return;
  DISPLAY = id;
  localStorage.setItem("splitflap.display", id);
  // Everything on screen belongs to the OLD wall — its geometry, its apps, its
  // playlists, its triggers, its gateway's tabs. Re-read the lot rather than trying to
  // patch it, which is how you end up showing one wall's apps on another's grid.
  await bootGrid();
  try { await loadApps(); } catch { /* the rest must still come up */ }
  await loadPlaylists();
  await loadTriggers();
  setupGatewayTabs();
}

// ---- Displays: add, rename, re-point, remove, choose the default -------------
async function openDisplays() {
  const body = el("div", "stack");
  const list = el("div", "stack");
  body.appendChild(list);

  const render = async () => {
    const doc = await api("/api/displays");
    DISPLAYS = doc.displays || [];
    DEFAULT_DISPLAY = doc.default;
    list.innerHTML = "";

    DISPLAYS.forEach((d) => {
      const row = el("div", "row display-row");
      const isDefault = d.id === doc.default;

      const name = el("input", "input");
      name.value = d.name;
      name.setAttribute("aria-label", t("Name"));
      const gw = el("input", "input");
      gw.value = d.gateway_url || "";
      gw.placeholder = "http://192.168.1.50";
      gw.setAttribute("aria-label", t("Gateway URL"));

      const info = el("span", "muted sm");
      info.textContent = d.grid ? `${d.grid.rows}×${d.grid.cols}` : t("not running");

      const save = el("button", "btn btn-sm");
      save.textContent = t("Save");
      // PATCH, not POST — a rename lands at once, a re-point needs a restart.
      save.onclick = async () => {
        const res = await fetch(url(`/api/displays/${encodeURIComponent(d.id)}`), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.value.trim(), gateway_url: gw.value.trim() }),
        });
        const doc2 = await res.json();
        if (doc2.restart_required) {
          info.textContent = t("Restart the add-on to re-point this display");
          info.className = "warn sm";
        }
        await render();
        await loadDisplays();
      };

      const mkDefault = el("button", "btn btn-sm ghost");
      mkDefault.textContent = isDefault ? t("Default") : t("Make default");
      mkDefault.disabled = isDefault;
      mkDefault.title = t("The display that anything not naming one drives — Home Assistant, the Vestaboard API, an MCP call");
      mkDefault.onclick = async () => {
        await post(`/api/displays/${encodeURIComponent(d.id)}/default`, {});
        await render();
        await loadDisplays();
      };

      const del = el("button", "btn btn-sm danger");
      del.textContent = t("Remove");
      del.disabled = DISPLAYS.length < 2;
      del.onclick = async () => {
        if (!confirm(t("Remove this display? Its settings, playlists and triggers are kept.")))
          return;
        const res = await fetch(url(`/api/displays/${encodeURIComponent(d.id)}`), { method: "DELETE" });
        if (!res.ok) { alert((await res.json()).detail || t("Could not remove it")); return; }
        if (DISPLAY === d.id) { DISPLAY = ""; localStorage.removeItem("splitflap.display"); }
        await render();
        await loadDisplays();
        await switchDisplay(DEFAULT_DISPLAY);
      };

      row.append(name, gw, info, save, mkDefault, del);
      list.appendChild(row);
    });

    // add a wall
    const add = el("div", "row display-row");
    const an = el("input", "input"); an.placeholder = t("Office wall");
    const ag = el("input", "input"); ag.placeholder = "http://192.168.1.50";
    const btn = el("button", "btn btn-sm primary");
    btn.textContent = t("Add display");
    btn.onclick = async () => {
      if (!ag.value.trim()) { ag.focus(); return; }
      const res = await fetch(url("/api/displays"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: an.value.trim() || ag.value.trim(), gateway_url: ag.value.trim() }),
      });
      if (!res.ok) { alert((await res.json()).detail || t("Could not add it")); return; }
      an.value = ""; ag.value = "";
      await render();
      await loadDisplays();
    };
    add.append(an, ag, btn);
    list.appendChild(add);

    const note = el("p", "muted sm");
    note.textContent = t("A new display starts from this one's global settings — location, language and API keys are copied so you don't retype them. They become its own from then on, and are stored on its own gateway.");
    list.appendChild(note);
  };

  await render();
  openModal(t("Displays"), body, []);
}


async function init() {
  // Unless an explicit choice was made, prefer Home Assistant's language over the
  // browser's: someone whose HA is in French wants a French add-on, whatever their
  // browser was configured with years ago.
  if (!window.__LOCKED__) {
    const ha = offered(haLanguage());
    if (ha) LANG = ha;
  }
  await loadI18n();
  translateDom();
  const h = await api("/api/health"); $("version").textContent = "v" + h.version;
  // Before ANY other /api call: DISPLAY decides which wall they are about.
  await loadDisplays();
  wireTabs();
  await bootGrid();
  $("stopAppBtn").addEventListener("click", stopApp);
  $("homeAllBtn").addEventListener("click", homeAll);
  $("manageAppsBtn").addEventListener("click", openLibrary);
  $("globalSettingsBtn").addEventListener("click", openGlobalSettings);
  // The ⚙ tools menu is permanent; /api/dev only feeds the SIM badge on the button.
  $("devBtn").addEventListener("click", openDevMenu);
  try { updateDevBtn(await api("/api/dev")); } catch { /* badge only */ }
  $("modalClose").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  // compose
  $("cmpPush").addEventListener("click", cmpPush);
  $("cmpClear").addEventListener("click", cmpClear);
  // playlists
  $("plAddApp").addEventListener("click", () => { PL_ENTRIES.push({ type: "app", app: APPS[0]?.id || "", duration: 30 }); plRender(); });
  $("plAddMsg").addEventListener("click", () => { PL_ENTRIES.push({ type: "compose", text: "", duration: 15 }); plRender(); });
  $("plRun").addEventListener("click", runPlaylistNow);
  $("plSave").addEventListener("click", savePlaylist);
  $("plNew").addEventListener("click", plNew);
  $("plName").addEventListener("input", plSaveLabel);
  // triggers
  $("trigAdd").addEventListener("click", addTrigger);
  $("trigSave").addEventListener("click", saveTriggers);
  // Guarded: a throw here used to abort init() and take everything after it with it —
  // the gateway tabs never appeared, and the only symptom was a console error.
  try { await loadApps(); } catch (e) { console.error("loadApps failed:", e); }
  setupGatewayTabs();
  openTabFromHash();
  window.addEventListener("hashchange", openTabFromHash);
  pollState(); pollStatus();
  setInterval(pollState, 300);
  setInterval(pollStatus, 3000);
}

init();
