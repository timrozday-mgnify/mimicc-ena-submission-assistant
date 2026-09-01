"use strict";

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

async function loadRecords(entity, outId, status = "all", withActions = false, fullFields = false) {
  appendLog("recLog", `Fetching ${entity} (status=${status}, test=${TEST}, full_fields=${fullFields})…`);
  try {
    const rows = await api(`/api/records/${entity}?test=${TEST}&status=${status}&full_fields=${fullFields}`);
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
  // The known columns first, then whatever else the rows carry — with
  // full_fields on that is every checklist attribute the samples were
  // submitted with, and the point of asking for them is seeing them.
  const known = RECORD_COLUMNS[entity] || ["accession", "alias", "title", "status"];
  const extra = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((c) => !known.includes(c));
  const cols = [...known.filter((c) => rows.some((r) => c in r)), ...extra];
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
