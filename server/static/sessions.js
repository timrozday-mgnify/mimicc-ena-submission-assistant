"use strict";

// ---------------------------------------------------------------------------
// Session store — IndexedDB (single-user, local-only; no backend). One record
// per named session holds the full UI snapshot, both DataHarmonizer exports,
// and the reads resume ledger. Mirrors the shape the old /api/sessions
// endpoints returned so the rest of this file is unchanged.
// ---------------------------------------------------------------------------
const DB_NAME = "mimicc";
const DB_STORE = "sessions";
let _dbPromise = null;

function idbOpen() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DB_STORE)) db.createObjectStore(DB_STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}
function idbReq(request) {
  return new Promise((res, rej) => { request.onsuccess = () => res(request.result); request.onerror = () => rej(request.error); });
}
async function idbGet(id) {
  const db = await idbOpen();
  return idbReq(db.transaction(DB_STORE, "readonly").objectStore(DB_STORE).get(id));
}
async function idbAll() {
  const db = await idbOpen();
  return idbReq(db.transaction(DB_STORE, "readonly").objectStore(DB_STORE).getAll());
}
async function idbPut(record) {
  const db = await idbOpen();
  const tx = db.transaction(DB_STORE, "readwrite");
  tx.objectStore(DB_STORE).put(record);
  return new Promise((res, rej) => { tx.oncomplete = () => res(record); tx.onerror = () => rej(tx.error); });
}
async function idbDelete(id) {
  const db = await idbOpen();
  const tx = db.transaction(DB_STORE, "readwrite");
  tx.objectStore(DB_STORE).delete(id);
  return new Promise((res, rej) => { tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); });
}

function newSessionId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
function sessionMeta(r) { return { id: r.id, name: r.name, test_env: !!r.test_env, created_at: r.created_at, updated_at: r.updated_at }; }

async function dbListSessions() {
  const all = await idbAll();
  return all.map(sessionMeta).sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
}
async function dbCreateSession(name, testEnv) {
  name = (name || "").trim();
  if (!name) throw new Error("Session name is required");
  const all = await idbAll();
  if (all.some((r) => r.name === name)) throw new Error(`A session named "${name}" already exists`);
  const now = new Date().toISOString();
  const rec = {
    id: newSessionId(), name, test_env: !!testEnv, created_at: now, updated_at: now,
    state: null, state_saved_at: null,
    dh_export_sample: null, dh_export_sample_saved_at: null,
    dh_export_experiment: null, dh_export_experiment_saved_at: null,
    reads_runs: {},
  };
  await idbPut(rec);
  return sessionMeta(rec);
}
async function dbGetSession(id) {
  const r = await idbGet(id);
  if (!r) throw new Error("Session not found");
  return {
    session: sessionMeta(r),
    state: r.state,
    dh_export: r.dh_export_sample, dh_saved_at: r.dh_export_sample_saved_at,
    exp_dh_export: r.dh_export_experiment, exp_dh_saved_at: r.dh_export_experiment_saved_at,
    reads_runs: r.reads_runs || {},
  };
}
async function dbSaveState(id, state, testEnv, readsRuns) {
  const r = await idbGet(id);
  if (!r) throw new Error("Session not found");
  const now = new Date().toISOString();
  Object.assign(r, { state, state_saved_at: now, updated_at: now, test_env: !!testEnv, reads_runs: readsRuns || {} });
  await idbPut(r);
  return now;
}
async function dbSaveDhExport(id, kind, exportJson) {
  const r = await idbGet(id);
  if (!r) throw new Error("Session not found");
  const now = new Date().toISOString();
  const field = kind === "experiment" ? "dh_export_experiment" : "dh_export_sample";
  r[field] = exportJson; r[field + "_saved_at"] = now; r.updated_at = now;
  await idbPut(r);
  return now;
}

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
    const sessions = await dbListSessions();
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
      del.onclick = async () => { if (confirm(`Delete session "${s.name}"? This removes its saved data.`)) { await idbDelete(s.id); loadSessionList(); } };
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
    const s = await dbCreateSession(name, TEST);
    $("newSessionName").value = "";
    await openSession(s.id);
  } catch (e) { banner("sessionBanner", false, e.message); }
}

async function openSession(id) {
  try {
    const data = await dbGetSession(id);
    SESSION = data.session;
    setSessionChip();
    closeSessionModal();
    await applyState(data);                 // resetToBlank() clears READS_RUNS…
    READS_RUNS = data.reads_runs || {};     // …so restore the resume ledger after.
    renderRunTable();
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
    const savedAt = await dbSaveState(SESSION.id, collectState(), TEST, READS_RUNS);
    setSessionSaved(savedAt);
  } catch { /* transient; next change retries */ }
}

// ---------------------------------------------------------------------------
// Backup: download / import a whole session as JSON. The browser profile is
// now the only copy of session data, so this is the durability the DB used to
// provide — and makes sessions portable between machines.
// ---------------------------------------------------------------------------
async function downloadSession() {
  if (!SESSION) { banner("sessionBanner", false, "Open a session first."); return; }
  await saveSessionNow();
  const rec = await idbGet(SESSION.id);
  const blob = new Blob([JSON.stringify(rec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${String(rec.name).replace(/[^A-Za-z0-9._-]+/g, "-")}.session.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
function importSession() {
  const f = $("sessionImportFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const rec = JSON.parse(reader.result);
      if (!rec || !rec.name) throw new Error("Not a valid session file.");
      const all = await idbAll();
      // Fresh id, and de-duplicate the name so an import never clobbers an
      // existing session.
      rec.id = newSessionId();
      let name = rec.name, n = 2;
      while (all.some((r) => r.name === name)) { name = `${rec.name} (${n++})`; }
      rec.name = name;
      rec.updated_at = new Date().toISOString();
      rec.reads_runs = rec.reads_runs || {};
      await idbPut(rec);
      $("sessionImportFile").value = "";
      banner("sessionBanner", true, `Imported session "${rec.name}".`);
      loadSessionList();
    } catch (e) { banner("sessionBanner", false, e.message); }
  };
  reader.readAsText(f);
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
