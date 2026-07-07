// SplitFlapGatewayCompanion — Phase 1 SPA (vanilla).
// Live preview + click-to-type compose grid + settings.

const COLORS = { r: "r", o: "o", y: "y", g: "g", b: "b", p: "p", w: "w" };
const COLOR_CODES = ["r", "o", "y", "g", "b", "p", "w"];
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
  const c = (ch || " ").toLowerCase();
  return COLORS[c] ? `flap color-${c}` : "flap";
}
function glyph(ch) {
  // color codes render as an empty colored tile
  return COLOR_CODES.includes((ch || "").toLowerCase()) ? "" : (ch || "");
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
  // color tiles show no glyph
  if (COLOR_CODES.includes((inp.value || "").toLowerCase())) inp.style.color = "transparent";
  else inp.style.color = "";
}

function composeString() {
  const inputs = $("composeGrid").querySelectorAll("input");
  let s = "";
  inputs.forEach((inp) => (s += inp.value || " "));
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

// ---- settings --------------------------------------------------------------
async function loadConfig() {
  const cfg = await api("/api/config");
  $("cfgRows").value = cfg.grid.rows;
  $("cfgCols").value = cfg.grid.cols;
  $("cfgBase").value = cfg.grid.module_id_base;
  $("cfgTransport").value = cfg.transport.type;
  $("cfgGatewayUrl").value = cfg.transport.gateway_url || "";
  $("cfgAutoSync").checked = cfg.sync_from_gateway !== false;
  const m = cfg.transport.mqtt || {};
  $("cfgMqttBroker").value = m.broker || "";
  $("cfgMqttPort").value = m.port || 1883;
  $("cfgMqttPrefix").value = m.prefix || "splitflap";
  $("cfgMqttUser").value = m.username || "";
  $("cfgMqttPass").value = m.password === "********" ? "" : (m.password || "");
  $("gatewayLink").href = cfg.transport.gateway_url || "#";
  toggleMqttFields();
  applyAutoSyncLock();
}
function toggleMqttFields() {
  document.querySelectorAll(".mqtt-only").forEach(
    (el) => (el.style.display = $("cfgTransport").value === "mqtt" ? "" : "none")
  );
}
// When auto-sync is on, the gateway owns rows/cols + MQTT broker/port/user/prefix,
// so we lock those fields (password + ID base + transport type stay editable).
function applyAutoSyncLock() {
  const locked = $("cfgAutoSync").checked;
  ["cfgRows", "cfgCols", "cfgMqttBroker", "cfgMqttPort", "cfgMqttPrefix", "cfgMqttUser"]
    .forEach((id) => { $(id).disabled = locked; $(id).title = locked ? "From gateway" : ""; });
}
async function syncFromGateway() {
  $("syncMsg").textContent = "Syncing…";
  try {
    const url = $("cfgGatewayUrl").value.trim();
    if (url) await post("/api/config", { transport: { gateway_url: url } });
    const r = await post("/api/gateway/sync");
    if (!r.ok) { $("syncMsg").textContent = "Gateway error: " + r.error; return; }
    await loadConfig(); await bootGrid();
    $("syncMsg").textContent = `Synced ✓ ${r.gateway.gridRows}×${r.gateway.gridCols}, broker ${r.gateway.mqHost || "—"}`;
  } catch (e) { $("syncMsg").textContent = "Error: " + e.message; }
}
async function saveSettings() {
  const autoSync = $("cfgAutoSync").checked;
  const patch = {
    sync_from_gateway: autoSync,
    grid: { module_id_base: +$("cfgBase").value },
    transport: { type: $("cfgTransport").value, gateway_url: $("cfgGatewayUrl").value.trim(), mqtt: {} },
  };
  // Only push gateway-owned fields when the user is in manual mode; otherwise
  // we'd overwrite freshly-synced values with stale UI values.
  if (!autoSync) {
    patch.grid.rows = +$("cfgRows").value;
    patch.grid.cols = +$("cfgCols").value;
    patch.transport.mqtt.broker = $("cfgMqttBroker").value.trim();
    patch.transport.mqtt.port = +$("cfgMqttPort").value;
    patch.transport.mqtt.prefix = $("cfgMqttPrefix").value.trim() || "splitflap";
    patch.transport.mqtt.username = $("cfgMqttUser").value;
  }
  const pass = $("cfgMqttPass").value;
  if (pass) patch.transport.mqtt.password = pass;
  $("settingsMsg").textContent = "Applying…";
  try {
    await post("/api/config", patch);
    await loadConfig();
    await bootGrid();
    $("settingsMsg").textContent = "Saved ✓";
    setTimeout(() => ($("settingsMsg").textContent = ""), 2500);
  } catch (e) { $("settingsMsg").textContent = "Error: " + e.message; }
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
      ["compose", "settings", "display"].forEach((p) => $("page-" + p).classList.toggle("hidden", p !== t.dataset.tab));
      if (t.dataset.tab === "display") refreshGatewayDump();
    })
  );
}
async function refreshGatewayDump() {
  try {
    const s = await api("/api/gateway/status");
    $("gatewayDump").textContent = JSON.stringify(s, null, 2);
  } catch (e) { $("gatewayDump").textContent = "error: " + e.message; }
}

async function init() {
  const h = await api("/api/health"); $("version").textContent = "v" + h.version;
  wireTabs();
  buildPalette();
  await bootGrid();
  await loadConfig();
  $("sendBtn").addEventListener("click", send);
  $("clearGridBtn").addEventListener("click", clearGrid);
  $("clearDisplayBtn").addEventListener("click", () => post("/api/display/clear"));
  $("quickInput").addEventListener("input", quickFill);
  $("saveSettingsBtn").addEventListener("click", saveSettings);
  $("cfgTransport").addEventListener("change", toggleMqttFields);
  $("syncBtn").addEventListener("click", syncFromGateway);
  $("cfgAutoSync").addEventListener("change", applyAutoSyncLock);
  pollState(); pollStatus();
  setInterval(pollState, 300);
  setInterval(pollStatus, 3000);
}

init();
