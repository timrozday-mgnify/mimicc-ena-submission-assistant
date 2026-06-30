"use strict";

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
  "defaultStudy", "recEntity", "recStatus", "dhExport", "readsLocalDir",
];
const _CHECK_IDS = ["studyModify", "studyPublic", "sampleModify", "samplePublic", "forceReupload"];
const _RESULT_IDS = ["studyOut", "prepOut", "sampleOut", "recOut", "readsResults"];
const _LOG_IDS = ["readsLog", "recLog"];

// Pristine, blank-slate values for every field/check/result/log, captured
// once at page load (before any session is applied) — see init(). Used to
// fully reset the form between sessions so a new (or different) session
// never shows a previous session's leftover data.
let INITIAL_DEFAULTS = null;

function captureInitialDefaults() {
  const fields = {};
  _FIELD_IDS.forEach((id) => { fields[id] = $(id).value; });
  const checks = {};
  _CHECK_IDS.forEach((id) => { checks[id] = $(id).checked; });
  const resultsHtml = {};
  _RESULT_IDS.forEach((id) => { resultsHtml[id] = $(id).innerHTML; });
  const logs = {};
  _LOG_IDS.forEach((id) => { logs[id] = $(id).textContent; });
  INITIAL_DEFAULTS = { fields, checks, resultsHtml, logs };
}

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

// Reset the entire UI to a blank slate: every field/check/result/log back to
// its pristine default, interactive state cleared, the resume ledger
// cleared, and the DataHarmonizer grid reloaded back to its empty template.
// Always run this before applying a session's saved state (if any) so
// switching to — or creating — a session never leaves the previous
// session's data on screen.
function resetToBlank() {
  const d = INITIAL_DEFAULTS;
  Object.entries(d.fields).forEach(([id, v]) => { if ($(id) != null) $(id).value = v; });
  Object.entries(d.checks).forEach(([id, v]) => { if ($(id) != null) $(id).checked = v; });
  Object.entries(d.resultsHtml).forEach(([id, html]) => { if ($(id) != null) $(id).innerHTML = html; });
  Object.entries(d.logs).forEach(([id, txt]) => { if ($(id) != null) $(id).textContent = txt; });

  RUN_ROWS = [];
  READ_SAMPLES = [];
  SELECTED_SAMPLE = "";
  READS_RUNS = {};
  window.__prepared = undefined;
  renderRunTable();
  renderReadSampleList();
  $("sampleSubmitBtn").disabled = true;

  setDhSavedIndicator(null);
  reloadDhFrame(); // back to DataHarmonizer's empty default template
  setExpDhSavedIndicator(null);
  reloadExpDhFrame();
}

function reloadDhFrame() {
  const frame = $("dhFrame");
  if (!frame) return;
  if (dhAutosaveTimer) { clearInterval(dhAutosaveTimer); dhAutosaveTimer = null; }
  try { frame.contentWindow.location.reload(); } catch { frame.src = frame.src; }
}

async function applyState(data) {
  suppressSave = true;
  try {
    resetToBlank();

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
    // DataHarmonizer grid: load saved export into the textarea + the grid
    // (reloadDhFrame() above already reset it to blank; this repopulates it
    // once the reloaded iframe is ready again).
    if (data.dh_export) {
      $("dhExport").value = JSON.stringify(data.dh_export);
      setDhSavedIndicator(data.dh_saved_at);
      loadDhGridWhenReady(data.dh_export);
    }
    if (data.exp_dh_export) {
      setExpDhSavedIndicator(data.exp_dh_saved_at);
      loadExpDhGridWhenReady(data.exp_dh_export);
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

// Persist field edits (text inputs, selects, checkboxes) as the user types.
// Credentials inputs are excluded — they are never part of session state.
document.querySelector("main").addEventListener("input", (e) => {
  if (["username", "password", "readsLocalDir", "newUserName", "newUserPassword"].includes(e.target.id)) return;
  scheduleSave();
});
document.querySelector("main").addEventListener("change", (e) => {
  if (["username", "password", "newUserName", "newUserPassword"].includes(e.target.id)) return;
  scheduleSave();
});
