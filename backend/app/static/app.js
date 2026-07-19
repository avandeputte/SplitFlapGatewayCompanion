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
let CANVAS = false;               // does THIS wall have a framebuffer? (a Matrix panel does)
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
  if (!r.ok) {
    // Surface the server's own explanation (FastAPI's {"detail": ...}) instead of
    // a bare status code — it's the difference between "422" and "name in use".
    let detail = "";
    try {
      const j = await r.json();
      if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not JSON — the status is all we have */ }
    throw new Error(`${path} → ${r.status}${detail ? ` — ${detail}` : ""}`);
  }
  return r.status === 204 ? null : r.json();
}
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const patch = (path, body) =>
  api(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

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
// The browser rides a Server-Sent Events stream (GET /api/events): the backend PUSHES
// the display state the instant the wall changes, so the preview follows in near-real
// time without a poll hammering a few times a second. If the stream drops we fall back
// to polling /api/current_state — the very document the stream carries — until it
// recovers. The badge shows nothing while the display is reachable, only a red banner if
// the connection drops; no technical wording surfaces.
function setBadge(offline) {
  const badge = $("statusBadge");
  const cls = offline ? "badge err" : "badge hidden";
  if (badge.className === cls) return;                       // diff: this is a hot path
  badge.className = cls;
  badge.textContent = offline ? t("⚠ Display offline") : "";
}

// A canvas app draws on the LED panel, not the flaps, and its frame is a PNG we refresh
// on a timer: a running effect's state snapshot doesn't change frame to frame, so no SSE
// event announces each one. The timer runs ONLY while a canvas is up, so an ordinary flap
// wall never polls for an image it doesn't have.
let CANVAS_TIMER = null;
function refreshCanvasImg() {
  const cimg = $("canvasPreview");
  if (!cimg) return;
  const src = url("/api/current_state/canvas.png");
  cimg.src = src + (src.includes("?") ? "&" : "?") + "t=" + Date.now();
}
function startCanvasRefresh() {
  if (CANVAS_TIMER) return;
  refreshCanvasImg();
  CANVAS_TIMER = setInterval(refreshCanvasImg, 300);
}
function stopCanvasRefresh() {
  if (CANVAS_TIMER) { clearInterval(CANVAS_TIMER); CANVAS_TIMER = null; }
}

// The tiny "how is the preview being fed" line under the board: the live SSE stream, the
// fallback poll, or "local" when the wall is simulated (no gateway). Diffed — this runs on
// every frame.
function setPreviewSrc(mode) {
  const el = $("previewSrc");
  if (!el) return;
  const txt = t("Updates: %s", mode);
  if (el.textContent !== txt) el.textContent = txt;
}

// Paint the preview from one state document — whether it arrived over the stream or a
// fallback poll. Idempotent (a diff-render), so applying the same state twice is harmless.
let APPLY_BUSY = false;          // guards the rare async re-boot when the grid resizes under us
async function applyState(st, disp) {
  if (disp !== DISPLAY) return;                             // stale: for a wall we've left
  const tr = st.transport || {};
  setBadge(!(tr.connected || tr.type === "sim"));
  setPreviewSrc(tr.type === "sim" ? "local" : (ES_LIVE ? "SSE" : "poll"));
  const board = $("preview");
  // The wall can change shape under us: the gateway may have been unreachable at boot, or
  // its Display Layout edited since. Re-read the geometry rather than reusing a stale
  // GRID.cols, which would lay 75 cells out 15-wide by luck alone.
  if (st.module_count && st.module_count !== GRID.module_count) {
    if (APPLY_BUSY) return;
    APPLY_BUSY = true;
    try { await bootGrid(); loadApps(); }                    // rebuilds both boards; re-gates apps
    finally { APPLY_BUSY = false; }
    return;                                                  // next frame paints on the rebuilt grid
  }
  let cimg = $("canvasPreview");
  if (st.canvas) {
    if (!cimg) {
      cimg = el("img"); cimg.id = "canvasPreview"; cimg.className = "canvas-preview"; cimg.alt = "";
      // Only take the preview over once a panel frame actually LOADS. If the gateway can't
      // produce one (readback unavailable — canvas.png 404s), keep the flap grid up rather
      // than swapping in a blank/broken image, which is what left the preview empty.
      cimg.addEventListener("load", () => { cimg.style.display = ""; $("preview").style.display = "none"; });
      cimg.addEventListener("error", () => { cimg.style.display = "none"; $("preview").style.display = ""; });
      board.parentNode.insertBefore(cimg, board.nextSibling);
    }
    startCanvasRefresh();          // sets the src; load/error above toggles which view shows
  } else {
    stopCanvasRefresh();
    if (cimg) cimg.style.display = "none";
    board.style.display = "";
    if (board.children.length !== st.chars.length) buildBoard(board, st.chars.length, GRID.cols);
    st.chars.forEach((ch, i) => {
      const cell = board.children[i];
      if (!cell) return;
      // Diff before writing: most frames change nothing, and rewriting every cell's class
      // + text is layout work for identical pixels.
      const cls = classForChar(ch), g = glyph(ch);
      if (cell.className !== cls) cell.className = cls;
      if (cell.textContent !== g) cell.textContent = g;
    });
  }
  const meta = `${GRID.rows}×${GRID.cols} · ${st.module_count} ${t("modules")}`;
  if ($("previewMeta").textContent !== meta) $("previewMeta").textContent = meta;
  if (APPS.length) updateActiveUI(st.active_app, st.active_playlist);
}

let POLL_BUSY = false;           // the fallback poll: never stack a request on a slow link
async function pollState() {
  if (POLL_BUSY) return;
  POLL_BUSY = true;
  const disp = DISPLAY;          // the wall this request is ABOUT — drop it if we switch
  try {
    await applyState(await api("/api/current_state"), disp);
  } catch (e) {
    if (disp === DISPLAY) setBadge(true);                    // transient or down: say so
  } finally {
    POLL_BUSY = false;
  }
}

// ---- live preview driver: a reliable poll, promoted to a live SSE stream where it works ---
// Polling always runs first and is NOT torn down until the stream proves itself by delivering
// an event — so the preview never depends on the stream coming up. A stream that never delivers
// (a proxy that buffers or refuses it) just leaves the poll running; one that works takes over
// for near-real-time updates. That self-correction is why we can attempt the stream everywhere —
// including behind Home Assistant's ingress — without special-casing the proxy in front of us.
let ES = null;                   // the EventSource, or null when we aren't streaming
let ES_LIVE = false;             // has the stream proved itself (delivered an event)?
let POLL_TIMER = null;
function stopPolling() {
  if (POLL_TIMER) { clearInterval(POLL_TIMER); POLL_TIMER = null; }
}
function startPolling(ms) {
  if (POLL_TIMER) return;
  pollState();                   // paint at once, then keep it fresh
  POLL_TIMER = setInterval(pollState, ms);
}
function stopPreview() {
  if (ES) { try { ES.close(); } catch (e) {} ES = null; }
  ES_LIVE = false;
  stopPolling();
}
// Start (or re-point, for the wall we just switched to) the live preview.
function startPreview() {
  stopPreview();
  const disp = DISPLAY;
  startPolling(500);             // the reliable baseline — always paints, stream or not
  let es;
  try { es = new EventSource(url("/api/events")); } catch (e) { return; }
  ES = es;
  const live = () => { if (!ES_LIVE) { ES_LIVE = true; stopPolling(); } };   // proven: drop the poll
  es.addEventListener("display", (ev) => {
    live();
    if (disp !== DISPLAY) return;
    let st; try { st = JSON.parse(ev.data); } catch (e) { return; }
    applyState(st, disp);
  });
  es.onerror = () => {
    // The stream dropped or never came up. EventSource retries on its own; meanwhile resume
    // polling so the preview stays live. A later event promotes us off the poll again.
    ES_LIVE = false;
    startPolling(500);
  };
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
  else return;              // printable chars (incl. space) go through the `input` event,
                            // so a soft keyboard that doesn't keydown per char still types
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

  // Tapping a cell focuses the hidden <input> (which opens the on-screen keyboard
  // on iOS/iPadOS — a focused <div> never does). Its keystrokes drive the grid:
  // navigation/backspace through keydown, the printed characters through `input`
  // (so soft keyboards, which don't always keydown per character, work too).
  const catcher = $("cmpCatcher");
  catcher.setAttribute("aria-label", t("Compose"));
  if (!catcher.dataset.wired) {
    catcher.dataset.wired = "1";
    catcher.addEventListener("keydown", cmpKey);
    catcher.addEventListener("input", () => {
      for (const ch of catcher.value) cmpSet(ch === " " ? "" : ch);
      catcher.value = "";
    });
  }
  [...board.children].forEach((cell, i) =>
    cell.addEventListener("click", () => { catcher.focus(); cmpFocus(i); }));

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
      $("cmpCatcher").focus();
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
  // onchange (assignment), NOT addEventListener: cmpBuild reruns on every grid change
  // and display switch, and each run used to stack one more listener on the same
  // persistent <select> — after N switches one change wrote the speed N times.
  sel.onchange = () => {
    // 'slot' is the spin effect and is paced by its own, much slower global.
    $("cmpSpeed").value = sel.value === "slot"
      ? (disp.slot_speed ?? 80)
      : (disp.transition_speed ?? 15);
  };

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
      ["apps", "compose", "playlists", "triggers", "panel"]
        .forEach((p) => $("page-" + p).classList.toggle("hidden", p !== tab));
      const loaders = { apps: loadApps, playlists: loadPlaylists, triggers: loadTriggers, panel: loadPanel };
      if (loaders[tab]) loaders[tab]();
      if (tab === "compose") $("cmpCatcher").focus();   // opens the keyboard on iOS
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

// A tiny amber dot-matrix that echoes the Matrix Portal's LEDs (amber dots on black), so a
// canvas app reads as "draws on the panel" at a glance. Inline SVG — shown in the app library,
// the app cards, and the rich app pickers (which is why those are custom dropdowns, not native
// <select>s: an <option> can hold only plain text, never markup).
function canvasMark() {
  const label = esc(t("Matrix panel"));
  let dots = "";
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++)
    dots += `<circle cx="${4.5 + c * 5}" cy="${4.5 + r * 5}" r="1.7"/>`;
  return `<svg class="app-canvas" viewBox="0 0 24 24" role="img" aria-label="${label}">` +
    `<title>${label}</title>` +
    `<rect x="1" y="1" width="22" height="22" rx="4.5" fill="#0c0c0c" stroke="#2b2b2b"/>` +
    `<g fill="#f5c518">${dots}</g></svg>`;
}

// A single-select that renders rich options — an app's icon, name, the amber dot-matrix
// canvas marker and the 🌐 badge — which a native <select> can't (its <option>s are text
// only). Exposes `.value` (get/set) and calls onChange(id) on pick; keyboard + click, with
// the option list floating as a fixed overlay so it never inflates a modal's scroll height.
function richAppSelect(apps, value, onChange) {
  const box = el("div", "rsel");
  const btn = el("button", "rsel-btn"); btn.type = "button";
  const menu = el("div", "rsel-menu"); menu.style.display = "none";
  let cur = value;
  const optHTML = (a) =>
    `<span class="rsel-ic">${esc(a.icon || "🧩")}</span>` +
    `<span class="rsel-nm">${esc(a.name)}</span>` +
    (a.surface === "canvas" ? canvasMark() : "") +
    (a.i18n ? `<span class="rsel-i18n" title="${esc(t("Multilingual — adapts to the global Language"))}">🌐</span>` : "");
  const drawBtn = () => {
    const a = apps.find((x) => x.id === cur) || apps[0];
    btn.innerHTML = (a ? optHTML(a) : `<span class="rsel-nm">${esc(t("Pick an app"))}</span>`) + `<span class="rsel-caret">▾</span>`;
  };
  const place = () => { const r = btn.getBoundingClientRect(); menu.style.left = r.left + "px"; menu.style.top = r.bottom + 2 + "px"; menu.style.minWidth = r.width + "px"; };
  const onRe = () => place();
  const isOpen = () => menu.style.display !== "none";
  const close = () => { menu.style.display = "none"; window.removeEventListener("scroll", onRe, true); window.removeEventListener("resize", onRe); };
  const open = () => { menu.style.display = ""; place(); window.addEventListener("scroll", onRe, true); window.addEventListener("resize", onRe); (menu.querySelector('[aria-selected="true"]') || menu.firstChild)?.focus(); };
  apps.forEach((a) => {
    const row = el("div", "rsel-opt"); row.innerHTML = optHTML(a);
    row.setAttribute("role", "option"); row.tabIndex = -1;
    if (a.id === cur) row.setAttribute("aria-selected", "true");
    const pick = () => {
      cur = a.id; drawBtn();
      menu.querySelectorAll('[aria-selected]').forEach((n) => n.removeAttribute("aria-selected"));
      row.setAttribute("aria-selected", "true");
      close(); btn.focus(); if (onChange) onChange(cur);
    };
    row.addEventListener("click", pick);
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); (row.nextElementSibling || menu.firstChild).focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); (row.previousElementSibling || menu.lastChild).focus(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); btn.focus(); }
    });
    menu.appendChild(row);
  });
  btn.addEventListener("click", () => (isOpen() ? close() : open()));
  btn.addEventListener("keydown", (e) => { if ((e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") && !isOpen()) { e.preventDefault(); open(); } });
  const maybeClose = () => setTimeout(() => { if (!box.contains(document.activeElement)) close(); }, 120);
  btn.addEventListener("blur", maybeClose);
  menu.addEventListener("focusout", maybeClose);
  box.appendChild(btn); box.appendChild(menu); drawBtn();
  Object.defineProperty(box, "value", { get: () => cur, set: (v) => { cur = v; drawBtn(); } });
  return box;
}

async function loadApps() {
  const data = await api(`/api/apps?lang=${LANG}`);
  APPS = data.apps;
  const grid = $("appsGrid");
  grid.innerHTML = "";
  APPS.forEach((a) => {
    // A canvas app draws to a framebuffer, so it only runs on a wall that has one.
    // On a flap wall it is shown DISABLED with a "Matrix panel only" hint — the same
    // treatment a too-big app gets — rather than hidden, so it is still discoverable.
    const isCanvas = a.surface === "canvas";
    const needsPanel = isCanvas && !CANVAS;
    const fits = appFits(a) && !needsPanel;
    const reqLabel = needsPanel ? t("Matrix panel only") : appReq(a);
    const tile = el("div", "app-tile" + (fits ? "" : " disabled") + (isCanvas ? " is-canvas" : ""));
    tile.dataset.appId = a.id;
    tile.setAttribute("role", "button");
    tile.tabIndex = fits ? 0 : -1;
    if (!fits) {
      tile.title = needsPanel ? t("Matrix panel only") : t("Needs at least %s", appReq(a));
      tile.setAttribute("aria-disabled", "true");
    }
    // name/description/icon come from the app's MANIFEST — an uploaded zip, i.e.
    // attacker-controlled. Everything of it that lands in markup goes through esc().
    tile.innerHTML =
      `<div class="app-icon">${esc(a.icon || "🧩")}</div>` +
      `<div class="app-name">${esc(a.name)}</div>` +
      `<div class="app-desc">${esc(a.description || "")}</div>` +
      (a.has_settings ? `<button class="app-gear" title="${esc(t("Settings"))}">⚙</button>` : "") +
      `<div class="app-foot">` +
        (a.i18n ? `<span class="app-i18n" title="${esc(t("Multilingual — adapts to the global Language"))}">🌐</span>` : "") +
        // A "draws on the panel" marker so a canvas app reads as one at a glance,
        // whether or not this wall can run it.
        (isCanvas ? canvasMark() : "") +
        `<span class="app-badge"></span>` +
        (fits ? "" : `<span class="app-req">${esc(reqLabel)}</span>`) +
      `</div>`;
    const activate = (e) => {
      if (e.target.closest(".app-gear")) { openAppSettings(a.id, a.name); return; }
      if (!fits) return;   // too big for this panel
      runApp(a.id);
    };
    tile.addEventListener("click", activate);
    tile.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(e); }
    });
    grid.appendChild(tile);
  });
  updateActiveUI(data.active_app, data.active_playlist);
}

let ACTIVE_BANNER = null;   // last-painted banner markup — this runs every 300 ms poll
function updateActiveUI(activeApp, activePlaylist) {
  const banner = $("activeBanner");
  let label = "";
  if (activeApp) {
    const a = APPS.find((x) => x.id === activeApp);
    label = a ? a.name : activeApp;
  } else if (activePlaylist) {
    label = t("Playlist · %s", activePlaylist);
  }
  // Word order differs per language ("X is running" / "X läuft"), so the whole
  // sentence is one catalog key with the app name spliced in bold.
  const html = label ? t("▶ %s is running").replace("%s", `<b>${esc(label)}</b>`) : "";
  if (html !== ACTIVE_BANNER) {         // diff: don't rebuild identical DOM 3×/second
    ACTIVE_BANNER = html;
    if (html) { $("activeText").innerHTML = html; banner.classList.remove("hidden"); }
    else banner.classList.add("hidden");
  }
  document.querySelectorAll(".app-tile").forEach((tile) => {
    const on = tile.dataset.appId === activeApp;
    if (tile.classList.contains("running") !== on) tile.classList.toggle("running", on);
    const badge = tile.querySelector(".app-badge");
    const want = on ? t("▶ RUNNING") : "";
    if (badge && badge.textContent !== want) badge.textContent = want;
  });
}

async function runApp(id) {
  try { await post("/api/apps/run", { app: id }); updateActiveUI(id, null); }
  catch (e) { alert(t("Failed: %s", e.message)); }
}
async function stopApp() {
  try { await post("/api/apps/stop"); updateActiveUI(null, null); }
  catch (e) { alert(t("Failed: %s", e.message)); }
}

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
let MODAL_RETURN = null;   // where focus goes back to when the dialog closes
function openModal(title, bodyEl, footButtons) {
  $("modalTitle").textContent = title;
  const body = $("modalBody"); body.innerHTML = ""; body.appendChild(bodyEl);
  const foot = $("modalFoot"); foot.innerHTML = "";
  footButtons.forEach((b) => foot.appendChild(b));
  // Focus moves INTO the dialog on open and back to the opener on close, and the
  // card carries role="dialog"/aria-modal (index.html) — without this a keyboard
  // or screen-reader user is left behind on the now-inert page underneath.
  MODAL_RETURN = document.activeElement;
  $("modal").classList.remove("hidden");
  document.querySelector(".modal-card").focus();
}
function closeModal() {
  $("modal").classList.add("hidden");
  if (MODAL_RETURN && typeof MODAL_RETURN.focus === "function") MODAL_RETURN.focus();
  MODAL_RETURN = null;
}

// ---- app settings form -----------------------------------------------------
function normOpts(options) {
  return (options || []).map((o) => (typeof o === "object" ? o : { value: o, label: String(o) }));
}
// Append this wall's stored animations (firmware 2.1 library, GET /api/panel/library) to a
// select. Best-effort: a non-Matrix or older wall returns none and the field keeps just its
// manifest options. Re-selects the saved value once its option exists.
async function fillAnimLibrary(sel, current) {
  try {
    const lib = await api("/api/panel/library");
    // Each anim is an object {name, frames, w, h, fps, ...} — use its name, not the object
    // (which stringifies to "[object Object]"). Tolerate a bare string too, just in case.
    ((lib && lib.anims) || []).forEach((a) => {
      const name = typeof a === "string" ? a : (a && a.name);
      if (!name || [...sel.options].some((o) => o.value === name)) return;   // skip blanks/dupes
      const op = el("option"); op.value = name; op.textContent = name; sel.appendChild(op);
    });
    if (current != null && current !== "") sel.value = current;
  } catch (e) { /* no library here — the manifest's fallback option stands */ }
}
function chipLabel(v) { return String(v).includes("|") ? String(v).split("|").pop() : v; }

// entity_table <-> the app's `entity_id | Name | low,high` config (one line per row).
function parseEntityRows(val) {
  return String(val || "").split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const parts = line.split("|").map((p) => p.trim());
    let low = "", high = "";
    if (parts[2]) { const n = parts[2].split(",").map((x) => x.trim()); if (n.length === 2) { low = n[0]; high = n[1]; } }
    return { eid: parts[0], name: parts[1] || "", low, high };
  }).filter((r) => r.eid);
}
function serializeEntityRows(rows) {
  return rows.map((r) => {
    const hasThr = r.low !== "" && r.high !== "";
    let line = r.eid;
    if (r.name || hasThr) line += " | " + (r.name || "");
    if (hasThr) line += " | " + r.low + "," + r.high;
    return line;
  }).join("\n");
}

const COMPUTES = {
  polling_usage_estimate(cur) {
    const k = Object.keys(cur).find((x) => x.endsWith("polling_rate"));
    const r = Number(cur[k]) || 0;
    if (!r) return t("Set a polling rate to estimate API usage.");
    const d = Math.round(86400 / r);
    return t("≈ %s requests/day · %sk/month", d.toLocaleString(), (d * 30 / 1000).toFixed(1));
  },
};

// The one form engine behind all three settings dialogs (app / playlist entry /
// global). The field list lives in THIS closure — there is no shared global, so an
// entry-settings dialog opened over the playlists page can never read another
// form's fields. Returns { form, values() }.
function buildForm(schema, initial, { skip } = {}) {
  const fields = [];
  const form = el("div");

  const findField = (key) => fields.find((w) => w._field.key === key);
  const currentValues = () => {
    const c = {};
    fields.forEach((w) => { const v = w._getValue && w._getValue(); if (v !== undefined) c[w._field.key] = v; });
    return c;
  };
  const recompute = () => {
    const cur = currentValues();
    fields.forEach((w) => {
      if (w._field.type === "computed" && w._computeEl) {
        const fn = COMPUTES[w._field.compute];
        w._computeEl.textContent = fn ? fn(cur, w._field) : "";
      }
    });
  };
  const applyVisibility = () => {
    const cur = currentValues();
    fields.forEach((w) => {
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
  };
  const onFormChange = () => { applyVisibility(); recompute(); };

  const applySync = (f, newVal) => {
    if (f.sync_values && f.sync_values[newVal]) {
      Object.entries(f.sync_values[newVal]).forEach(([tk, tv]) => {
        const tw = findField(tk); if (tw && tw._setValue) tw._setValue(tv);
      });
    }
    if (f.sync_parent) {
      const pw = findField(f.sync_parent);
      if (pw && pw._setValue) pw._setValue(f.sync_parent_custom_value || "custom");
    }
  };

  function mkField(f, values) {
  const wrap = el("div", "field"); wrap._field = f; wrap._getValue = () => undefined;
  const val = values[f.key];
  if (f.type === "notice") { const n = el("div", "notice"); n.textContent = t(f.label || f.text || ""); wrap.appendChild(n); return wrap; }
  if (f.type === "computed") {
    if (f.label) { const l = el("span"); l.textContent = t(f.label); wrap.appendChild(l); }
    const n = el("div", "notice"); wrap.appendChild(n); wrap._computeEl = n; return wrap;
  }
  // textContent, not innerHTML: labels arrive from the app's manifest (settings
  // schema), which an uploaded zip controls. No chrome label carries markup.
  const label = el("span"); label.textContent = t(f.label || f.key); wrap.appendChild(label);
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
      // A select fed by the LIVE wall: append the animations stored on the gateway
      // (GET /api/panel/library). The manifest options stay as the leading fallback,
      // so an empty/older/non-Matrix wall still has its "none" choice.
      if (f.options_source === "anim_library") fillAnimLibrary(sel, val);
    }
  } else if (f.type === "textarea") {
    const ta = el("textarea"); ta.rows = 3; ta.value = val ?? "";
    ta.addEventListener("input", onFormChange); wrap.appendChild(ta);
    wrap._getValue = () => ta.value; wrap._setValue = (v) => { ta.value = v; };
  } else if (f.type === "entity_table") {
    // A table of entities: reorder, rename, and set numeric thresholds in one place.
    // Serialises to the app's `entity_id | Name | low,high` config (one line per row, in order).
    let rows = parseEntityRows(val);
    const box = el("div", "entity-table");
    const head = el("div", "et-head");
    ["", t("Entity"), t("Name"), t("Low"), t("High"), ""].forEach((h) => { const c = el("div"); c.textContent = h; head.appendChild(c); });
    const body = el("div", "et-rows");
    const swap = (i, j) => { [rows[i], rows[j]] = [rows[j], rows[i]]; draw(); onFormChange(); };
    const draw = () => {
      body.innerHTML = "";
      if (!rows.length) { const em = el("div", "et-empty"); em.textContent = t("No entities yet — search below to add."); body.appendChild(em); return; }
      rows.forEach((r, i) => {
        const row = el("div", "et-row");
        const ord = el("div", "et-ord");
        const up = el("button"); up.type = "button"; up.textContent = "▲"; up.title = t("Move up"); up.disabled = i === 0;
        up.onclick = () => swap(i, i - 1);
        const dn = el("button"); dn.type = "button"; dn.textContent = "▼"; dn.title = t("Move down"); dn.disabled = i === rows.length - 1;
        dn.onclick = () => swap(i, i + 1);
        ord.appendChild(up); ord.appendChild(dn);
        const eid = el("div", "et-eid"); eid.textContent = r.eid; eid.title = r.eid;
        const name = el("input", "et-name"); name.value = r.name; name.placeholder = t("Name");
        name.addEventListener("input", () => { r.name = name.value; onFormChange(); });
        const low = el("input", "et-num"); low.type = "number"; low.value = r.low; low.placeholder = t("Low");
        low.addEventListener("input", () => { r.low = low.value; onFormChange(); });
        const high = el("input", "et-num"); high.type = "number"; high.value = r.high; high.placeholder = t("High");
        high.addEventListener("input", () => { r.high = high.value; onFormChange(); });
        const del = el("button", "et-del"); del.type = "button"; del.textContent = "✕"; del.title = t("Remove");
        del.onclick = () => { rows.splice(i, 1); draw(); onFormChange(); };
        row.append(ord, eid, name, low, high, del);
        body.appendChild(row);
      });
    };
    box.appendChild(head); box.appendChild(body);
    // Add entities: the same search-and-pick the chip picker uses, results in a floating overlay.
    const searchBox = el("div", "chip-search");
    const search = el("input"); search.placeholder = t("Search…");
    const results = el("div", "chip-results"); results.style.display = "none";
    const placeResults = () => {
      const rc = search.getBoundingClientRect();
      results.style.left = rc.left + "px"; results.style.top = rc.bottom + 2 + "px"; results.style.width = rc.width + "px";
    };
    const onReposition = () => placeResults();
    const showResults = () => { results.style.display = ""; placeResults(); window.addEventListener("scroll", onReposition, true); window.addEventListener("resize", onReposition); };
    const hideResults = () => { results.style.display = "none"; window.removeEventListener("scroll", onReposition, true); window.removeEventListener("resize", onReposition); };
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
            d.setAttribute("role", "button"); d.tabIndex = 0;
            const pick = () => {
              const eid = it.value ?? it.id;
              if (eid && !rows.some((r) => r.eid === eid)) { rows.push({ eid, name: "", low: "", high: "" }); draw(); onFormChange(); }
              search.value = ""; hideResults(); search.focus();
            };
            d.onclick = pick;
            d.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
            results.appendChild(d);
          });
          if (items.length) showResults(); else hideResults();
        } catch { hideResults(); }
      }, 250);
    });
    search.addEventListener("blur", () => setTimeout(() => { if (!results.contains(document.activeElement)) hideResults(); }, 150));
    searchBox.appendChild(search); searchBox.appendChild(results); box.appendChild(searchBox);
    wrap.appendChild(box); draw();
    wrap._getValue = () => serializeEntityRows(rows);
    wrap._setValue = (v) => { rows = parseEntityRows(v); draw(); };
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
            // Keyboard-reachable: each result is a button you can Tab to from the
            // search box and pick with Enter/Space, not a mouse-only target.
            d.setAttribute("role", "button"); d.tabIndex = 0;
            const pick = () => {
              if (maxItems === 1) chips = [];
              if (chips.length < maxItems) { chips.push({ value: it.value ?? it.abbr ?? it.id, label: it.label || it.name || it.value }); draw(); onFormChange(); }
              search.value = ""; hideResults(); search.focus();
            };
            d.onclick = pick;
            d.addEventListener("keydown", (e) => {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
            });
            results.appendChild(d);
          });
          if (items.length) showResults(); else hideResults();
        } catch { hideResults(); }
      }, 250);
    });
    // Deferred, and only if focus didn't move INTO the list — Tabbing from the box
    // to a result must not hide the result out from under the keypress.
    search.addEventListener("blur", () =>
      setTimeout(() => { if (!results.contains(document.activeElement)) hideResults(); }, 150));
    box.appendChild(chipsDiv); box.appendChild(search);
    // A phone (or any device with a GPS/Wi-Fi fix) can fill this in one tap: ask the
    // browser for coordinates, reverse-geocode them to a place chip, and drop it in.
    if (f.geolocate && "geolocation" in navigator) {
      const gpsLabel = "📍 " + t("Use my location");
      const gps = el("button", "gps-btn"); gps.type = "button"; gps.textContent = gpsLabel;
      gps.addEventListener("click", () => {
        gps.disabled = true; gps.textContent = t("Locating…");
        const reset = () => { gps.disabled = false; gps.textContent = gpsLabel; };
        navigator.geolocation.getCurrentPosition(async (pos) => {
          try {
            const d = await api(`/location_reverse?lat=${pos.coords.latitude}&lon=${pos.coords.longitude}`);
            if (d.result) { chips = [{ value: d.result.value, label: d.result.label }]; draw(); onFormChange(); }
          } catch { /* keep whatever was there */ }
          reset();
        }, () => {
          reset();
          alert(t("Couldn't get your location — check the browser's location permission."));
        }, { enableHighAccuracy: true, timeout: 10000 });
      });
      box.appendChild(gps);
    }
    box.appendChild(results); wrap.appendChild(box); draw();
    wrap._getValue = () => chips.map((c) => c.value).join(",");
    wrap._setValue = (v) => { chips = v ? String(v).split(",").filter(Boolean).map((x) => ({ value: x, label: chipLabel(x) })) : []; draw(); };
  } else {
    // text / number / password / date-time (+ optional stepper). The date types
    // pass straight through to the browser's native pickers — datetime-local
    // yields exactly the ISO string datetime.fromisoformat parses, so the
    // countdown's target field is a calendar instead of a guess-the-format box.
    const NATIVE = { password: 1, number: 1, "datetime-local": 1, date: 1, time: 1 };
    const inp = el("input");
    inp.type = NATIVE[f.type] ? f.type : "text";
    if (f.min != null) inp.min = f.min;
    if (f.max != null) inp.max = f.max;
    if (f.step != null) inp.step = f.step;
    if (f.ph) inp.placeholder = t(f.ph);
    // A bare date saved before the picker existed ("2033-06-30") is valid to the
    // backend but rejected by a datetime-local input (it demands a time part) —
    // pad it so old values still show up in the calendar instead of blanking.
    const coerce = (v) =>
      f.type === "datetime-local" && /^\d{4}-\d{2}-\d{2}$/.test(String(v || ""))
        ? `${v}T00:00` : (v != null ? v : "");
    inp.value = val != null && val !== "" ? coerce(val) : "";
    inp.addEventListener("input", onFormChange);
    wrap._getValue = () => (f.type === "number" ? Number(inp.value) : inp.value);
    wrap._setValue = (v) => { inp.value = coerce(v); };
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

  schema.fields.forEach((f) => {
    if (skip && skip(f)) return;
    const w = mkField(f, initial); fields.push(w); form.appendChild(w);
    if (f.inline_toggle) {
      const it = f.inline_toggle;
      const tw = mkField({ key: it.key, type: "toggle", label: "", options: it.options }, initial);
      fields.push(tw); form.appendChild(tw);
    }
  });
  onFormChange();
  return { form, values: currentValues };
}

async function openAppSettings(id, name) {
  const schema = await api(`/api/apps/${id}/settings?lang=${LANG}`);
  const { form, values } = buildForm(schema, schema.values);
  const save = el("button", "btn primary"); save.textContent = t("Save");
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  save.addEventListener("click", async () => {
    msg.textContent = t("Saving…");
    try { await post(`/api/apps/${id}/settings`, { values: values() }); closeModal(); }
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
  const { form, values } = buildForm(schema, base, {
    skip: (f) => f.key && f.key.startsWith("_globals_note_"),   // the shared-globals hint isn't overridable per entry
  });
  const note = el("p", "hint");
  note.textContent = t("These apply to this playlist entry only. Unchanged fields follow the app's own settings.");
  form.prepend(note);
  const save = el("button", "btn primary"); save.textContent = t("Save for this entry");
  const clear = el("button", "btn ghost"); clear.textContent = t("Clear");
  clear.title = t("Remove all per-entry overrides (follow the app's settings)");
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  clear.addEventListener("click", () => { entry.overrides = {}; closeModal(); plRender(); });
  save.addEventListener("click", () => {
    const ov = {};
    Object.entries(values()).forEach(([k, v]) => {
      if (String(v) !== String(schema.values[k] ?? "")) ov[k] = v;   // store only genuine overrides
    });
    entry.overrides = ov;
    closeModal(); plRender();
  });
  openModal(t("%s — entry settings", `${app ? app.icon + " " : ""}${app ? app.name : entry.app}`), form, [msg, clear, save]);
}

async function openGlobalSettings() {
  const schema = await api(`/api/global-settings?lang=${LANG}`);
  const { form, values } = buildForm(schema, schema.values);
  form.className = "gsettings";
  if (!schema.fields.length) {
    const p = el("p", "hint"); p.textContent = t("No global settings yet — install apps that use shared settings (weather, stocks, …).");
    form.appendChild(p);
  }
  const save = el("button", "btn primary"); save.textContent = t("Save");
  const msg = el("span", "hint"); msg.style.marginRight = "auto";
  save.addEventListener("click", async () => {
    msg.textContent = t("Saving…");
    try {
      await post("/api/global-settings", { values: values() });
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
let LIB_CANVAS = false;   // library "Matrix panel apps only" toggle (independent of category)

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
  // A canvas app draws to a Matrix panel's framebuffer — flag it so it reads as one
  // here too (it will run only on a canvas-capable wall).
  if (a.surface === "canvas") {
    const surf = el("span", "lib-tag canvas");
    surf.innerHTML = canvasMark() + " " + esc(t("Matrix panel"));
    tags.appendChild(surf);
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
      try { await del(`/api/apps/${encodeURIComponent(a.id)}`); }
      catch (e) { alert(t("Failed: %s", e.message)); return; }
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
    if (LIB_CANVAS && a.surface !== "canvas") return false;
    const q = LIB_Q.trim().toLowerCase();
    if (!q) return true;
    return [a.name, a.description, a.category, a.id].some((f) => (f || "").toLowerCase().includes(q));
  };
  // A Matrix-only toggle sits alongside the category buttons — but on a different axis (surface,
  // not category), so it toggles independently rather than being one of the exclusive categories.
  const hasCanvas = data.apps.some((a) => a.surface === "canvas");
  const mf = el("button", "lib-filter lib-filter-canvas"); mf.type = "button";
  mf.innerHTML = canvasMark() + " " + esc(t("Matrix"));
  mf.title = t("Matrix panel apps only");
  mf.addEventListener("click", () => { LIB_CANVAS = !LIB_CANVAS; draw(); });
  const draw = () => {
    list.innerHTML = "";
    const shown = data.apps.filter(matches);
    shown.forEach((a) => list.appendChild(libRow(a, openLibrary)));
    if (!shown.length) list.innerHTML = `<span class="hint">${t("No apps match.")}</span>`;
    [...filters.children].forEach((f) => f.classList.toggle("active", f.dataset.cat === LIB_CAT));
    mf.classList.toggle("active", LIB_CANVAS);
  };
  [["", t("All")], ...cats.map((c) => [c, t(titleCase(c))])].forEach(([value, label]) => {
    const f = el("button", "lib-filter");
    f.type = "button"; f.dataset.cat = value; f.textContent = label;
    f.addEventListener("click", () => { LIB_CAT = value; draw(); });
    filters.appendChild(f);
  });
  if (hasCanvas) filters.appendChild(mf);
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
    // Drag the handle to reorder. The handle is the grip (so dragging never
    // starts from the select / inputs); every row is a drop target — dropping
    // onto row i moves the dragged entry to that position.
    const tag = el("span", "handle"); tag.textContent = "⠿ " + (e.type === "app" ? t("App") : t("Msg"));
    tag.draggable = true; tag.title = t("Drag to reorder");
    tag.addEventListener("dragstart", (ev) => {
      ev.dataTransfer.setData("text/plain", String(i));
      ev.dataTransfer.effectAllowed = "move"; row.classList.add("dragging");
    });
    tag.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dragover", (ev) => { ev.preventDefault(); ev.dataTransfer.dropEffect = "move"; row.classList.add("drop-target"); });
    row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
    row.addEventListener("drop", (ev) => {
      ev.preventDefault(); row.classList.remove("drop-target");
      const from = Number(ev.dataTransfer.getData("text/plain"));
      if (Number.isNaN(from) || from === i) return;
      const [moved] = PL_ENTRIES.splice(from, 1);
      PL_ENTRIES.splice(i, 0, moved);
      plRender();
    });
    row.appendChild(tag);
    if (e.type === "app") {
      if (!e.app && APPS[0]) e.app = APPS[0].id;
      // A rich picker (not a native <select>) so the amber dot-matrix marks a canvas app here too.
      const sel = richAppSelect(APPS, e.app, (v) => { e.app = v; e.overrides = {}; plRender(); });
      sel.classList.add("grow"); row.appendChild(sel);
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
    row.dataset.name = n;      // identity, so plMarkEditing can move the highlight in place
    const nm = el("span", "grow"); nm.textContent = n; row.appendChild(nm);
    if (n === PL_NAME) { const tag = el("span", "pill sm"); tag.textContent = t("editing"); row.appendChild(tag); }
    const run = el("button", "btn btn-sm primary"); run.textContent = t("Run");
    run.onclick = async () => {
      try { await post("/api/playlists/run", { entries: SAVED_PL[n].entries, loop: SAVED_PL[n].loop !== false, name: n }); }
      catch (e) { alert(t("Failed: %s", e.message)); }
    };
    row.appendChild(run);
    const load = el("button", "btn btn-sm ghost"); load.textContent = t("Edit"); load.onclick = () => plEdit(n); row.appendChild(load);
    const rm = el("button", "btn btn-sm ghost"); rm.textContent = t("Delete");
    rm.onclick = async () => {
      try { await del("/api/playlists/" + encodeURIComponent(n)); }
      catch (e) { alert(t("Failed: %s", e.message)); return; }
      loadPlaylists();
    };
    row.appendChild(rm);
    saved.appendChild(row);
  });
  if (!PL_ENTRIES.length) plRender();
  plSaveLabel();
}
async function runPlaylistNow() {
  if (!PL_ENTRIES.length) return;
  try { await post("/api/playlists/run", { entries: PL_ENTRIES, loop: $("plLoop").checked, name: PL_NAME || "(unsaved)" }); }
  catch (e) { alert(t("Failed: %s", e.message)); }
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

// Move the "editing" mark to whatever PL_NAME now is, IN PLACE. Nothing about the
// saved list's data changed, so refetching + rebuilding it (what plEdit/plNew used
// to do via loadPlaylists) was a network round trip to move one highlight.
function plMarkEditing() {
  document.querySelectorAll("#plSaved .saved-row").forEach((row) => {
    const on = row.dataset.name === PL_NAME;
    row.classList.toggle("editing", on);
    const pill = row.querySelector(".pill");
    if (on && !pill) {
      const tag = el("span", "pill sm"); tag.textContent = t("editing");
      row.insertBefore(tag, row.children[1] || null);   // right after the name
    } else if (!on && pill) {
      pill.remove();
    }
  });
}

function plEdit(name) {
  PL_ENTRIES = JSON.parse(JSON.stringify(SAVED_PL[name].entries));
  PL_NAME = name;
  $("plName").value = name;
  $("plLoop").checked = SAVED_PL[name].loop !== false;
  plRender();
  plMarkEditing();
}

function plNew() {
  PL_ENTRIES = [];
  PL_NAME = "";
  $("plName").value = "";
  $("plLoop").checked = true;
  plRender();
  plMarkEditing();
}

async function savePlaylist() {
  const name = $("plName").value.trim();
  if (!name) { $("plName").focus(); return; }
  if (!PL_ENTRIES.length) return;
  try {
    await post("/api/playlists", { name, entries: PL_ENTRIES, loop: $("plLoop").checked });
    // A rename is a save under the new name plus a delete of the old — otherwise the one
    // you renamed away from lingers as a stale duplicate of what you just edited.
    if (PL_NAME && PL_NAME !== name) {
      await del("/api/playlists/" + encodeURIComponent(PL_NAME));
    }
  } catch (e) {
    alert(t("Failed: %s", e.message));
    return;
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
  // A rich picker in place of the native <select>, so a canvas app carries the dot-matrix marker.
  const widget = richAppSelect(TRIG_APPS, TRIG_APPS[0]?.id || "", null);
  widget.id = "trigAddApp"; widget.classList.add("grow");
  $("trigAddApp").replaceWith(widget);
  trigRender();
}
function addTrigger() {
  const app = $("trigAddApp").value; if (!app) return;
  const meta = TRIG_APPS.find((a) => a.id === app) || {};
  TRIGS.push({ id: rid("trig_"), app, name: meta.name || app, enabled: true, cooldown: meta.trigger_cooldown || 300, display_seconds: meta.trigger_display_seconds || 30, conditions: {} });
  trigRender();
}
async function saveTriggers() {
  try {
    await post("/api/triggers", { triggers: TRIGS, triggers_enabled: $("trigEnabled").checked });
    $("trigMsg").textContent = t("Saved ✓");
  } catch (e) {
    $("trigMsg").textContent = t("Failed: %s", e.message);
  }
  setTimeout(() => ($("trigMsg").textContent = ""), 4000);
}

// ---- panel (Matrix LED-panel controls) -------------------------------------
// The overlay ticker, transitions, the on-device animation & font libraries and the
// boot splash — all Matrix-only, all gated on what /api/panel/caps advertises, so the
// whole tab is absent on a flap wall and each card is absent on a wall too old for it.
let PANEL_CAPS = null;

function syncPanelTab() {
  const btn = $("tab-panel");
  if (!btn) return;
  btn.classList.toggle("hidden", !CANVAS);
  // Left a canvas wall while the Panel tab was open: fall back to Apps.
  if (!CANVAS && btn.classList.contains("active"))
    document.querySelector('.tab[data-tab="apps"]').click();
}

async function loadPanel() {
  let caps;
  try { caps = await api("/api/panel/caps"); } catch { return; }   // not a canvas wall
  PANEL_CAPS = caps;
  $("panelMeta").textContent = `${caps.width}×${caps.height} · fw ${caps.fw}`;
  $("panelOverlay").classList.toggle("hidden", !caps.overlay);
  $("panelTransition").classList.toggle("hidden", !caps.transition);
  $("panelAnims").classList.toggle("hidden", !caps.anim_library);
  $("panelFonts").classList.toggle("hidden", !caps.fonts);
  await refreshPanelLibrary();
}

async function refreshPanelLibrary() {
  if (!PANEL_CAPS || !(PANEL_CAPS.anim_library || PANEL_CAPS.fonts)) return;
  let lib;
  try { lib = await api("/api/panel/library"); } catch { return; }
  renderAnimList(lib.anims || [], lib.boot || "");
  renderFontList(lib.fonts || []);
}

function _hex2rgb(h) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h || "");
  return m ? [1, 2, 3].map((i) => parseInt(m[i], 16)) : [255, 255, 255];
}

function _panelRow(name, metaText, buttons) {
  const row = el("div", "panel-row");
  const n = el("span", "panel-name"); n.textContent = name;
  const meta = el("span", "hint"); meta.textContent = metaText;
  const sp = el("div", "spacer");
  row.append(n, meta, sp, ...buttons);
  return { row, name: n };
}

function renderAnimList(anims, boot) {
  const box = $("animList"); box.innerHTML = "";
  if (!anims.length) { box.innerHTML = `<span class="hint">${t("None yet.")}</span>`; return; }
  anims.forEach((a) => {
    const play = el("button", "btn ghost btn-sm"); play.textContent = t("Play");
    play.onclick = () => panelDo("/api/panel/anim/play", { name: a.name });
    const bootBtn = el("button", "btn ghost btn-sm");
    bootBtn.textContent = a.name === boot ? t("Unset boot") : t("Set boot");
    bootBtn.onclick = () => panelDo("/api/panel/boot", { name: a.name === boot ? "" : a.name }, refreshPanelLibrary);
    const del = el("button", "btn ghost btn-sm"); del.textContent = t("Delete");
    del.onclick = () => panelDo("/api/panel/anim/delete", { name: a.name }, refreshPanelLibrary);
    const { row, name } = _panelRow(a.name, `${a.frames}f · ${a.fps || "?"}fps`, [play, bootBtn, del]);
    if (a.name === boot) { const p = el("span", "pill"); p.textContent = t("boot"); name.appendChild(p); }
    box.appendChild(row);
  });
}

function renderFontList(fonts) {
  const box = $("fontList"); box.innerHTML = "";
  if (!fonts.length) { box.innerHTML = `<span class="hint">${t("None yet.")}</span>`; return; }
  fonts.forEach((f) => {
    const del = el("button", "btn ghost btn-sm"); del.textContent = t("Delete");
    del.onclick = () => panelDo("/api/panel/font/delete", { name: f.name }, refreshPanelLibrary);
    box.appendChild(_panelRow(f.name, `${f.w}×${f.h}`, [del]).row);
  });
}

async function panelDo(path, body, after) {
  try { await post(path, body); if (after) await after(); }
  catch (e) { $("animMsg").textContent = t("Failed: %s", e.message); }
}

async function _rawPut(path, buf) {
  return api(path, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: buf });
}

function wirePanel() {
  $("ovShow").addEventListener("click", async () => {
    try {
      await post("/api/panel/overlay", {
        text: $("ovText").value, color: _hex2rgb($("ovColor").value),
        speed: +$("ovSpeed").value || 3, band: $("ovBand").checked,
      });
      $("panelMeta").textContent = t("Overlay showing.");
    } catch (e) { $("panelMeta").textContent = t("Failed: %s", e.message); }
  });
  $("ovClear").addEventListener("click", () =>
    post("/api/panel/overlay", { text: "" }).then(() => ($("ovText").value = "")).catch(() => {}));
  $("trApply").addEventListener("click", () =>
    post("/api/panel/transition", { type: $("trType").value, ms: +$("trMs").value || 400 })
      .then(() => ($("panelMeta").textContent = t("Transition set."))).catch(() => {}));

  $("gifUpload").addEventListener("click", async () => {
    const f = $("gifFile").files[0];
    if (!f) { $("animMsg").textContent = t("Choose a GIF first."); return; }
    $("animMsg").textContent = t("Uploading…");
    try {
      const r = await _rawPut("/api/panel/gif", await f.arrayBuffer());
      $("animMsg").textContent = t("Playing %s frames.", String(r.frames || "?"));
      const suggested = f.name.replace(/\.gif$/i, "").toLowerCase().replace(/[^a-z0-9_-]/g, "-").slice(0, 24);
      const name = prompt(t("Save this animation to the panel as (a-z 0-9 - _):"), suggested);
      if (name) { await post("/api/panel/anim/save", { name: name.trim() }); await refreshPanelLibrary(); }
    } catch (e) { $("animMsg").textContent = t("Failed: %s", e.message); }
  });

  $("fontUpload").addEventListener("click", async () => {
    const f = $("fontFile").files[0];
    if (!f) { $("fontMsg").textContent = t("Choose a .fnt file first."); return; }
    try {
      await _rawPut("/api/panel/font", await f.arrayBuffer());
      const name = ($("fontName").value || "").trim();
      if (name) await post("/api/panel/font/save", { name });
      $("fontMsg").textContent = name ? t("Font saved as %s.", name) : t("Font installed (unsaved).");
      $("fontName").value = "";
      await refreshPanelLibrary();
    } catch (e) { $("fontMsg").textContent = t("Failed: %s", e.message); }
  });
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

    // 1b + 1c) The two integration switches (Vestaboard Local API, MCP server) are
    // the same control: a checkbox that flips /api/dev/<name>, and a note that shows
    // the endpoint + credential lines while it's on. One factory, two configs.
    const devToggle = ({ on, api: apiPath, title, offText, lines }) => {
      const F = el("div", "field");
      const lbl = el("label"); lbl.style.cssText = "display:flex;align-items:center;gap:8px;font-weight:600";
      const cb = el("input"); cb.type = "checkbox"; cb.checked = on; cb.style.width = "auto";
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(title));
      F.appendChild(lbl);
      const note = el("small", "field-note");
      F.appendChild(note);

      // Details (the credential + endpoint) only mean anything while it's on.
      const show = async () => {
        if (!cb.checked) { note.textContent = offText; return; }
        note.textContent = t("Loading…");
        try {
          const d = await api(apiPath);
          note.innerHTML = "";
          lines(d).forEach((txt, i) => {
            const div = el("div");
            if (i) div.style.marginTop = "2px";
            div.textContent = txt;
            note.appendChild(div);
          });
        } catch (e) { note.textContent = t("Failed: %s", e.message); }
      };
      cb.addEventListener("change", async () => {
        cb.disabled = true;
        try { render(await post(apiPath, { on: cb.checked })); }
        catch (e) { note.textContent = t("Failed: %s", e.message); cb.disabled = false; }
      });
      show();
      return F;
    };

    wrap.appendChild(devToggle({
      on: !!st.vestaboard,
      api: "/api/dev/vestaboard",
      title: t("Vestaboard API"),
      offText: t("Off. Turn on to accept Vestaboard Local API calls (Home Assistant, scripts) at /local-api/message — this display then answers like a Vestaboard."),
      lines: (d) => [
        // d.url is the address a client OUTSIDE the browser must use. As an add-on our
        // own origin is Home Assistant's (ingress), which does not reach this endpoint.
        `POST ${d.url || `${location.origin}${d.path}`}`,
        `X-Vestaboard-Local-Api-Key: ${d.key}`,
        d.env_key
          ? t("Key pinned by COMPANION_VESTABOARD_KEY.")
          : t("Key generated and stored with your settings. Pin your own with COMPANION_VESTABOARD_KEY."),
      ],
    }));

    wrap.appendChild(devToggle({
      on: !!st.mcp,
      api: "/api/dev/mcp",
      title: t("MCP server"),
      offText: t("Off. Turn on to let an LLM client (Claude, an agent) drive the display as tools at /mcp — show a message, run an app, read the board."),
      lines: (d) => [
        d.url || `${location.origin}${d.path}`,
        `Authorization: Bearer ${d.token}`,
        d.env_token
          ? t("Token pinned by COMPANION_MCP_TOKEN.")
          : t("Token generated and stored with your settings. Pin your own with COMPANION_MCP_TOKEN."),
      ],
    }));

    // 2) Force resync with the gateway
    const reF = el("div", "field");
    const reLbl = el("span"); reLbl.textContent = t("Gateway sync"); reLbl.style.fontWeight = "600"; reF.appendChild(reLbl);
    const reRow = el("div"); reRow.style.cssText = "display:flex;align-items:center;gap:10px;margin-top:6px";
    const reBtn = el("button", "btn ghost btn-sm"); reBtn.textContent = t("↻ Force resync");
    const reMsg = el("small", "field-note"); reMsg.textContent = t("Pull grid geometry from the gateway now.");
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
  CANVAS = !!(me && me.canvas);
  syncPanelTab();

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
  // The caps belong to the wall too: re-derive them from the (already loaded) list so
  // the compose preview and the canvas-app gates follow the wall we just switched to.
  const me = DISPLAYS.find((d) => d.id === id);
  RICH = !!(me && me.rich);
  CANVAS = !!(me && me.canvas);
  syncPanelTab();
  // Everything on screen belongs to the OLD wall — its geometry, its apps, its
  // playlists, its triggers, its gateway's tabs. Re-read the lot rather than trying to
  // patch it, which is how you end up showing one wall's apps on another's grid.
  await bootGrid();
  try { await loadApps(); } catch { /* the rest must still come up */ }
  await loadPlaylists();
  await loadTriggers();
  GW_TRIES = 0;             // the NEW wall's gateway gets its own round of re-asks
  setupGatewayTabs();
  startPreview();           // re-point the live preview at the wall we switched to
}

// ---- Displays: add, rename, re-point, remove, choose the default -------------
async function openDisplays() {
  const body = el("div");
  const list = el("div");
  body.appendChild(list);

  // One POST for both add paths — the manual form and a discovered gateway.
  const addDisplay = async (name, gatewayUrl) => {
    try { await post("/api/displays", { name, gateway_url: gatewayUrl }); return true; }
    catch (e) { alert(e.message || t("Could not add it")); return false; }
  };

  const render = async () => {
    const doc = await api("/api/displays");
    DISPLAYS = doc.displays || [];
    DEFAULT_DISPLAY = doc.default;
    list.innerHTML = "";

    DISPLAYS.forEach((d) => {
      const row = el("div", "display-row");
      const isDefault = d.id === doc.default;

      const name = el("input", "input");
      name.value = d.name;
      name.setAttribute("aria-label", t("Name"));
      const gw = el("input", "input");
      gw.value = d.gateway_url || "";
      gw.placeholder = "http://192.168.1.50";
      gw.setAttribute("aria-label", t("Gateway URL"));

      const info = el("span", "hint");
      info.textContent = d.grid ? `${d.grid.rows}×${d.grid.cols}` : t("not running");

      const save = el("button", "btn btn-sm");
      save.textContent = t("Save");
      // PATCH, not POST — a rename lands at once, a re-point needs a restart.
      save.onclick = async () => {
        let doc2;
        try {
          doc2 = await patch(`/api/displays/${encodeURIComponent(d.id)}`,
            { name: name.value.trim(), gateway_url: gw.value.trim() });
        } catch (e) { alert(t("Failed: %s", e.message)); return; }
        if (doc2 && doc2.restart_required) {
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

      const rm = el("button", "btn btn-sm warn");
      rm.textContent = t("Remove");
      rm.disabled = DISPLAYS.length < 2;
      rm.onclick = async () => {
        if (!confirm(t("Remove this display? Its settings, playlists and triggers are kept.")))
          return;
        try { await del(`/api/displays/${encodeURIComponent(d.id)}`); }
        catch (e) { alert(e.message || t("Could not remove it")); return; }
        if (DISPLAY === d.id) { DISPLAY = ""; localStorage.removeItem("splitflap.display"); }
        await render();
        await loadDisplays();
        await switchDisplay(DEFAULT_DISPLAY);
      };

      // The buttons travel together: in a dialog this narrow the row always wraps, and
      // wrapping them one at a time strands "Remove" alone on a line of its own.
      const acts = el("div", "display-acts");
      acts.append(info, save, mkDefault, rm);
      row.append(name, gw, acts);
      list.appendChild(row);
    });

    // add a wall
    const add = el("div", "display-row");
    const an = el("input", "input"); an.placeholder = t("Office wall");
    const ag = el("input", "input"); ag.placeholder = "http://192.168.1.50";
    const btn = el("button", "btn btn-sm primary");
    btn.textContent = t("Add display");
    btn.onclick = async () => {
      if (!ag.value.trim()) { ag.focus(); return; }
      if (!await addDisplay(an.value.trim() || ag.value.trim(), ag.value.trim())) return;
      an.value = ""; ag.value = "";
      await render();
      await loadDisplays();
    };
    add.append(an, ag, btn);
    list.appendChild(add);

    const note = el("p", "hint");
    note.textContent = t("A new display starts from this one's global settings — location, language and API keys are copied so you don't retype them. They become its own from then on, and are stored on its own gateway.");
    list.appendChild(note);
  };

  // ---- discovery — only here, on demand. A scan probes the LAN (and listens on
  // mDNS where multicast reaches us at all), which is something the user asks for
  // by opening this dialog, never something the companion does in the background.
  const disco = el("div", "discover");
  const dHead = el("div", "discover-head");
  const dTitle = el("span", "hint");
  const rescan = el("button", "btn btn-sm ghost");
  rescan.textContent = t("Scan again");
  dHead.append(dTitle, rescan);
  const dList = el("div");
  disco.append(dHead, dList);
  body.appendChild(disco);

  const scan = async () => {
    rescan.disabled = true;
    dList.innerHTML = "";
    dTitle.textContent = t("Scanning the network for gateways…");
    let found = [];
    try {
      found = (await api("/api/displays/discover")).found || [];
    } catch {
      dTitle.textContent = t("The scan failed — you can still add a gateway by URL above.");
      rescan.disabled = false;
      return;
    }
    rescan.disabled = false;
    const fresh = found.filter((g) => !g.known);
    dTitle.textContent = fresh.length
      ? t("Found on your network:")
      : t("No new gateways found — a gateway must be powered and on this network.");
    fresh.forEach((g) => {
      const row = el("div", "display-row");
      const label = el("span");
      label.textContent = `${g.url} · ${g.rows}×${g.cols}` + (g.version ? ` · v${g.version}` : "");
      const btn = el("button", "btn btn-sm primary");
      btn.textContent = t("Add");
      btn.onclick = async () => {
        btn.disabled = true;
        if (!await addDisplay(g.name || g.url, g.url)) { btn.disabled = false; return; }
        await render();
        await loadDisplays();
        await scan();
      };
      row.append(label, btn);
      dList.appendChild(row);
    });
  };
  rescan.onclick = scan;

  await render();
  openModal(t("Displays"), body, []);
  scan();                      // kicked off after the dialog is up, never blocking it
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
  // Guarded: the version string is cosmetic, and a throw here would abort init()
  // and take the whole UI with it.
  try { const h = await api("/api/health"); $("version").textContent = "v" + h.version; }
  catch (e) { console.error("health failed:", e); }
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
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("modal").classList.contains("hidden")) closeModal();
  });
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
  // panel (Matrix-only; the tab shows only on a canvas wall)
  wirePanel();
  // triggers
  $("trigAdd").addEventListener("click", addTrigger);
  $("trigSave").addEventListener("click", saveTriggers);
  // Guarded: a throw here used to abort init() and take everything after it with it —
  // the gateway tabs never appeared, and the only symptom was a console error.
  try { await loadApps(); } catch (e) { console.error("loadApps failed:", e); }
  setupGatewayTabs();
  openTabFromHash();
  window.addEventListener("hashchange", openTabFromHash);
  startPreview();                // live preview: poll baseline, promoted to SSE where it works
}

init();
