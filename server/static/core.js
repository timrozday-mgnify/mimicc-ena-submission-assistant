"use strict";

// ---------------------------------------------------------------------------
// Global state + helpers
// ---------------------------------------------------------------------------
let TEST = true;            // ENA test vs production
let HEALTH = {};
let RUN_ROWS = [];          // editable run records for the Reads tab
let READ_SAMPLES = [];      // ENA samples available for read assignment
let SELECTED_SAMPLE = "";   // selected sample accession for click assignment
let SESSION = null;         // active submission session {id, name, test_env}
let READS_RUNS = {};        // run_name -> reads ledger row (resume status) for the active session

const $ = (id) => document.getElementById(id);

let HELPER_BASE = "";       // base URL of the local reads upload helper, e.g. http://localhost:9100
let HELPER_OK = false;      // whether the helper is currently reachable

// Webin (ENA) credentials live in the browser for this tab only (sessionStorage,
// see credentials.js) and ride along on every API call as headers — the
// stateless local backend reads them per-request (server/webin_creds.py).
let CREDS = { username: "", password: "" };
function webinHeaders() {
  return CREDS.username && CREDS.password
    ? { "X-Webin-Username": CREDS.username, "X-Webin-Password": CREDS.password }
    : {};
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...webinHeaders() },
    ...opts,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// Call the local reads upload helper (cross-origin to 127.0.0.1:<helper_port>).
async function helperApi(path, opts = {}) {
  const res = await fetch(HELPER_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (!res.ok) throw new Error(body.detail || `helper HTTP ${res.status}`);
  return body;
}

function banner(id, ok, msg) {
  const el = $(id);
  if (msg) {
    el.className = "vf-banner vf-banner--alert " + (ok ? "vf-banner--success" : "vf-banner--danger");
    el.style.display = "block";
  } else {
    el.style.display = "none";
  }
  el.textContent = msg;
}

function renderTable(containerId, rows) {
  const el = $(containerId);
  if (!rows || !rows.length) { el.innerHTML = '<p class="muted" style="padding:10px">No records.</p>'; return; }
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  let h = "<table><thead><tr>" + cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) {
    h += "<tr>" + cols.map((c) => `<td>${r[c] == null ? "" : String(r[c])}</td>`).join("") + "</tr>";
  }
  el.innerHTML = h + "</tbody></table>";
}

function renderSubmissionResult(containerId, result) {
  const el = $(containerId);
  el.innerHTML = "";

  const pre = document.createElement("pre");
  pre.className = "log";
  pre.textContent = submissionLogText(result);
  el.appendChild(pre);

  const rows = result.accessions || [];
  const tableSlot = document.createElement("div");
  el.appendChild(tableSlot);
  if (!rows.length) {
    tableSlot.innerHTML = '<p class="muted" style="padding:10px">No accession records returned.</p>';
    return;
  }

  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  let h = "<table><thead><tr>" + cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) {
    h += "<tr>" + cols.map((c) => `<td>${r[c] == null ? "" : String(r[c])}</td>`).join("") + "</tr>";
  }
  tableSlot.innerHTML = h + "</tbody></table>";
}

function renderSampleSubmission(containerId, result) {
  renderSubmissionResult(containerId, result);
}

function submissionLogText(result) {
  const logs = Array.isArray(result.logs) ? result.logs : [];
  if (logs.length) return logs.join("\n");
  if (result.error) return `ERROR: ${result.error}`;
  return "No submission log lines returned.";
}

function renderSubmissionLog(containerId, result) {
  const el = $(containerId);
  if (el) el.textContent = submissionLogText(result);
}

function submissionFailureMessage(result, fallback = "Submission failed.") {
  const logs = Array.isArray(result.logs) ? result.logs : [];
  const diagnostic = [...logs].reverse().find((line) => /^(ERROR|WARNING):/.test(line));
  if (diagnostic) {
    return diagnostic
      .replace(/^(ERROR|WARNING):\s*/, "")
      .replace(/^Receipt:\s*/, "")
      .replace(/^ERROR:\s*/, "");
  }
  if (result.error) return result.error;
  if (logs.length) return "Submission failed; see the log for the last completed step.";
  return fallback;
}

function togglePanelMax(panelId) {
  const panel = $(panelId);
  if (!panel) return;
  const wasMaximized = panel.classList.contains("maximized");
  document.querySelectorAll(".panel.maximized").forEach((el) => {
    el.classList.remove("maximized");
    const btn = el.querySelector(".panel-head .maximize-toggle-btn");
    if (btn) {
      btn.textContent = "⛶";
      btn.title = "Maximize panel";
      btn.setAttribute("aria-label", "Maximize panel");
    }
  });

  if (!wasMaximized) {
    panel.classList.add("maximized");
    const btn = panel.querySelector(".panel-head .maximize-toggle-btn");
    if (btn) {
      btn.textContent = "−";
      btn.title = "Minimize panel";
      btn.setAttribute("aria-label", "Minimize panel");
    }
  }
  document.body.classList.toggle("has-maximized-panel", !wasMaximized);
}

// ---------------------------------------------------------------------------
// Stop a DataHarmonizer iframe from silently reclaiming keyboard focus once
// the user has clicked elsewhere. Handsontable tracks its own "isListening"
// state (a module-level `activeGuid`, set via `hot.listen()`) completely
// decoupled from real DOM focus — outsideClickDeselects:false is set
// deliberately upstream — and keeps re-asserting it via real focus/select
// calls on its own elements (see dataharmonizer.js's
// disableIframeFocusWhenInactive for exactly which APIs and why). Confirmed
// via a live console focus log that this reclaim can happen with NO bubbling
// "focusin" the parent document ever sees, so nothing here can react to it
// after the fact — hence blocking those calls at the source instead.
// `dataset.userActive` gates that blocking; it's turned OFF here (any
// mousedown that doesn't land on a given iframe means the user has moved on
// from it), but turned ON from *inside* that iframe's own document, in
// disableIframeFocusWhenInactive — a capturing-phase listener on the iframe's
// own `document` is guaranteed to run before Handsontable's own listener on
// the same document, whereas the ordering between this (parent-document)
// listener and the iframe's internal one is not guaranteed at all, and
// turning it on from here was observed to lose that race (blocking
// Handsontable's own legitimate selection setup and breaking editing).
// ---------------------------------------------------------------------------
document.addEventListener("mousedown", (e) => {
  const activeFrame = e.target.closest("iframe");
  document.querySelectorAll(".dh-embed-frame").forEach((f) => {
    if (f !== activeFrame) f.dataset.userActive = "0";
  });
}, true);

// Handsontable's shortcut recorder (shortcuts/recorder.mjs `mount()`) walks
// up the window hierarchy and attaches its own keydown/keyup listener
// directly to every ANCESTOR window's `documentElement` too — i.e. it puts a
// listener on THIS page's `documentElement`, from inside each DH iframe,
// entirely deliberately (documented as supporting shortcuts while embedded).
// It's gated only by Handsontable's own `isListening()` flag, which has no
// public API to clear and is completely decoupled from real DOM focus, so a
// key pressed in an ordinary field here (e.g. the sample filter input) can
// still bubble up to that listener and get treated as a grid shortcut
// (Enter advances a row, Backspace deletes a cell) even though the grid
// never had real focus. Stopping propagation at `body` — one level below
// `documentElement`, where Handsontable's listener actually lives — lets the
// keystroke's target (and anything between it and `body`) see the event
// normally, then keeps it from ever reaching that injected listener.
document.body.addEventListener("keydown", (e) => e.stopImmediatePropagation());
document.body.addEventListener("keyup", (e) => e.stopImmediatePropagation());

// ---------------------------------------------------------------------------
// Env toggle (tab switching handled by VF scripts.js via data-vf-js-tabs)
// ---------------------------------------------------------------------------
$("prodToggle").onchange = (e) => {
  TEST = !e.target.checked;
  const pill = $("envPill");
  pill.textContent = TEST ? "TEST" : "PRODUCTION";
  pill.className = "vf-badge " + (TEST ? "vf-badge--primary" : "vf-badge--secondary");
  if (!TEST && !confirm("Switch to PRODUCTION ENA service? Submissions will be permanent.")) {
    e.target.checked = false; TEST = true; pill.textContent = "TEST"; pill.className = "vf-badge vf-badge--primary";
  }
  scheduleSave();
};
