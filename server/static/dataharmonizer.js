"use strict";

// ---------------------------------------------------------------------------
// DataHarmonizer export integration (sample grid: button + autosave; the
// experiment grid below follows the same pattern, parameterized by frame id).
// ---------------------------------------------------------------------------
const DH_AUTOSAVE_INTERVAL_MS = 30000;
let dhAutosaveTimer = null;
let expDhAutosaveTimer = null;

// Each DataHarmonizer iframe is same-origin, so its window.dataHarmonizer
// hook (added to the DataHarmonizer fork — see web/index.js there) is
// directly reachable. Returns null until that grid has finished loading.
function dataHarmonizerApi(frameId) {
  const frame = $(frameId);
  const win = frame && frame.contentWindow;
  return win && win.dataHarmonizer && win.dataHarmonizer.ready ? win.dataHarmonizer : null;
}
function dhApi() { return dataHarmonizerApi("dhFrame"); }
function expDhApi() { return dataHarmonizerApi("expDhFrame"); }

function formatSavedAt(isoTs) {
  return isoTs ? new Date(isoTs).toLocaleTimeString() : "never";
}

function setDhSavedIndicator(isoTs) {
  $("dhSavedIndicator").textContent = "Last saved: " + formatSavedAt(isoTs);
}
function setExpDhSavedIndicator(isoTs) {
  $("expDhSavedIndicator").textContent = "Last saved: " + formatSavedAt(isoTs);
}

async function saveDhExport(exportJson, { silent = false } = {}) {
  if (!SESSION) { if (!silent) banner("prepBanner", false, "Open a session first."); return; }
  try {
    const r = await api(`/api/sessions/${SESSION.id}/dh-export/sample`, { method: "POST", body: JSON.stringify({ export: exportJson }) });
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
$("dhFrame").addEventListener("load", () => propagateThemeToFrames(currentEffectiveTheme()));
$("dhFrame").addEventListener("load", () => stabilizeDataHarmonizerFrameRows("dhFrame"));
$("dhFrame").addEventListener("load", markDhFrameLoaded);

// ---------------------------------------------------------------------------
// Experiment metadata DataHarmonizer panel (Reads tab)
//
// The schema for this grid doesn't exist yet (the user authors it) — these
// constants are the contract it must follow (LinkML slot `title:` values)
// for the pairing-sync and submit-time merge below to find the right
// columns. Documented in README "Experiment metadata schema".
// ---------------------------------------------------------------------------
const EXP_KEY_TITLE = "Experiment name";       // matches a pairing row's NAME
const EXP_SAMPLE_TITLE = "Sample alias";       // matches a pairing row's SAMPLE
const EXP_FIELD_TITLES = {                     // manifest field -> experiment-DH column title
  PLATFORM: "Platform",
  INSTRUMENT: "Instrument",
  LIBRARY_SOURCE: "Library source",
  LIBRARY_SELECTION: "Library selection",
  LIBRARY_STRATEGY: "Library strategy",
  INSERT_SIZE: "Insert size",
  LIBRARY_NAME: "Library name",
  DESCRIPTION: "Description",
};

let EXP_TEMPLATE_PATH = null; // "mimicc_experiment/<schema name>", or null if not built
const DH_FRAME_READY_TIMEOUT_MS = 20000;

function dhRoleConfig(role) {
  if (role === "sample") {
    return { frameId: "dhFrame", missingId: "dhMissing", bannerId: "prepBanner" };
  }
  return { frameId: "expDhFrame", missingId: "expDhMissing", bannerId: "readsBanner" };
}

function dhFrameUrl(templatePath, token) {
  const params = new URLSearchParams({ template: templatePath, t: token });
  return `/dh/?${params.toString()}`;
}

async function fetchDhRegistry() {
  const res = await fetch(`/dh/dh-template-registry.json?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`DataHarmonizer registry unavailable (HTTP ${res.status}).`);
  return res.json();
}

function pointDhFrameAtTemplate(role, templatePath) {
  const { frameId, missingId } = dhRoleConfig(role);
  const frame = $(frameId);
  if (!frame || !templatePath) return;
  const token = String(Date.now());
  frame.dataset.expectedDhLoad = token;
  delete frame.dataset.loadedDhLoad;
  frame.src = dhFrameUrl(templatePath, token);
  if ($(missingId)) $(missingId).style.display = "none";
  return token;
}

function markDhFrameLoaded(e) {
  const frame = e.target;
  if (frame?.dataset?.expectedDhLoad) {
    frame.dataset.loadedDhLoad = frame.dataset.expectedDhLoad;
  }
}

function waitForDhFrameReady(role, templatePath, token) {
  const { frameId } = dhRoleConfig(role);
  const frame = $(frameId);
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const poll = setInterval(() => {
      try {
        if (token && frame?.dataset?.loadedDhLoad !== token) return;
        const win = frame?.contentWindow;
        const doc = frame?.contentDocument;
        if (win?.dataHarmonizer?.ready && doc?.querySelector(".handsontable")) {
          clearInterval(poll);
          resolve();
          return;
        }
      } catch (e) {
        clearInterval(poll);
        reject(e);
        return;
      }
      if (Date.now() - started > DH_FRAME_READY_TIMEOUT_MS) {
        clearInterval(poll);
        reject(new Error(`DataHarmonizer did not finish loading "${templatePath}". Check the browser console and server logs.`));
      }
    }, 250);
  });
}

// Point both DH iframes at an explicit `?template=<folder>/<schema name>`,
// looked up from the build's own registry rather than hardcoded — and
// required even for the sample grid (the default, query-param-less `/dh/`
// only works when exactly one template is registered; with two or more, the
// Toolbar's own Template <select> can end up loading a different one than
// AppContext's own startup reload did, leaving window.dataHarmonizer
// pointing at no current grid).
async function initDhFrames() {
  let registry = {};
  try {
    registry = await fetchDhRegistry();
  } catch (e) {
    console.error("Failed to load DataHarmonizer template registry", e);
    /* dh-default not built at all — fall through, both show "missing" */
  }

  if (registry.mimicc) {
    pointDhFrameAtTemplate("sample", `mimicc/${registry.mimicc}`);
  }
  if (registry.mimicc_experiment) {
    EXP_TEMPLATE_PATH = `mimicc_experiment/${registry.mimicc_experiment}`;
    pointDhFrameAtTemplate("experiment", EXP_TEMPLATE_PATH);
    checkExpSchemaColumns();
  } else {
    $("expDhMissing").style.display = "block";
  }
  return registry;
}

// Warn (non-blocking) when the experiment schema doesn't use the exact
// column titles the pairing-sync (syncPairingsToExperimentDh) and submit-time
// merge (mergeExperimentMetadata) rely on — see README "Sample and
// experiment metadata schemas" for the column-title contract.
async function checkExpSchemaColumns() {
  const el = $("expSchemaWarning");
  el.className = "banner";
  el.textContent = "";
  try {
    const folder = EXP_TEMPLATE_PATH.split("/")[0];
    const schema = await (await fetch(`/templates/${folder}/schema.json?t=${Date.now()}`, { cache: "no-store" })).json();
    const titles = new Set(
      Object.values(schema.classes || {}).flatMap((c) => Object.values(c.attributes || {}).map((a) => a.title))
    );
    const missing = [EXP_KEY_TITLE, EXP_SAMPLE_TITLE].filter((t) => !titles.has(t));
    if (missing.length) {
      el.className = "banner warn";
      el.textContent = `This experiment schema is missing the column(s) ${missing.join(", ")} — read-pairing sync and submission won't be able to match rows by experiment name/sample.`;
    }
  } catch { /* best-effort only */ }
}

async function saveExpDhExport(exportJson, { silent = false } = {}) {
  if (!SESSION) { if (!silent) banner("readsBanner", false, "Open a session first."); return; }
  try {
    const r = await api(`/api/sessions/${SESSION.id}/dh-export/experiment`, { method: "POST", body: JSON.stringify({ export: exportJson }) });
    setExpDhSavedIndicator(r.saved_at);
    scheduleSave();
    if (!silent) banner("readsBanner", true, "Saved experiment metadata.");
  } catch (e) {
    if (!silent) banner("readsBanner", false, e.message);
  }
}

function exportExpDhNow() {
  const dh = expDhApi();
  if (!dh) { banner("readsBanner", false, "Experiment DataHarmonizer isn't ready yet."); return; }
  saveExpDhExport(dh.getExportJson());
}

function autosaveExpDhExport() {
  const dh = expDhApi();
  if (!dh || !SESSION) return;
  saveExpDhExport(dh.getExportJson(), { silent: true });
}

function loadExpDhGridWhenReady(exportObj) {
  if (!exportObj) return;
  const poll = setInterval(() => {
    const dh = expDhApi();
    if (!dh || !dh.loadExportJson) return;
    clearInterval(poll);
    try { dh.loadExportJson(exportObj); } catch { /* schema mismatch — leave grid empty */ }
  }, 500);
  setTimeout(() => clearInterval(poll), 15000);
}

function startExpDhAutosave() {
  const poll = setInterval(() => {
    if (!expDhApi()) return;
    clearInterval(poll);
    if (expDhAutosaveTimer) clearInterval(expDhAutosaveTimer);
    expDhAutosaveTimer = setInterval(autosaveExpDhExport, DH_AUTOSAVE_INTERVAL_MS);
    syncPairingsToExperimentDh(); // catch up on any pairings made before this grid was ready
  }, 500);
}
$("expDhFrame").addEventListener("load", startExpDhAutosave);
$("expDhFrame").addEventListener("load", () => propagateThemeToFrames(currentEffectiveTheme()));
$("expDhFrame").addEventListener("load", () => stabilizeDataHarmonizerFrameRows("expDhFrame"));
$("expDhFrame").addEventListener("load", markDhFrameLoaded);

function reloadExpDhFrame() {
  const frame = $("expDhFrame");
  if (!frame) return;
  if (expDhAutosaveTimer) { clearInterval(expDhAutosaveTimer); expDhAutosaveTimer = null; }
  if (!frame.src) return; // never pointed at a template (schema not built) — nothing to reload
  try { frame.contentWindow.location.reload(); } catch { frame.src = frame.src; }
}

// Push each pairing row's NAME+SAMPLE into the experiment grid, touching
// only those two columns (upsertRow) so anything already filled in for that
// row — manually, or via the schema's own ifabsent defaults — is preserved.
function syncPairingsToExperimentDh() {
  const dh = expDhApi();
  if (!dh) return;
  RUN_ROWS.forEach((row) => {
    if (!row.NAME) return;
    try { dh.upsertRow(EXP_KEY_TITLE, row.NAME, { [EXP_SAMPLE_TITLE]: row.SAMPLE || "" }); } catch { /* ignore */ }
  });
}
