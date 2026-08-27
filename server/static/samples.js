"use strict";

// ---------------------------------------------------------------------------
// Studies
// ---------------------------------------------------------------------------
async function prepareStudies() {
  const dh = studyDhApi();
  if (!dh) {
    const msg = "Study DataHarmonizer isn't ready. Select a schema first.";
    banner("studyPrepBanner", false, msg);
    renderSubmissionLog("studyLog", { logs: [`ERROR: ${msg}`] });
    return;
  }
  try {
    const exportJson = dh.getExportJson();
    await saveStudyDhExport(exportJson, { silent: true });
    const r = await api("/api/study/prepare", { method: "POST", body: JSON.stringify({ export: exportJson }) });
    window.__preparedStudies = r.records;
    banner("studyPrepBanner", true, `Prepared ${r.records.length} study record(s). Ready to submit.`);
    renderTable("studyPrepOut", r.records);
    scheduleSave();
  } catch (e) {
    banner("studyPrepBanner", false, e.message);
    renderSubmissionLog("studyLog", { logs: [`ERROR: ${e.message}`] });
  }
}

async function submitStudies() {
  let clientLogs = [];
  try {
    const records = window.__preparedStudies;
    if (!records || !records.length) {
      const msg = "No prepared studies. Click Prepare first.";
      banner("studyBanner", false, msg);
      renderSubmissionLog("studyLog", { logs: [`ERROR: ${msg}`] });
      return;
    }
    clientLogs = [
      `INFO: Browser started study submission for ${records.length} prepared record(s).`,
      "INFO: Sending prepared studies to the local server for ENA pre-validation and submission.",
    ];
    banner("studyBanner", true, "Submitting prepared studies...");
    renderSubmissionLog("studyLog", { logs: clientLogs });
    window.__lastStudySubmitResponse = { accessions: [], logs: clientLogs };
    const r = await api("/api/study/submit", { method: "POST", body: JSON.stringify({
      records, test: TEST, modify: $("studyModify").checked,
      hold_until: $("studyHold").value || null, public: $("studyPublic").checked,
    }) });
    window.__lastStudySubmitResponse = r;
    banner(
      "studyBanner",
      r.success,
      r.success ? `Submitted ${(r.accessions || []).length} study record(s).` : submissionFailureMessage(r)
    );
    renderSubmissionLog("studyLog", r);
    renderTable("studyOut", r.accessions || []);
    await saveSessionNow();
  } catch (e) {
    const failure = { accessions: [], logs: [...clientLogs, `ERROR: ${e.message}`] };
    window.__lastStudySubmitResponse = failure;
    banner("studyBanner", false, e.message);
    renderSubmissionLog("studyLog", failure);
    renderTable("studyOut", []);
    await saveSessionNow();
  }
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
  const dh = dhApi();
  let exportJson;
  if (dh) {
    exportJson = dh.getExportJson();
    await saveDhExport(exportJson, { silent: true });
  } else {
    try {
      exportJson = JSON.parse($("dhExport").value);
    } catch (e) {
      banner("prepBanner", false, "Paste or upload a DH export JSON, or wait for the grid above to finish loading.");
      $("sampleSubmitBtn").disabled = true;
      return;
    }
  }
  try {
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
    banner("sampleBanner", r.success, r.success ? `Submitted ${(r.accessions || []).length} sample(s).` : (r.error || "Submission failed."));
    renderSubmissionResult("sampleOut", r);
    if (r.success && Array.isArray(r.accessions)) {
      READ_SAMPLES = r.accessions;
      SELECTED_SAMPLE = "";
      renderReadSampleList();
    }
    scheduleSave();
  } catch (e) {
    banner("sampleBanner", false, e.message);
    renderSubmissionResult("sampleOut", { accessions: [], logs: [`ERROR: ${e.message}`] });
  }
}
