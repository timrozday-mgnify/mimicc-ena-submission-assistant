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

// Echoes Django's csrftoken cookie back as a header, satisfying CsrfViewMiddleware
// in hosted mode; a cross-site request cannot read this same-origin cookie.
function csrfHeaders() {
  const m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
  return m ? { "X-CSRFToken": decodeURIComponent(m[1]) } : {};
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    credentials: "same-origin",
    ...opts,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (res.status === 401 && HEALTH && HEALTH.deployment_mode === "hosted" && !path.startsWith("/api/auth/")) {
    // Session expired / not signed in — surface the login overlay.
    $("loginModal").classList.add("show");
  }
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
  el.className = "banner " + (ok ? "ok" : "bad");
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

function renderSampleSubmission(containerId, result) {
  const el = $(containerId);
  el.innerHTML = "";

  const logs = Array.isArray(result.logs) ? result.logs : [];
  if (logs.length) {
    const pre = document.createElement("pre");
    pre.className = "log";
    pre.textContent = logs.join("\n");
    el.appendChild(pre);
  }

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
// Tabs + env toggle
// ---------------------------------------------------------------------------
document.querySelectorAll("nav button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "admin") loadUsers();
  };
});

$("prodToggle").onchange = (e) => {
  TEST = !e.target.checked;
  const pill = $("envPill");
  pill.textContent = TEST ? "TEST" : "PRODUCTION";
  pill.className = "pill " + (TEST ? "test" : "prod");
  if (!TEST && !confirm("Switch to PRODUCTION ENA service? Submissions will be permanent.")) {
    e.target.checked = false; TEST = true; pill.textContent = "TEST"; pill.className = "pill test";
  }
  scheduleSave();
};
