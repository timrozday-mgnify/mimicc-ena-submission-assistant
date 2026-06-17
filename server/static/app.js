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

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
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
// Theme (light/dark/system)
// ---------------------------------------------------------------------------
const THEME_KEY = "mimicc-theme";
const prefersLight = window.matchMedia("(prefers-color-scheme: light)");

function applyTheme(theme) {
  const effective = theme === "system" ? (prefersLight.matches ? "light" : "dark") : theme;
  document.documentElement.setAttribute("data-theme", effective);
}

const savedTheme = localStorage.getItem(THEME_KEY) || "system";
$("themeSelect").value = savedTheme;
applyTheme(savedTheme);

$("themeSelect").onchange = (e) => {
  localStorage.setItem(THEME_KEY, e.target.value);
  applyTheme(e.target.value);
};

prefersLight.addEventListener("change", () => {
  if ($("themeSelect").value === "system") applyTheme("system");
});

// ---------------------------------------------------------------------------
// Submission sessions (named, persisted; restore-all on open)
// ---------------------------------------------------------------------------
let saveTimer = null;
let suppressSave = false;  // true while applying restored state (don't echo back)

function openSessionModal() { loadSessionList(); $("sessionModal").classList.add("show"); }
function closeSessionModal() { $("sessionModal").classList.remove("show"); }

function setSessionChip() {
  $("sessionName").textContent = SESSION ? SESSION.name : "no session";
  document.body.classList.toggle("no-session", !SESSION);
}

function setSessionSaved(isoTs) {
  $("sessionSaved").textContent = isoTs ? "· saved " + new Date(isoTs).toLocaleTimeString() : "";
}

async function loadSessionList() {
  const el = $("sessionList");
  try {
    const sessions = await api("/api/sessions");
    if (!sessions.length) { el.innerHTML = '<p class="muted" style="padding:10px">No sessions yet — create one below.</p>'; return; }
    el.innerHTML = "";
    sessions.forEach((s) => {
      const row = document.createElement("div");
      row.className = "session-row";
      const left = document.createElement("div");
      left.innerHTML = `<b>${s.name}</b><div class="meta">${s.test_env ? "TEST" : "PRODUCTION"} · updated ${new Date(s.updated_at).toLocaleString()}</div>`;
      const actions = document.createElement("div");
      actions.className = "inline";
      const open = document.createElement("button");
      open.className = "btn"; open.style.padding = "4px 12px"; open.textContent = "Open";
      open.onclick = () => openSession(s.id);
      const del = document.createElement("button");
      del.className = "icon-btn danger"; del.textContent = "×"; del.title = "Delete session";
      del.onclick = async () => { if (confirm(`Delete session "${s.name}"? This removes its saved data.`)) { await api(`/api/sessions/${s.id}`, { method: "DELETE" }); loadSessionList(); } };
      actions.append(open, del);
      row.append(left, actions);
      el.appendChild(row);
    });
  } catch (e) { el.innerHTML = `<p class="muted" style="padding:10px">${e.message}</p>`; }
}

async function createSession() {
  const name = $("newSessionName").value.trim();
  if (!name) { banner("sessionBanner", false, "Enter a session name."); return; }
  try {
    const s = await api("/api/sessions", { method: "POST", body: JSON.stringify({ name, test_env: TEST }) });
    $("newSessionName").value = "";
    await openSession(s.id);
  } catch (e) { banner("sessionBanner", false, e.message); }
}

async function openSession(id) {
  try {
    const data = await api(`/api/sessions/${id}`);
    SESSION = data.session;
    READS_RUNS = {};
    (data.reads_runs || []).forEach((r) => { READS_RUNS[r.run_name] = r; });
    setSessionChip();
    closeSessionModal();
    await applyState(data);
    setSessionSaved(data.session.updated_at);
  } catch (e) { banner("sessionBanner", false, e.message); }
}

// Snapshot all user-entered + result/log state (never credentials). Result
// tables and logs are captured as rendered HTML/text so they restore exactly;
// interactive state (run rows, samples, prepared records) is captured as data.
const _FIELD_IDS = [
  "studyJson", "studyHold", "sampleFilter", "sampleChecklist", "sampleHold",
  "defaultStudy", "presetSelect", "recEntity", "recStatus", "dhExport",
];
const _CHECK_IDS = ["studyModify", "studyPublic", "sampleModify", "samplePublic"];
const _RESULT_IDS = ["studyOut", "prepOut", "sampleOut", "recOut", "readsResults"];
const _LOG_IDS = ["readsLog", "recLog"];

function collectState() {
  const fields = {};
  _FIELD_IDS.forEach((id) => { fields[id] = $(id).value; });
  const checks = {};
  _CHECK_IDS.forEach((id) => { checks[id] = $(id).checked; });
  const resultsHtml = {};
  _RESULT_IDS.forEach((id) => { resultsHtml[id] = $(id).innerHTML; });
  const logs = {};
  _LOG_IDS.forEach((id) => { logs[id] = $(id).textContent; });
  return {
    v: 1, test: TEST, fields, checks, resultsHtml, logs,
    runRows: RUN_ROWS, readSamples: READ_SAMPLES, selectedSample: SELECTED_SAMPLE,
    prepared: window.__prepared || null,
  };
}

async function applyState(data) {
  suppressSave = true;
  try {
    const st = data.state || {};
    // Env
    TEST = st.test !== undefined ? st.test : SESSION.test_env;
    $("prodToggle").checked = !TEST;
    $("envPill").textContent = TEST ? "TEST" : "PRODUCTION";
    $("envPill").className = "pill " + (TEST ? "test" : "prod");
    // Fields + checkboxes
    Object.entries(st.fields || {}).forEach(([id, v]) => { if ($(id) != null) $(id).value = v; });
    Object.entries(st.checks || {}).forEach(([id, v]) => { if ($(id) != null) $(id).checked = v; });
    // Result tables + logs (rendered HTML / text)
    Object.entries(st.resultsHtml || {}).forEach(([id, html]) => { if ($(id) != null) $(id).innerHTML = html; });
    Object.entries(st.logs || {}).forEach(([id, txt]) => { if ($(id) != null) $(id).textContent = txt; });
    // Interactive state
    RUN_ROWS = st.runRows || [];
    READ_SAMPLES = st.readSamples || [];
    SELECTED_SAMPLE = st.selectedSample || "";
    window.__prepared = st.prepared || undefined;
    renderRunTable();
    renderReadSampleList();
    $("sampleSubmitBtn").disabled = !(window.__prepared && window.__prepared.length);
    // DataHarmonizer grid: load saved export into the textarea + the grid.
    if (data.dh_export) {
      $("dhExport").value = JSON.stringify(data.dh_export);
      setDhSavedIndicator(data.dh_saved_at);
      loadDhGridWhenReady(data.dh_export);
    } else {
      setDhSavedIndicator(null);
    }
  } finally {
    suppressSave = false;
  }
}

function scheduleSave() {
  if (suppressSave || !SESSION) return;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(saveSessionNow, 1200);
}

async function saveSessionNow() {
  if (!SESSION) return;
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  try {
    const r = await api(`/api/sessions/${SESSION.id}/state`, {
      method: "PUT",
      body: JSON.stringify({ state: collectState(), test_env: TEST }),
    });
    setSessionSaved(r.saved_at);
  } catch { /* transient; next change retries */ }
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

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------
async function saveCreds() {
  try {
    await api("/api/credentials", { method: "POST", body: JSON.stringify({ username: $("username").value, password: $("password").value, test: TEST }) });
    $("password").value = "";
    banner("credBanner", true, `Credentials validated and saved for ${TEST ? "TEST" : "PRODUCTION"} (memory only).`);
    refreshHealth();
  } catch (e) { banner("credBanner", false, e.message); }
}
async function clearCreds() {
  await api("/api/credentials", { method: "DELETE" });
  banner("credBanner", true, "Credentials cleared.");
  refreshHealth();
}
async function refreshHealth() {
  HEALTH = await api("/api/health");
  const s = $("credStatus");
  s.textContent = "credentials: " + (HEALTH.credentials_configured ? "set" : "not set");
  s.className = "creds-status " + (HEALTH.credentials_configured ? "on" : "");
  // Seed the default sample filter only when empty (don't clobber a restored
  // session value).
  if (!$("sampleFilter").value) $("sampleFilter").value = HEALTH.default_sample_filter || "";
  applyActiveReadsDir(HEALTH);
  if (!HEALTH.dh_available) { $("dhWrap").style.display = "none"; $("dhMissing").style.display = "block"; }
  // populate library presets (rebuild so repeated calls don't duplicate options)
  const sel = $("presetSelect");
  const current = sel.value;
  sel.innerHTML = '<option value="">— choose preset —</option>';
  Object.entries(HEALTH.library_presets || {}).forEach(([k, v]) => {
    const o = document.createElement("option"); o.value = k; o.textContent = v.label; sel.appendChild(o);
  });
  sel.value = current;
}

// ---------------------------------------------------------------------------
// Studies
// ---------------------------------------------------------------------------
async function submitStudies() {
  try {
    const records = JSON.parse($("studyJson").value);
    const r = await api("/api/study/submit", { method: "POST", body: JSON.stringify({
      records, test: TEST, modify: $("studyModify").checked,
      hold_until: $("studyHold").value || null, public: $("studyPublic").checked,
    }) });
    banner("studyBanner", r.success, r.success ? `Submitted ${r.accessions.length} study record(s).` : (r.error || "Submission failed."));
    renderTable("studyOut", r.accessions);
    scheduleSave();
  } catch (e) { banner("studyBanner", false, e.message); }
}

// ---------------------------------------------------------------------------
// Samples: DataHarmonizer export integration (button + autosave)
// ---------------------------------------------------------------------------
const DH_AUTOSAVE_INTERVAL_MS = 30000;
let dhAutosaveTimer = null;

// The DataHarmonizer iframe is same-origin, so its window.dataHarmonizer hook
// (added to the DataHarmonizer fork — see web/index.js there) is directly
// reachable. Returns null until the grid has finished loading.
function dhApi() {
  const frame = $("dhFrame");
  const win = frame && frame.contentWindow;
  return win && win.dataHarmonizer && win.dataHarmonizer.ready ? win.dataHarmonizer : null;
}

function formatSavedAt(isoTs) {
  return isoTs ? new Date(isoTs).toLocaleTimeString() : "never";
}

function setDhSavedIndicator(isoTs) {
  $("dhSavedIndicator").textContent = "Last saved: " + formatSavedAt(isoTs);
}

async function saveDhExport(exportJson, { silent = false } = {}) {
  if (!SESSION) { if (!silent) banner("prepBanner", false, "Open a session first."); return; }
  try {
    const r = await api(`/api/sessions/${SESSION.id}/dh-export`, { method: "POST", body: JSON.stringify({ export: exportJson }) });
    $("dhExport").value = JSON.stringify(exportJson);
    setDhSavedIndicator(r.saved_at);
    scheduleSave();
    if (!silent) banner("prepBanner", true, "Exported from DataHarmonizer.");
  } catch (e) {
    if (!silent) banner("prepBanner", false, e.message);
  }
}

function exportDhNow() {
  const dh = dhApi();
  if (!dh) { banner("prepBanner", false, "DataHarmonizer isn't ready yet."); return; }
  saveDhExport(dh.getExportJson());
}

function autosaveDhExport() {
  const dh = dhApi();
  if (!dh || !SESSION) return; // not loaded / no session — skip this tick silently
  saveDhExport(dh.getExportJson(), { silent: true });
}

// Push a saved export object back into the DH grid once the iframe is ready.
function loadDhGridWhenReady(exportObj) {
  if (!exportObj) return;
  const poll = setInterval(() => {
    const dh = dhApi();
    if (!dh || !dh.loadExportJson) return;
    clearInterval(poll);
    try { dh.loadExportJson(exportObj); } catch { /* schema mismatch — leave grid empty */ }
  }, 500);
  setTimeout(() => clearInterval(poll), 15000); // give up after 15s
}

function startDhAutosave() {
  const poll = setInterval(() => {
    if (!dhApi()) return;
    clearInterval(poll);
    if (dhAutosaveTimer) clearInterval(dhAutosaveTimer);
    dhAutosaveTimer = setInterval(autosaveDhExport, DH_AUTOSAVE_INTERVAL_MS);
  }, 500);
}
$("dhFrame").addEventListener("load", startDhAutosave);

// ---------------------------------------------------------------------------
// Samples
// ---------------------------------------------------------------------------
function loadDhFile() {
  const f = $("dhFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => { $("dhExport").value = reader.result; };
  reader.readAsText(f);
}
async function prepareSamples() {
  try {
    const exportJson = JSON.parse($("dhExport").value);
    const r = await api("/api/sample/prepare", { method: "POST", body: JSON.stringify({ export: exportJson, where: $("sampleFilter").value || null }) });
    window.__prepared = r.records;
    banner("prepBanner", true, `Prepared ${r.count} sample record(s). Ready to submit.`);
    renderTable("prepOut", r.records);
    $("sampleSubmitBtn").disabled = r.count === 0;
    scheduleSave();
  } catch (e) { banner("prepBanner", false, e.message); $("sampleSubmitBtn").disabled = true; }
}
async function submitSamples() {
  try {
    const r = await api("/api/sample/submit", { method: "POST", body: JSON.stringify({
      records: window.__prepared || [], test: TEST, modify: $("sampleModify").checked,
      checklist: $("sampleChecklist").value || null, hold_until: $("sampleHold").value || null, public: $("samplePublic").checked,
    }) });
    banner("sampleBanner", r.success, r.success ? `Submitted ${r.accessions.length} sample(s).` : (r.error || "Submission failed."));
    renderSampleSubmission("sampleOut", r);
    if (r.success && Array.isArray(r.accessions)) {
      READ_SAMPLES = r.accessions;
      SELECTED_SAMPLE = "";
      renderReadSampleList();
    }
    scheduleSave();
  } catch (e) {
    banner("sampleBanner", false, e.message);
    renderSampleSubmission("sampleOut", { accessions: [], logs: [`ERROR: ${e.message}`] });
  }
}

// ---------------------------------------------------------------------------
// Reads: active directory + browser
// ---------------------------------------------------------------------------
let BROWSE_PATH = null;

function applyActiveReadsDir(health) {
  $("readsDirLabel").value = health.host_reads_dir || "/reads";
  $("resetDirBtn").style.display = (health.host_reads_dir === health.default_host_reads_dir) ? "none" : "inline-block";
}

function toggleBrowser() {
  const el = $("dirBrowser");
  const show = el.style.display === "none";
  el.style.display = show ? "block" : "none";
  if (show) browseGoTo(BROWSE_PATH || HEALTH.host_reads_dir || "/");
}

async function browseGoTo(path) {
  try {
    const r = await api("/api/reads/browse?path=" + encodeURIComponent(path));
    BROWSE_PATH = r.path;
    $("browsePathInput").value = r.path;
    renderBrowseList(r);
  } catch (e) { banner("readsBanner", false, e.message); }
}

function renderBrowseList(r) {
  const el = $("browseList");
  el.className = "scroll dirlist";
  let h = "";
  if (r.parent !== null) {
    h += `<a href="#" data-path="${r.parent}">.. (up)</a>`;
  }
  if (!r.dirs.length) h += '<p class="muted" style="padding:6px">No subdirectories.</p>';
  for (const d of r.dirs) {
    const full = (r.path === "/" ? "" : r.path) + "/" + d;
    h += `<a href="#" data-path="${full}">${d}/</a>`;
  }
  el.innerHTML = h;
  el.querySelectorAll("a").forEach((a) => {
    a.onclick = (e) => { e.preventDefault(); browseGoTo(a.dataset.path); };
  });
}

function browseGo() { browseGoTo($("browsePathInput").value); }

async function selectBrowsedFolder() {
  try {
    const r = await api("/api/reads/set-dir", { method: "POST", body: JSON.stringify({ path: BROWSE_PATH }) });
    applyActiveReadsDir(r);
    $("dirBrowser").style.display = "none";
    banner("readsBanner", true, `Active reads directory set to ${r.host_reads_dir}.`);
  } catch (e) { banner("readsBanner", false, e.message); }
}

async function resetReadsDir() {
  try {
    const r = await api("/api/reads/set-dir", { method: "POST", body: JSON.stringify({ path: null }) });
    applyActiveReadsDir(r);
    banner("readsBanner", true, `Active reads directory reset to ${r.host_reads_dir}.`);
  } catch (e) { banner("readsBanner", false, e.message); }
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
function blankRun(group) {
  return {
    NAME: group.group, files: group.files, paired: group.paired,
    FASTQ1: group.files_by_mate?.["1"] || "", FASTQ2: group.files_by_mate?.["2"] || "",
    FASTQ: group.paired ? "" : (group.files[0] || ""),
    SAMPLE: group.suggested_sample || "", STUDY: $("defaultStudy").value || "",
    PLATFORM: "", INSTRUMENT: "", LIBRARY_SOURCE: "", LIBRARY_SELECTION: "", LIBRARY_STRATEGY: "",
    confidence: group.confidence || "none", suggested_alias: group.suggested_alias || "",
  };
}

function sampleAccession(sample) {
  return sample.accession || sample.secondary_accession || sample.external_accession || "";
}

function sampleLabel(sample) {
  return sample.alias || sample.title || sampleAccession(sample) || "Unnamed sample";
}

function rowFileCount(row) {
  if (row.paired) {
    return [row.FASTQ1, row.FASTQ2].filter((v) => v && String(v).trim()).length || (row.files || []).length;
  }
  return (row.FASTQ && String(row.FASTQ).trim()) ? 1 : (row.files || []).length;
}

function sampleAssignmentCount(accession) {
  return RUN_ROWS
    .filter((row) => row.SAMPLE === accession)
    .reduce((total, row) => total + rowFileCount(row), 0);
}

function renderReadSampleList() {
  const el = $("readSampleList");
  if (!el) return;
  if (!READ_SAMPLES.length) {
    el.innerHTML = '<p class="muted" style="padding:10px">Load samples to assign accessions.</p>';
    return;
  }

  el.innerHTML = "";
  READ_SAMPLES.forEach((sample) => {
    const accession = sampleAccession(sample);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sample-item" + (accession === SELECTED_SAMPLE ? " selected" : "");
    btn.disabled = !accession;
    btn.onclick = () => {
      SELECTED_SAMPLE = accession;
      renderReadSampleList();
    };

    const main = document.createElement("div");
    main.className = "sample-main";
    const name = document.createElement("span");
    name.className = "sample-name";
    name.textContent = sampleLabel(sample);
    const count = document.createElement("span");
    count.className = "tag sample-count";
    const assigned = sampleAssignmentCount(accession);
    count.textContent = `${assigned} file${assigned === 1 ? "" : "s"}`;
    main.append(name, count);

    const acc = document.createElement("div");
    acc.className = "sample-accession";
    acc.textContent = accession || "No accession";
    btn.append(main, acc);
    el.appendChild(btn);
  });
}

async function loadReadSamples() {
  try {
    const rows = await api(`/api/sample/list?test=${TEST}&status=all`);
    READ_SAMPLES = rows;
    if (!READ_SAMPLES.some((sample) => sampleAccession(sample) === SELECTED_SAMPLE)) {
      SELECTED_SAMPLE = "";
    }
    renderReadSampleList();
    banner("readsBanner", true, `Loaded ${READ_SAMPLES.length} sample(s).`);
  } catch (e) { banner("readsBanner", false, e.message); }
}

async function scanReads() {
  try {
    const r = await api("/api/reads/scan", { method: "POST", body: JSON.stringify({}) });
    RUN_ROWS = r.groups.map(blankRun);
    renderRunTable();
    banner("readsBanner", true, `Found ${r.count} read group(s) in ${r.host_reads_dir}.`);
    scheduleSave();
  } catch (e) { banner("readsBanner", false, e.message); }
}
async function suggestSamples() {
  if (!RUN_ROWS.length) { banner("readsBanner", false, "Scan first."); return; }
  try {
    const groups = RUN_ROWS.map((r) => ({ group: r.NAME, files: r.files, paired: r.paired, files_by_mate: { 1: r.FASTQ1, 2: r.FASTQ2 } }));
    const r = await api("/api/reads/suggest", { method: "POST", body: JSON.stringify({ groups, test: TEST }) });
    r.groups.forEach((g, i) => { if (g.suggested_sample) { RUN_ROWS[i].SAMPLE = g.suggested_sample; RUN_ROWS[i].confidence = g.confidence; } });
    READ_SAMPLES = r.samples;
    renderRunTable();
    renderReadSampleList();
    banner("readsBanner", true, `Auto-assigned ${r.groups.filter((g) => g.suggested_sample).length}/${r.groups.length} group(s).`);
    scheduleSave();
  } catch (e) { banner("readsBanner", false, e.message); }
}
function runStatusCell(name) {
  const led = READS_RUNS[name];
  if (!led) return { text: "—", title: "not yet submitted in this session" };
  const acc = led.run_accession || led.experiment_accession || "";
  if (led.status === "done") return { text: `✓ done ${acc}`.trim(), title: "submitted in this session" };
  if (led.status === "already_in_ena") return { text: `● in ENA ${acc}`.trim(), title: "already present in ENA — skipped on resume" };
  if (led.status === "failed") return { text: "✗ failed", title: "last submission failed" };
  return { text: led.status, title: led.status };
}

function renderRunTable() {
  const cols = ["NAME", "files", "SAMPLE", "STUDY", "PLATFORM", "INSTRUMENT", "LIBRARY_SOURCE", "LIBRARY_SELECTION", "LIBRARY_STRATEGY"];
  const head = $("runTable").querySelector("thead");
  const body = $("runTable").querySelector("tbody");
  head.innerHTML = "<tr><th></th>" + cols.map((c) => `<th>${c}</th>`).join("") + "<th>status</th><th>re-upload</th></tr>";
  body.innerHTML = "";
  RUN_ROWS.forEach((row, i) => {
    const tr = document.createElement("tr");
    tr.className = SELECTED_SAMPLE ? "assignable" : "";
    tr.onclick = (e) => {
      if (!SELECTED_SAMPLE || e.target.closest("input,button,select,textarea")) return;
      RUN_ROWS[i].SAMPLE = SELECTED_SAMPLE;
      RUN_ROWS[i].confidence = "manual";
      renderRunTable();
      renderReadSampleList();
      scheduleSave();
    };

    const removeTd = document.createElement("td");
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "icon-btn danger";
    removeBtn.title = "Remove row";
    removeBtn.textContent = "×";
    removeBtn.onclick = (e) => {
      e.stopPropagation();
      RUN_ROWS.splice(i, 1);
      renderRunTable();
      renderReadSampleList();
      scheduleSave();
    };
    removeTd.appendChild(removeBtn);
    tr.appendChild(removeTd);

    cols.forEach((c) => {
      const td = document.createElement("td");
      if (c === "files") {
        td.className = "wrap";
        td.innerHTML = row.files.join("<br>") + (row.confidence === "high" ? ' <span class="tag high">auto</span>' : "");
      } else {
        const inp = document.createElement("input");
        inp.value = row[c] || "";
        inp.oninput = (e) => {
          RUN_ROWS[i][c] = e.target.value;
          if (c === "SAMPLE" || c === "FASTQ" || c === "FASTQ1" || c === "FASTQ2") renderReadSampleList();
          scheduleSave();
        };
        td.appendChild(inp);
      }
      tr.appendChild(td);
    });

    // status (resume ledger)
    const statusTd = document.createElement("td");
    const st = runStatusCell(row.NAME);
    statusTd.textContent = st.text;
    statusTd.title = st.title;
    statusTd.className = "wrap";
    tr.appendChild(statusTd);

    // re-upload toggle
    const reTd = document.createElement("td");
    const reChk = document.createElement("input");
    reChk.type = "checkbox";
    reChk.style.width = "auto";
    reChk.checked = !!row.reupload;
    reChk.title = "Re-submit this run under a fresh alias even if it's already in ENA";
    reChk.onchange = (e) => { RUN_ROWS[i].reupload = e.target.checked; scheduleSave(); };
    reChk.onclick = (e) => e.stopPropagation();
    reTd.appendChild(reChk);
    tr.appendChild(reTd);

    body.appendChild(tr);
  });
  const has = RUN_ROWS.length > 0;
  $("readsSubmitBtn").disabled = !has;
  $("readsValidateBtn").disabled = !has;
  renderReadSampleList();
}
function applyPreset() {
  const p = (HEALTH.library_presets || {})[$("presetSelect").value];
  if (!p) return;
  RUN_ROWS.forEach((r) => { ["PLATFORM", "INSTRUMENT", "LIBRARY_SOURCE", "LIBRARY_SELECTION", "LIBRARY_STRATEGY"].forEach((k) => (r[k] = p[k])); });
  const s = $("defaultStudy").value;
  if (s) RUN_ROWS.forEach((r) => { if (!r.STUDY) r.STUDY = s; });
  renderRunTable();
  scheduleSave();
}
async function submitReads(doSubmit) {
  $("readsLog").textContent = "";
  $("readsResults").innerHTML = "";
  try {
    const runs = RUN_ROWS.map((r) => {
      const o = { NAME: r.NAME, STUDY: r.STUDY, SAMPLE: r.SAMPLE, PLATFORM: r.PLATFORM, INSTRUMENT: r.INSTRUMENT,
        LIBRARY_SOURCE: r.LIBRARY_SOURCE, LIBRARY_SELECTION: r.LIBRARY_SELECTION, LIBRARY_STRATEGY: r.LIBRARY_STRATEGY,
        reupload: !!r.reupload };
      if (r.paired) { o.FASTQ1 = r.FASTQ1; o.FASTQ2 = r.FASTQ2; } else { o.FASTQ = r.FASTQ; }
      return o;
    });
    const { job_id } = await api("/api/reads/submit", { method: "POST", body: JSON.stringify({
      runs, test: TEST, submit: doSubmit,
      session_id: SESSION ? SESSION.id : null, force_reupload: $("forceReupload").checked,
    }) });
    streamReads(job_id);
  } catch (e) { banner("readsBanner", false, e.message); }
}
function streamReads(jobId) {
  const log = $("readsLog");
  const results = [];
  const es = new EventSource(`/api/reads/stream/${jobId}`);
  es.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.error) { banner("readsBanner", false, m.error); es.close(); return; }
    if (m.line != null) { log.textContent += m.line + "\n"; log.scrollTop = log.scrollHeight; }
    if (m.result) {
      results.push(m.result);
      // Update the per-run resume ledger from each result as it lands.
      const r = m.result;
      if (r.name) {
        READS_RUNS[r.name] = {
          run_name: r.name,
          status: r.skipped ? (r.reason === "already_in_ena" ? "already_in_ena" : "done")
            : (r.success ? "done" : "failed"),
          experiment_accession: r.experiment_accession || "",
          run_accession: r.run_accession || "",
        };
      }
    }
    if (m.done) {
      const ok = m.results.every((r) => r.success);
      const skipped = m.results.filter((r) => r.skipped).length;
      banner("readsBanner", ok,
        ok ? `Done: ${m.results.length} run(s)${skipped ? `, ${skipped} skipped (already in ENA)` : ""}.`
           : "Some runs failed — see results.");
      renderTable("readsResults", m.results);
      renderRunTable();   // refresh per-row status column
      saveSessionNow();   // persist log + results + ledger immediately
      es.close();
    }
  };
  es.onerror = () => { es.close(); };
}

// ---------------------------------------------------------------------------
// Records browser
// ---------------------------------------------------------------------------
const RECORD_COLUMNS = {
  studies: ["accession", "secondary_accession", "alias", "title", "status"],
  samples: ["accession", "secondary_accession", "alias", "title", "status"],
  runs: ["accession", "alias", "experiment_accession", "study_accession", "sample_accession", "status"],
  experiments: ["accession", "alias", "title", "study_accession", "sample_accession", "status"],
  analyses: ["accession", "alias", "title", "study_accession", "status"],
};

function appendLog(id, text) {
  const el = $(id);
  if (!el) return;
  const ts = new Date().toISOString().slice(11, 23);
  el.textContent += `[${ts}] ${text}\n`;
  el.scrollTop = el.scrollHeight;
}

function clearLog(id) {
  const el = $(id);
  if (el) el.textContent = "";
}

async function loadRecords(entity, outId, status = "all", withActions = false) {
  appendLog("recLog", `Fetching ${entity} (status=${status}, test=${TEST})…`);
  try {
    const rows = await api(`/api/records/${entity}?test=${TEST}&status=${status}`);
    appendLog("recLog", `Got ${rows.length} ${entity} row(s).`);
    if (rows.length) {
      // Log every field actually present (not just the columns the table
      // shows) — this surfaces raw Reports API keys the alias mapping
      // didn't recognise, which is exactly what's needed to debug a blank
      // linking accession column.
      const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
      appendLog("recLog", `Fields present: ${keys.join(", ")}`);
      appendLog("recLog", `First row: ${JSON.stringify(rows[0])}`);
    }
    if (withActions) renderRecordsWithActions(outId, entity, rows);
    else renderTable(outId, rows);
    if ($("recBanner") && outId === "recOut") banner("recBanner", true, `${rows.length} ${entity}.`);
    scheduleSave();
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    if ($("recBanner")) banner("recBanner", false, e.message);
  }
}
function renderRecordsWithActions(outId, entity, rows) {
  const el = $(outId);
  if (!rows.length) { el.innerHTML = '<p class="muted" style="padding:10px">No records.</p>'; return; }
  const cols = RECORD_COLUMNS[entity] || ["accession", "alias", "title", "status"].filter((c) => rows.some((r) => c in r));
  let h = "<table><thead><tr>" + cols.map((c) => `<th>${c}</th>`).join("") + "<th>actions</th></tr></thead><tbody>";
  rows.forEach((r) => {
    const acc = r.accession || r.secondary_accession || "";
    h += "<tr>" + cols.map((c) => `<td>${r[c] == null ? "" : String(r[c])}</td>`).join("");
    h += `<td>
      <button class="btn secondary" style="padding:3px 8px" onclick="recAction('release','${acc}')">release</button>
      <button class="btn secondary" style="padding:3px 8px" onclick="recAction('hold','${acc}')">hold</button>
      <button class="btn secondary" style="padding:3px 8px" onclick="recAction('suppress','${acc}')">suppress</button>
      <button class="btn danger" style="padding:3px 8px" onclick="recAction('cancel','${acc}')">cancel</button>
    </td></tr>`;
  });
  el.innerHTML = h + "</tbody></table>";
}
async function recAction(action, accession) {
  let hold = null;
  if (action === "hold") { hold = prompt("Hold until (YYYY-MM-DD):"); if (!hold) return; }
  if ((action === "cancel" || action === "kill") && !confirm(`${action} ${accession}?`)) return;
  appendLog("recLog", `${action} ${accession}…`);
  try {
    const r = await api("/api/records/action", { method: "POST", body: JSON.stringify({ action, accession, test: TEST, hold_until: hold }) });
    appendLog("recLog", `${action} ${accession}: ${r.success ? "ok" : "failed"} — ${r.messages || ""}`);
    banner("recBanner", r.success, `${action} ${accession}: ${r.success ? "ok" : "failed"} — ${r.messages || ""}`);
    scheduleSave();
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    banner("recBanner", false, e.message);
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

// Persist field edits (text inputs, selects, checkboxes) as the user types.
// Credentials inputs are excluded — they are never part of session state.
document.querySelector("main").addEventListener("input", (e) => {
  if (e.target.id === "username" || e.target.id === "password" || e.target.id === "browsePathInput") return;
  scheduleSave();
});
document.querySelector("main").addEventListener("change", (e) => {
  if (e.target.id === "username" || e.target.id === "password") return;
  scheduleSave();
});

async function init() {
  await refreshHealth();
  setSessionChip();          // no session yet -> body.no-session (blurs/locks tabs)
  openSessionModal();        // force a session pick on load
}
init();
