"use strict";

// ---------------------------------------------------------------------------
// Schema library: select a schema for the sample/experiment grids (Samples /
// Reads tabs), build new schemas by importing ENA XML/XSD/YAML sources, and
// edit/preview schemas via the embedded dataharmonizer-template-builder
// (dhtb) sidecar.
// ---------------------------------------------------------------------------
let SCHEMA_LIST = [];

async function refreshSchemaList() {
  try {
    SCHEMA_LIST = await api("/api/schemas");
  } catch {
    SCHEMA_LIST = [];
  }
  renderSchemaLibrary();
  populateSchemaSelect("sampleSchemaSelect");
  populateSchemaSelect("expSchemaSelect");
  populateSchemaMultiSelect("schemaImportExisting");
}

function schemaOptionLabel(s) {
  return s.title && s.title !== s.id ? `${s.title} (${s.id})` : s.id;
}

function populateSchemaSelect(selectId) {
  const el = $(selectId);
  if (!el) return;
  const prev = el.value;
  el.innerHTML = SCHEMA_LIST.map((s) => `<option value="${s.id}">${schemaOptionLabel(s)}</option>`).join("");
  if (SCHEMA_LIST.some((s) => s.id === prev)) el.value = prev;
}

function populateSchemaMultiSelect(selectId) {
  const el = $(selectId);
  if (!el) return;
  el.innerHTML = SCHEMA_LIST.map((s) => `<option value="${s.id}">${schemaOptionLabel(s)}</option>`).join("");
}

function renderSchemaLibrary() {
  const el = $("schemaLibraryList");
  if (!SCHEMA_LIST.length) { el.innerHTML = '<p class="muted" style="padding:10px">No schemas saved yet.</p>'; return; }
  let h = "<table><thead><tr><th>Schema</th><th>Description</th><th>Actions</th></tr></thead><tbody>";
  SCHEMA_LIST.forEach((s) => {
    h += `<tr>
      <td>${schemaOptionLabel(s)}</td>
      <td class="wrap">${s.description || ""}</td>
      <td>
        <button class="btn secondary" style="padding:3px 8px" onclick="editSchemaInLibrary('${s.id}')">Edit</button>
        <button class="btn secondary" style="padding:3px 8px" onclick="selectSchemaById('sample','${s.id}')">Use for sample</button>
        <button class="btn secondary" style="padding:3px 8px" onclick="selectSchemaById('experiment','${s.id}')">Use for experiment</button>
        <a class="btn secondary" style="padding:3px 8px; display:inline-block" href="/api/schemas/${s.id}/export">Export</a>
        <button class="btn danger" style="padding:3px 8px" onclick="deleteSchemaFromLibrary('${s.id}')">Delete</button>
      </td>
    </tr>`;
  });
  el.innerHTML = h + "</tbody></table>";
}

async function applySchemaSelection(role) {
  const selectId = role === "sample" ? "sampleSchemaSelect" : "expSchemaSelect";
  const bannerId = role === "sample" ? "prepBanner" : "readsBanner";
  const schemaId = $(selectId).value;
  if (!schemaId) { banner(bannerId, false, "No schema selected."); return; }
  await selectSchemaById(role, schemaId, bannerId);
}

async function selectSchemaById(role, schemaId, bannerId) {
  const fallbackBanner = bannerId || (role === "sample" ? "prepBanner" : "readsBanner");
  try {
    const result = await api("/api/schemas/select", { method: "POST", body: JSON.stringify({ role, schema_id: schemaId }) });
    console.info("DataHarmonizer schema selection", result);
    const loadToken = pointDhFrameAtTemplate(role, result.template);
    if (role === "experiment") {
      EXP_TEMPLATE_PATH = result.template;
      checkExpSchemaColumns();
    }
    await waitForDhFrameReady(role, result.template, loadToken);
    const diagnostics = Array.isArray(result.diagnostics) && result.diagnostics.length
      ? ` ${result.diagnostics.map((d) => d.message || String(d)).join(" ")}`
      : "";
    banner(fallbackBanner, true, `Switched the ${role} grid to "${schemaId}".${diagnostics}`);
  } catch (e) {
    console.error(`Failed to switch ${role} DataHarmonizer schema`, e);
    banner(fallbackBanner, false, e.message);
  }
}

async function deleteSchemaFromLibrary(schemaId) {
  if (!confirm(`Delete schema "${schemaId}" from the library?`)) return;
  try {
    await api(`/api/schemas/${schemaId}`, { method: "DELETE" });
    banner("schemaLibraryBanner", true, `Deleted "${schemaId}".`);
    refreshSchemaList();
  } catch (e) { banner("schemaLibraryBanner", false, e.message); }
}

async function editSchemaInLibrary(schemaId) {
  try {
    const r = await api(`/api/schemas/${schemaId}`);
    $("schemaSaveName").value = schemaId;
    loadSchemaIntoEditor(r.yaml, schemaId);
    banner("schemaLibraryBanner", true, `Loaded "${schemaId}" into the editor below.`);
  } catch (e) { banner("schemaLibraryBanner", false, e.message); }
}

async function refreshEnaSources() {
  try {
    const sources = await api("/api/schemas/ena-sources");
    $("schemaImportChecklists").innerHTML = sources.checklists
      .map((s) => `<option value="${s.id}">${s.filename}</option>`).join("");
    $("schemaImportXsd").innerHTML = sources.xsd
      .map((s) => `<option value="${s.id}">${s.filename}</option>`).join("");
  } catch (e) { banner("schemaImportBanner", false, e.message); }
}

function selectedOptions(selectId) {
  return Array.from($(selectId).selectedOptions).map((o) => o.value);
}

function clearSchemaMultiSelect(selectId) {
  const el = $(selectId);
  if (!el) return;
  el.selectedIndex = -1;
  Array.from(el.options).forEach((o) => { o.selected = false; });
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function buildImportedSchema() {
  const sourceIds = selectedOptions("schemaImportChecklists").concat(selectedOptions("schemaImportXsd"));
  const schemaIds = selectedOptions("schemaImportExisting");
  if (!sourceIds.length && !schemaIds.length) {
    banner("schemaImportBanner", false, "Select at least one checklist, XSD, or existing schema to import.");
    return;
  }
  const form = new FormData();
  sourceIds.forEach((id) => form.append("source_ids", id));
  schemaIds.forEach((id) => form.append("schema_ids", id));
  const name = $("schemaImportName").value.trim();
  if (name) form.append("name", name);
  try {
    const res = await fetch("/api/schemas/import", { method: "POST", body: form, headers: csrfHeaders(), credentials: "same-origin" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    $("schemaSaveName").value = name || "";
    loadSchemaIntoEditor(body.yaml, name);
    banner("schemaImportBanner", true, "Built merged schema — loaded into the editor below.");
  } catch (e) { banner("schemaImportBanner", false, e.message); }
}

function importSchemaFile() {
  const f = $("schemaFilePicker").files[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  fetch("/api/schemas/import-file", { method: "POST", body: form, headers: csrfHeaders(), credentials: "same-origin" })
    .then(async (res) => {
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      $("schemaSaveName").value = f.name.replace(/\.(ya?ml|xml|xsd)$/i, "");
      loadSchemaIntoEditor(body.yaml, $("schemaSaveName").value);
      banner("schemaLibraryBanner", true, `Loaded "${f.name}" into the editor below.`);
    })
    .catch((e) => banner("schemaLibraryBanner", false, e.message))
    .finally(() => { $("schemaFilePicker").value = ""; });
}

// ---------------------------------------------------------------------------
// dataharmonizer-template-builder (dhtb) embed: iframe + postMessage bridge.
// See ../dataharmonizer-template-builder/docs/integration-contract.md.
// ---------------------------------------------------------------------------
let DHTB_READY = false;
let DHTB_PENDING_YAML = null; // {yaml, name} queued until dhtb.ready fires
let DHTB_EXPORT_INTENT = null; // "save" | "export" — which action requested the pending dhtb.exportYaml

function initSchemaEditorFrame() {
  const url = HEALTH.dhtb_url;
  if (!url) { $("schemaEditorMissing").style.display = "block"; return; }
  $("schemaEditorFrame").src = url;
}

function loadSchemaIntoEditor(yamlText, name) {
  if (DHTB_READY) {
    postToDhtb("dhtb.loadYaml", { yaml: yamlText, name: name || "" });
  } else {
    DHTB_PENDING_YAML = { yaml: yamlText, name: name || "" };
  }
}

function postToDhtb(type, payload) {
  const frame = $("schemaEditorFrame");
  if (!frame || !frame.contentWindow) return;
  frame.contentWindow.postMessage({ type, ...payload }, "*");
}

window.addEventListener("message", (ev) => {
  if (HEALTH.dhtb_url && ev.origin !== new URL(HEALTH.dhtb_url).origin) return;
  const msg = ev.data;
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "dhtb.ready") {
    DHTB_READY = true;
    $("schemaEditorMissing").style.display = "none";
    postToDhtb("dhtb.setTheme", { theme: currentEffectiveTheme() });
    if (DHTB_PENDING_YAML) { postToDhtb("dhtb.loadYaml", DHTB_PENDING_YAML); DHTB_PENDING_YAML = null; }
  } else if (msg.type === "dhtb.exported") {
    window.__dhtbExportedYaml = msg.yaml;
    const intent = DHTB_EXPORT_INTENT;
    DHTB_EXPORT_INTENT = null;
    if (intent === "export") downloadYamlFile(msg.yaml);
    else saveExportedSchema(msg.yaml);
  } else if (msg.type === "dhtb.error") {
    banner("schemaEditorBanner", false, msg.message || "dataharmonizer-template-builder reported an error.");
  }
});

async function saveExportedSchema(yamlText) {
  const name = $("schemaSaveName").value.trim();
  if (!name) { banner("schemaEditorBanner", false, "Enter a name to save as."); return; }
  try {
    const r = await api("/api/schemas", { method: "POST", body: JSON.stringify({ name, yaml: yamlText }) });
    banner("schemaEditorBanner", true, `Saved as "${r.id}".`);
    refreshSchemaList();
  } catch (e) { banner("schemaEditorBanner", false, e.message); }
}

function downloadYamlFile(yamlText) {
  const name = $("schemaSaveName").value.trim() || "schema";
  const blob = new Blob([yamlText], { type: "application/x-yaml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}.yaml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  banner("schemaEditorBanner", true, `Exported "${name}.yaml".`);
}

function saveEditorSchema() {
  if (!DHTB_READY) { banner("schemaEditorBanner", false, "Editor isn't ready yet."); return; }
  DHTB_EXPORT_INTENT = "save";
  postToDhtb("dhtb.exportYaml", {});
}

function exportEditorSchema() {
  if (!DHTB_READY) { banner("schemaEditorBanner", false, "Editor isn't ready yet."); return; }
  DHTB_EXPORT_INTENT = "export";
  postToDhtb("dhtb.exportYaml", {});
}
