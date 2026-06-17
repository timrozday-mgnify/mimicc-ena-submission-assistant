"use strict";

// ---------------------------------------------------------------------------
// Global state + helpers
// ---------------------------------------------------------------------------
let TEST = true;            // ENA test vs production
let HEALTH = {};
let RUN_ROWS = [];          // editable run records for the Reads tab
let READ_SAMPLES = [];      // ENA samples available for read assignment
let SELECTED_SAMPLE = "";   // selected sample accession for click assignment

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
    const btn = el.querySelector(".panel-head button");
    if (btn) {
      btn.textContent = "⛶";
      btn.title = "Maximize panel";
      btn.setAttribute("aria-label", "Maximize panel");
    }
  });

  if (!wasMaximized) {
    panel.classList.add("maximized");
    const btn = panel.querySelector(".panel-head button");
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
  $("sampleFilter").value = HEALTH.default_sample_filter || "";
  applyActiveReadsDir(HEALTH);
  if (!HEALTH.dh_available) { $("dhWrap").style.display = "none"; $("dhMissing").style.display = "block"; }
  // populate library presets
  const sel = $("presetSelect");
  Object.entries(HEALTH.library_presets || {}).forEach(([k, v]) => {
    const o = document.createElement("option"); o.value = k; o.textContent = v.label; sel.appendChild(o);
  });
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
  } catch (e) { banner("studyBanner", false, e.message); }
}

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
  } catch (e) { banner("readsBanner", false, e.message); }
}
function renderRunTable() {
  const cols = ["NAME", "files", "SAMPLE", "STUDY", "PLATFORM", "INSTRUMENT", "LIBRARY_SOURCE", "LIBRARY_SELECTION", "LIBRARY_STRATEGY"];
  const head = $("runTable").querySelector("thead");
  const body = $("runTable").querySelector("tbody");
  head.innerHTML = "<tr><th></th>" + cols.map((c) => `<th>${c}</th>`).join("") + "</tr>";
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
        };
        td.appendChild(inp);
      }
      tr.appendChild(td);
    });
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
}
async function submitReads(doSubmit) {
  $("readsLog").textContent = "";
  $("readsResults").innerHTML = "";
  try {
    const runs = RUN_ROWS.map((r) => {
      const o = { NAME: r.NAME, STUDY: r.STUDY, SAMPLE: r.SAMPLE, PLATFORM: r.PLATFORM, INSTRUMENT: r.INSTRUMENT,
        LIBRARY_SOURCE: r.LIBRARY_SOURCE, LIBRARY_SELECTION: r.LIBRARY_SELECTION, LIBRARY_STRATEGY: r.LIBRARY_STRATEGY };
      if (r.paired) { o.FASTQ1 = r.FASTQ1; o.FASTQ2 = r.FASTQ2; } else { o.FASTQ = r.FASTQ; }
      return o;
    });
    const { job_id } = await api("/api/reads/submit", { method: "POST", body: JSON.stringify({ runs, test: TEST, submit: doSubmit }) });
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
    if (m.result) { results.push(m.result); }
    if (m.done) {
      const ok = m.results.every((r) => r.success);
      banner("readsBanner", ok, ok ? `All ${m.results.length} run(s) succeeded.` : "Some runs failed — see results.");
      renderTable("readsResults", m.results);
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
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    banner("recBanner", false, e.message);
  }
}

refreshHealth();
