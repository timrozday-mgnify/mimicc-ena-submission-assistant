"use strict";

// ---------------------------------------------------------------------------
// Records browser: load an entity into the <ena-browser> grid, mirror the
// write toggle into it, stage edits, submit them as a MODIFY, and run
// lifecycle actions. The grid is a view — every ENA request is made
// server-side by ena-submission-toolkit; nothing here builds XML.
// ---------------------------------------------------------------------------

const ROW_ACTIONS = [
  { action: "release", label: "Release", title: "Make this record public now" },
  { action: "hold", label: "Hold", title: "Set the release date" },
  { action: "suppress", label: "Suppress", title: "Hide a public record" },
  { action: "cancel", label: "Cancel", title: "Cancel a private record" },
];

const envLabel = () => (TEST ? "TEST" : "PRODUCTION");
const recEntity = () => $("recEntity").value;
const recGrid = () => $("recGrid");

/** Which fields this app knows how to MODIFY, per entity. The server is the
 *  source of truth — it is what builds the XML (see /api/health). */
function editableFor(entity) {
  return (HEALTH.editable_columns || {})[entity] || [];
}

function canEdit() {
  return $("recWrite").checked && editableFor(recEntity()).length > 0;
}

// --- Checklist attributes ---------------------------------------------------
// A record's own XML carries the checklist fields — a sample's collection
// date, host, isolation source — and neither listing API has them, so
// ena-submission-toolkit merges them into the rows under an `attr:` namespace.
// That namespace is what makes them usable here: it says which columns are
// attributes, which is both how they get shown (a data-derived column arrives
// hidden, and these are the point of the fetch) and how they get edited (a
// MODIFY addresses one by its tag).
const ATTRIBUTE_PREFIX = "attr:";

let ATTRIBUTE_COLUMNS = [];

function attributeColumnsIn(rows) {
  const names = new Set();
  for (const row of rows) {
    for (const name of Object.keys(row)) {
      if (name.startsWith(ATTRIBUTE_PREFIX)) names.add(name);
    }
  }
  return [...names].sort();
}

/** Column specs that show the attributes and title them by their tag alone. */
function attributeColumnSpecs() {
  return ATTRIBUTE_COLUMNS.map((name) => ({
    name,
    title: name.slice(ATTRIBUTE_PREFIX.length),
    hidden: false,
  }));
}

/** The fixed editable fields, plus this listing's checklist attributes. The
 *  server is the authority on both and refuses anything else: a tag the record
 *  does not already carry, and ENA's own `ENA-*` tags, come back as a failed
 *  manifest rather than a submission. */
function editableColumnsNow() {
  const fixed = editableFor(recEntity());
  return canEdit() ? [...fixed, ...ATTRIBUTE_COLUMNS] : fixed;
}

function applyMode() {
  const write = $("recWrite").checked;
  if (!canEdit()) clearManifests();
  recGrid().applyConfig({
    mode: canEdit() ? "edit" : "read",
    columns: attributeColumnSpecs(),
    editableColumns: editableColumnsNow(),
    rowActions: write ? ROW_ACTIONS : [],
  });
  refreshSubmitButton();
  if (write && !canEdit()) {
    banner("recBanner", false, `Write mode — ${envLabel()}. ${recEntity()} cannot be edited here; row actions still apply.`);
  }
}

// --- The manifest gate ------------------------------------------------------
// Submitting a MODIFY replaces the whole record in ENA, so it is not a button
// press: the exact documents have to be built and shown first. MANIFESTS holds
// the last preview, MANIFEST_KEY the change set it was built from — edit
// anything and the key no longer matches, which re-locks the submit button.
let MANIFESTS = null;
let MANIFEST_KEY = "";

const changeKey = (entries) => JSON.stringify(entries);

function manifestsReady(entries) {
  return (
    entries.length > 0 &&
    MANIFESTS !== null &&
    MANIFEST_KEY === changeKey(entries) &&
    MANIFESTS.length === entries.length &&
    MANIFESTS.every((manifest) => manifest.success)
  );
}

function refreshSubmitButton() {
  const entries = canEdit() ? pendingChanges() : [];
  $("recGenerate").disabled = entries.length === 0;
  $("recSubmit").disabled = !manifestsReady(entries);
  renderManifestState(entries);
}

function renderManifestState(entries) {
  const state = $("recManifestState");
  const stale = MANIFESTS !== null && MANIFEST_KEY !== changeKey(entries);
  const failed = (MANIFESTS || []).filter((manifest) => !manifest.success).length;
  let className = "muted";
  let text;
  if (!entries.length) {
    text = MANIFESTS ? "no staged changes left" : "no staged changes";
  } else if (MANIFESTS === null || stale) {
    className = "warn";
    text = `${entries.length} record(s) staged — no manifests for these edits yet`;
  } else if (failed) {
    className = "bad";
    text = `${failed} of ${MANIFESTS.length} manifest(s) could not be built — nothing will be submitted`;
  } else {
    className = "ok";
    text = `${MANIFESTS.length} manifest(s) built and ready to review`;
  }
  state.className = "state " + className;
  state.textContent = text;
}

function clearManifests() {
  MANIFESTS = null;
  MANIFEST_KEY = "";
  $("recManifests").innerHTML = "";
  $("recManifestEmpty").hidden = false;
}

function fieldList(entry) {
  return Object.entries(entry.changes || {})
    .map(
      ([field, value]) =>
        `<li><code>${esc(field)}</code>: <span class="muted">${esc(entry.before?.[field] ?? "")}</span> → ${esc(value)}</li>`,
    )
    .join("");
}

function renderManifests(entries) {
  const byAccession = new Map(entries.map((entry) => [entry.accession, entry]));
  $("recManifestEmpty").hidden = MANIFESTS.length > 0;
  $("recManifests").innerHTML = MANIFESTS.map((manifest) => {
    const entry = byAccession.get(manifest.accession) || {};
    const changes = { ...entry, changes: manifest.changes || entry.changes };
    const body = manifest.success
      ? `<pre>${esc(manifest.xml)}</pre>`
      : `<p class="msg bad">${esc((manifest.messages || []).join("; ") || "could not be built")}</p>` +
        `<p class="muted">This record will not be submitted, and neither will any other ` +
        `until every manifest builds.</p>`;
    return (
      `<details class="entry ${manifest.success ? "ok" : "bad"}"${MANIFESTS.length === 1 ? " open" : ""}>` +
      `<summary>${esc(manifest.accession)} — ${manifest.success ? "manifest built" : "could not be built"}</summary>` +
      `<div class="body"><ul class="fields">${fieldList(changes)}</ul>${body}</div></details>`
    );
  }).join("");
}

$("recGenerate").onclick = async () => {
  const entries = pendingChanges();
  if (!entries.length) { refreshSubmitButton(); return; }
  $("recGenerate").disabled = true;
  try {
    const body = await api("/api/records/modify/preview", {
      method: "POST",
      body: JSON.stringify({
        entity: recEntity(),
        test: TEST,
        records: entries.map(({ accession, changes }) => ({ accession, changes })),
      }),
    });
    MANIFESTS = body.results || [];
    MANIFEST_KEY = changeKey(entries);
    renderManifests(entries);
  } catch (e) {
    clearManifests();
    banner("recBanner", false, `Could not build the manifests; nothing was submitted: ${e.message}`);
  }
  refreshSubmitButton();
};

// --- The submission log -----------------------------------------------------
// Verbose on purpose: what was sent, what ENA said, and whether the document
// that went out is the one that was reviewed.
function logEntry({ title, ok, lines = [], html = "" }) {
  $("recSubmitLogEmpty").hidden = true;
  const stamp = new Date().toLocaleTimeString();
  const element = document.createElement("details");
  element.className = "entry " + (ok ? "ok" : "bad");
  element.open = true;
  element.innerHTML =
    `<summary>${stamp} · ${esc(title)}</summary><div class="body">` +
    lines.map(([kind, text]) => `<p class="msg ${kind}">${esc(text)}</p>`).join("") +
    html +
    "</div>";
  $("recSubmitLog").prepend(element);
  while ($("recSubmitLog").children.length > 20) $("recSubmitLog").lastElementChild.remove();
}

function logSubmission(entries, results) {
  const reviewed = new Map((MANIFESTS || []).map((manifest) => [manifest.accession, manifest.xml]));
  for (const result of results) {
    const entry = entries.find((candidate) => candidate.accession === result.accession) || {};
    const lines = [];
    for (const message of result.info || []) lines.push(["", message]);
    for (const message of result.warnings || []) lines.push(["warn", message]);
    for (const message of result.errors || []) lines.push(["bad", message]);
    if (!lines.length) for (const message of result.messages || []) lines.push([result.success ? "" : "bad", message]);
    if (!lines.length) {
      lines.push(["", result.success ? "ENA returned no messages." : "ENA rejected it without saying why."]);
    }

    const sent = result.xml || "";
    const same = sent && reviewed.get(result.accession) === sent;
    lines.unshift([
      same ? "" : "warn",
      same
        ? "Document sent is byte-for-byte the manifest reviewed above."
        : "The document sent differs from the manifest that was reviewed.",
    ]);
    logEntry({
      title: `${result.accession} · MODIFY ${envLabel()} · ${result.success ? "accepted" : "REJECTED"}`,
      ok: result.success,
      lines,
      html:
        `<ul class="fields">${fieldList({ ...entry, changes: result.changes || entry.changes })}</ul>` +
        (sent ? `<pre>${esc(sent)}</pre>` : ""),
    });
  }
}

$("recSubmitLogClear").onclick = () => {
  $("recSubmitLog").innerHTML = "";
  $("recSubmitLogEmpty").hidden = false;
};

// --- Debug log --------------------------------------------------------------
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

// --- Loading ----------------------------------------------------------------
/** Report rows + the editable fields only the record XML carries.
 *
 *  A run's title and an experiment's library/instrument are not in the Reports
 *  API's answer, so without this there would be no cell to edit them in. Only
 *  worth the requests in write mode; a failure here degrades to the report's
 *  own columns rather than losing the grid. */
async function withEditableFields(rows) {
  const accessions = rows.map((row) => row.accession).filter(Boolean);
  if (!accessions.length) return rows;
  try {
    const body = await api(`/api/records/${recEntity()}/fields`, {
      method: "POST",
      body: JSON.stringify({ accessions, test: TEST }),
    });
    const fields = body.fields || {};
    return rows.map((row) => ({ ...row, ...(fields[row.accession] || {}) }));
  } catch (e) {
    banner("recBanner", false, `Loaded ${recEntity()}, but not the fields held only in the record XML — those columns will be missing: ${e.message}`);
    return rows;
  }
}

/** The query string of a fetch. These are criteria on the *request*, applied
 *  server-side by ena-submission-toolkit — not the grid's own client-side
 *  column filters. */
function recCriteriaQuery() {
  const params = new URLSearchParams({ test: String(TEST) });
  const search = $("recSearch").value.trim();
  const linked = $("recLinked").value.trim();
  if (search) params.set("search", search);
  if (linked) params.set("linked_to", linked);
  if ($("recUnlinked").checked) params.set("unlinked", "true");
  if ($("recFullFields").checked) params.set("full_fields", "true");
  if ($("recStatus").value !== "all") params.set("status", $("recStatus").value);
  return params.toString();
}

$("recClear").onclick = () => {
  $("recSearch").value = "";
  $("recLinked").value = "";
  $("recUnlinked").checked = false;
  $("recFullFields").checked = false;
  $("recStatus").value = "all";
  loadRecords();
};

for (const id of ["recSearch", "recLinked"]) {
  $(id).onkeydown = (e) => { if (e.key === "Enter") loadRecords(); };
}

$("recWrite").onchange = (e) => {
  if (e.target.checked && !confirm(`Enable write mode against ${envLabel()}? Edits you submit change records in ENA.`)) {
    e.target.checked = false;
    return;
  }
  // Entering write mode needs a reload, not just a mode switch: the fields
  // that only exist in the record XML are fetched with the rows.
  if (e.target.checked && recGrid().getRows().length) loadRecords();
  else applyMode();
};

async function loadRecords() {
  const entity = recEntity();
  const query = recCriteriaQuery();
  appendLog("recLog", `Fetching ${entity} (${query})…`);
  $("recCount").textContent = "loading…";
  recGrid().applyConfig({ entity, mode: "read", rowActions: $("recWrite").checked ? ROW_ACTIONS : [] });
  try {
    let rows = await api(`/api/records/${entity}?${query}`);
    appendLog("recLog", `Got ${rows.length} ${entity} row(s).`);
    if (rows.length) {
      // Log every field actually present (not just the columns the grid
      // shows) — this surfaces raw Reports API keys the alias mapping didn't
      // recognise, which is what's needed to debug a blank linking accession.
      const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
      appendLog("recLog", `Fields present: ${keys.join(", ")}`);
      appendLog("recLog", `First row: ${JSON.stringify(rows[0])}`);
    }
    ATTRIBUTE_COLUMNS = attributeColumnsIn(rows);
    if (canEdit()) rows = await withEditableFields(rows);
    // Before the rows, not after: a column the grid first meets in the data is
    // hidden by default, and that decision sticks in its layout.
    recGrid().applyConfig({ columns: attributeColumnSpecs() });
    applySavedGridLayout("records", entity);
    recGrid().setRows(rows);
    clearManifests();
    applyMode();
    $("recCount").textContent = `${rows.length} ${entity} from ${envLabel()}`;
    banner("recBanner", true, `${rows.length} ${entity}.`);
    scheduleSave();
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    $("recCount").textContent = "load failed";
    recGrid().setRows([]);
    banner("recBanner", false, e.message);
  }
}

// --- Submitting the change set ---------------------------------------------
/** Change set rows -> what /api/records/modify wants, narrowed to editable
 *  fields. The server refuses anything else, but sending it would be a bug. */
function pendingChanges() {
  const allowed = new Set(editableColumnsNow());
  return recGrid()
    .getChangeSet()
    .rows.map((row) => {
      const changes = {};
      for (const field of row.changed) {
        if (allowed.has(field)) changes[field] = row.after[field];
      }
      return { accession: row.accession || row.key, changes, before: row.before };
    })
    .filter((entry) => Object.keys(entry.changes).length > 0);
}

function showDiff(entries) {
  $("recDiffEnv").textContent =
    `${entries.length} record(s) will be modified in ${envLabel()}.` +
    (TEST ? "" : " This is the production service.");
  const rows = entries.flatMap((entry) =>
    Object.entries(entry.changes).map(
      ([field, value]) =>
        `<tr><td>${esc(entry.accession)}</td><td>${esc(field)}</td>` +
        `<td class="muted">${esc(entry.before?.[field] ?? "")}</td><td>${esc(value)}</td></tr>`,
    ),
  );
  $("recDiffTable").innerHTML =
    "<thead><tr><th>Accession</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>" +
    rows.join("") +
    "</tbody>";

  const dialog = $("recDiffDialog");
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.showModal();
  });
}

$("recSubmit").onclick = async () => {
  const entries = pendingChanges();
  if (!manifestsReady(entries)) { refreshSubmitButton(); return; }
  if (!(await showDiff(entries))) return;

  $("recSubmit").disabled = true;
  const payload = entries.map(({ accession, changes }) => ({ accession, changes }));
  try {
    const body = await api("/api/records/modify", {
      method: "POST",
      body: JSON.stringify({ entity: recEntity(), test: TEST, records: payload }),
    });
    const results = body.results || [];
    logSubmission(entries, results);
    const failed = results.filter((result) => !result.success);
    if (body.success) {
      // ENA now holds the new values; drop the local ones and re-fetch rather
      // than trusting the optimistic copy.
      recGrid().clearChanges();
      banner("recBanner", true, `Submitted ${results.length} change(s) to ${envLabel()}. See the submission log below.`);
      await loadRecords();
    } else {
      // Deliberately keep the change set so the user can fix and retry.
      banner("recBanner", false, `ENA rejected ${failed.length} of ${results.length} record(s); your edits are kept. The submission log below has what it said.`);
    }
  } catch (e) {
    logEntry({
      title: `MODIFY ${envLabel()} · failed before ENA answered`,
      ok: false,
      lines: [["bad", e.message], ["", `${payload.length} record(s) were in the batch; your edits are kept.`]],
    });
    banner("recBanner", false, `Submission failed; your edits are kept: ${e.message}`);
  }
  refreshSubmitButton();
};

// --- Lifecycle actions ------------------------------------------------------
async function recAction(action, accession) {
  let hold = null;
  if (action === "hold") { hold = prompt(`Hold ${accession} until (YYYY-MM-DD):`); if (!hold) return; }
  if (action !== "release" && !confirm(`${action.toUpperCase()} ${accession} in ${envLabel()}?`)) return;
  appendLog("recLog", `${action} ${accession}…`);
  try {
    const r = await api("/api/records/action", {
      method: "POST",
      body: JSON.stringify({ action, accession, test: TEST, hold_until: hold }),
    });
    const detail = r.messages || "";
    appendLog("recLog", `${action} ${accession}: ${r.success ? "ok" : "failed"} — ${detail}`);
    logEntry({
      title: `${accession} · ${action.toUpperCase()} ${envLabel()} · ${r.success ? "accepted" : "REJECTED"}`,
      ok: r.success,
      lines: [[r.success ? "" : "bad", detail || (r.success ? "ENA returned no messages." : "ENA rejected it")]],
    });
    banner("recBanner", r.success, `${action} ${accession}: ${r.success ? "ok" : "failed"} — ${detail}`);
    // The status column is how the user sees it worked.
    if (r.success) await loadRecords();
    else scheduleSave();
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    banner("recBanner", false, e.message);
  }
}

// --- Element events ---------------------------------------------------------
recGrid().addEventListener("ena-browser:row-action", (e) =>
  recAction(e.detail.action, e.detail.row?.accession || e.detail.key),
);
recGrid().addEventListener("ena-browser:change", () => refreshSubmitButton());
recGrid().addEventListener("ena-browser:error", (e) => banner("recBanner", false, e.detail.message));
for (const name of ["filter-change", "layout-change"]) {
  recGrid().addEventListener(`ena-browser:${name}`, (e) => {
    if (e.detail.source !== "api") scheduleSave();
  });
}
