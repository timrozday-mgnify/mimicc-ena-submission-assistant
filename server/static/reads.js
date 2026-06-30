"use strict";

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
// The local reads directory the helper scans/uploads from (on the user's machine).
function readsLocalDir() { return ($("readsLocalDir").value || "").trim(); }

function blankRun(group) {
  return {
    NAME: group.group, files: group.files, paired: group.paired,
    FASTQ1: group.files_by_mate?.["1"] || "", FASTQ2: group.files_by_mate?.["2"] || "",
    FASTQ: group.paired ? "" : (group.files[0] || ""),
    SAMPLE: group.suggested_sample || "", STUDY: $("defaultStudy").value || "",
    confidence: group.confidence || "none", suggested_alias: group.suggested_alias || "",
  };
}

// ---------------------------------------------------------------------------
// Pairing table TSV export/import
// Columns: NAME, SAMPLE, STUDY, paired, FASTQ1, FASTQ2, FASTQ — a full
// round-trip of a pairing row (not just the assignment decision), so import
// works standalone without a prior Scan too.
// ---------------------------------------------------------------------------
const PAIRING_TSV_COLS = ["NAME", "SAMPLE", "STUDY", "paired", "FASTQ1", "FASTQ2", "FASTQ"];

function exportPairingsTsv() {
  if (!RUN_ROWS.length) { banner("readsBanner", false, "Nothing to export — scan or pair some reads first."); return; }
  const lines = [PAIRING_TSV_COLS.join("\t")];
  RUN_ROWS.forEach((r) => {
    lines.push(PAIRING_TSV_COLS.map((c) => String(r[c] ?? "").replace(/\t|\n/g, " ")).join("\t"));
  });
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/tab-separated-values" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "read-sample-pairings.tsv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  banner("readsBanner", true, `Exported ${RUN_ROWS.length} pairing(s).`);
}

function parsePairingsTsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (!lines.length) return [];
  const header = lines[0].split("\t");
  return lines.slice(1).map((line) => {
    const cells = line.split("\t");
    const row = {};
    header.forEach((col, i) => { row[col] = cells[i] ?? ""; });
    return row;
  });
}

function importPairingsTsv() {
  const f = $("pairingsTsvFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    const imported = parsePairingsTsv(reader.result);
    let updated = 0, added = 0;
    imported.forEach((row) => {
      const paired = String(row.paired).toLowerCase() === "true";
      const existing = RUN_ROWS.find((r) => r.NAME === row.NAME);
      const files = paired
        ? [row.FASTQ1, row.FASTQ2].filter((v) => v)
        : [row.FASTQ].filter((v) => v);
      if (existing) {
        Object.assign(existing, {
          SAMPLE: row.SAMPLE || "", STUDY: row.STUDY || "",
          paired, FASTQ1: row.FASTQ1 || "", FASTQ2: row.FASTQ2 || "", FASTQ: row.FASTQ || "",
          files: files.length ? files : existing.files,
        });
        updated++;
      } else {
        RUN_ROWS.push({
          NAME: row.NAME, files, paired,
          FASTQ1: row.FASTQ1 || "", FASTQ2: row.FASTQ2 || "", FASTQ: row.FASTQ || "",
          SAMPLE: row.SAMPLE || "", STUDY: row.STUDY || "", confidence: "manual", suggested_alias: "",
        });
        added++;
      }
    });
    renderRunTable();
    renderReadSampleList();
    syncPairingsToExperimentDh();
    banner("readsBanner", true, `Imported ${imported.length} pairing(s) — ${updated} updated, ${added} added.`);
    scheduleSave();
    $("pairingsTsvFile").value = "";
  };
  reader.readAsText(f);
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
  const dir = readsLocalDir();
  if (!dir) { banner("readsBanner", false, "Enter the absolute path to your local reads directory."); return; }
  if (!HELPER_OK && !(await detectHelper())) {
    banner("readsBanner", false, "The local upload helper isn't running — start it, then re-check."); return;
  }
  try {
    const r = await helperApi("/api/scan", { method: "POST", body: JSON.stringify({ host_dir: dir }) });
    RUN_ROWS = r.groups.map(blankRun);
    renderRunTable();
    banner("readsBanner", true, `Found ${r.count} read group(s) in ${r.host_dir}.`);
    syncPairingsToExperimentDh();
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
    syncPairingsToExperimentDh();
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
  const cols = ["NAME", "files", "SAMPLE", "STUDY"];
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
      syncPairingsToExperimentDh();
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
          if (c === "SAMPLE" || c === "NAME") syncPairingsToExperimentDh();
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
// Look up each pairing row's experiment metadata (by EXP_KEY_TITLE = NAME) in
// the experiment DataHarmonizer grid and merge the EXP_FIELD_TITLES-mapped
// columns into a run dict. Throws with a clear message if the experiment
// grid isn't ready or a row has no matching experiment entry, rather than
// silently submitting an incomplete manifest.
function mergeExperimentMetadata(runRows) {
  const dh = expDhApi();
  if (!dh) {
    throw new Error("Experiment metadata DataHarmonizer isn't ready yet — open the Samples-like panel above and try again.");
  }
  const exportJson = dh.getExportJson();
  const rows = Object.values(exportJson.Container || {})[0] || [];
  const byName = {};
  rows.forEach((row) => { if (row[EXP_KEY_TITLE]) byName[row[EXP_KEY_TITLE]] = row; });

  return runRows.map((r) => {
    const expRow = byName[r.NAME];
    if (!expRow) {
      throw new Error(`No experiment metadata row found for "${r.NAME}" — check the experiment DataHarmonizer panel above.`);
    }
    const o = { NAME: r.NAME, STUDY: r.STUDY, SAMPLE: r.SAMPLE, reupload: !!r.reupload };
    Object.entries(EXP_FIELD_TITLES).forEach(([field, title]) => {
      if (expRow[title]) o[field] = expRow[title];
    });
    if (r.paired) { o.FASTQ1 = r.FASTQ1; o.FASTQ2 = r.FASTQ2; } else { o.FASTQ = r.FASTQ; }
    return o;
  });
}

function appendReadsLog(text) {
  const log = $("readsLog");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

// Reads upload is browser-bridged:
//   1. ask the server for a PLAN (which runs to upload vs. skip + manifest text),
//   2. for each upload, hand the manifest to the LOCAL HELPER which runs
//      webin-cli against the local files and streams the log,
//   3. relay each outcome back to the server (/api/reads/result) to update the
//      resume ledger. Reads never pass through the server.
async function submitReads(doSubmit) {
  $("readsLog").textContent = "";
  $("readsResults").innerHTML = "";
  const sessionId = SESSION ? SESSION.id : null;
  let runs;
  try {
    runs = mergeExperimentMetadata(RUN_ROWS);
  } catch (e) { banner("submitReadsBanner", false, e.message); return; }

  if (!HELPER_OK && !(await detectHelper())) {
    banner("submitReadsBanner", false, "The local upload helper isn't running — start it, then re-check."); return;
  }
  const dir = readsLocalDir();
  if (!dir) { banner("submitReadsBanner", false, "Set your local reads directory in step 1."); return; }

  try {
    const { plan, warnings } = await api("/api/reads/plan", { method: "POST", body: JSON.stringify({
      runs, test: TEST, session_id: sessionId, force_reupload: $("forceReupload").checked,
    }) });
    (warnings || []).forEach((w) => appendReadsLog("WARNING: " + w));

    const results = [];
    for (const entry of plan) {
      if (entry.action === "skip") {
        appendReadsLog(`=== ${entry.name} === SKIP (${entry.reason})`);
        results.push(entry);
        recordLedger(entry);
        continue;
      }
      appendReadsLog(`=== ${entry.name} === uploading via local helper…`);
      const result = await uploadOneViaHelper(entry, dir, doSubmit, sessionId);
      results.push(result);
      recordLedger(result);
      renderRunTable();
    }

    const ok = results.every((r) => r.success !== false);
    const skipped = results.filter((r) => r.skipped).length;
    banner("submitReadsBanner", ok,
      ok ? `Done: ${results.length} run(s)${skipped ? `, ${skipped} skipped` : ""}${doSubmit ? "" : " (validate only)"}.`
         : "Some runs failed — see results.");
    renderTable("readsResults", results);
    renderRunTable();
    saveSessionNow();
  } catch (e) { banner("submitReadsBanner", false, e.message); }
}

// Run one upload on the local helper and relay the outcome back to the server.
function uploadOneViaHelper(entry, inputDir, doSubmit, sessionId) {
  return new Promise(async (resolve) => {
    let job;
    try {
      job = await helperApi("/api/submit", { method: "POST", body: JSON.stringify({
        input_host_dir: inputDir, manifest_filename: entry.manifest_filename,
        manifest_text: entry.manifest_text, submit: doSubmit, test: TEST,
      }) });
    } catch (e) {
      appendReadsLog(`ERROR (${entry.name}): ${e.message}`);
      resolve({ name: entry.name, alias: entry.alias, sample: entry.sample, study: entry.study, success: false, exit_code: 1 });
      return;
    }
    const es = new EventSource(`${HELPER_BASE}/api/stream/${job.job_id}`);
    es.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.line != null) appendReadsLog(m.line);
      if (m.done) {
        es.close();
        // Relay the outcome to the server to update the ledger.
        let result;
        try {
          const r = await api("/api/reads/result", { method: "POST", body: JSON.stringify({
            session_id: sessionId, name: entry.name, alias: entry.alias, stable_alias: entry.stable_alias,
            exit_code: m.exit_code, log: m.log || "", sample: entry.sample, study: entry.study,
            experiment_accession: m.experiment_accession, run_accession: m.run_accession,
          }) });
          result = r.result;
        } catch (e) {
          appendReadsLog(`ERROR recording result (${entry.name}): ${e.message}`);
          result = { name: entry.name, alias: entry.alias, sample: entry.sample, study: entry.study,
            success: m.exit_code === 0, exit_code: m.exit_code,
            experiment_accession: m.experiment_accession || "", run_accession: m.run_accession || "" };
        }
        resolve(result);
      }
    };
    es.onerror = () => {
      es.close();
      appendReadsLog(`ERROR (${entry.name}): lost connection to the local helper.`);
      resolve({ name: entry.name, alias: entry.alias, sample: entry.sample, study: entry.study, success: false, exit_code: 1 });
    };
  });
}

function recordLedger(r) {
  if (!r || !r.name) return;
  READS_RUNS[r.name] = {
    run_name: r.name,
    status: r.skipped ? (r.reason === "already_in_ena" ? "already_in_ena" : "done")
      : (r.success ? "done" : "failed"),
    experiment_accession: r.experiment_accession || "",
    run_accession: r.run_accession || "",
  };
}
