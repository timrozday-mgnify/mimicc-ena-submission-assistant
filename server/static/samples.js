"use strict";

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
