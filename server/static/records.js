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
    scheduleSave();
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
    scheduleSave();
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    banner("recBanner", false, e.message);
  }
}

// ---------------------------------------------------------------------------
// Authentication + account management
// ---------------------------------------------------------------------------
async function doLogin() {
  try {
    await api("/api/auth/login", { method: "POST", body: JSON.stringify({
      username: $("loginUsername").value, password: $("loginPassword").value,
    }) });
    $("loginModal").classList.remove("show");
    $("loginPassword").value = "";
    $("loginBanner").className = "banner";
    await startApp();
  } catch (e) { banner("loginBanner", false, e.message); }
}
async function doLogout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  location.reload();
}
async function loadUsers() {
  try { renderUserList(await api("/api/admin/users")); }
  catch (e) { banner("adminBanner", false, e.message); }
}
function renderUserList(users) {
  const el = $("adminUserList");
  if (!users.length) { el.innerHTML = '<p class="muted" style="padding:10px">No users.</p>'; return; }
  let h = "<table><thead><tr><th>username</th><th>role</th><th>last login</th><th>actions</th></tr></thead><tbody>";
  users.forEach((u) => {
    const last = u.last_login ? u.last_login.slice(0, 19).replace("T", " ") : "—";
    h += `<tr><td>${u.username}</td><td>${u.is_admin ? "admin" : "user"}</td><td>${last}</td><td>`
      + `<button class="btn secondary" style="padding:3px 8px" onclick="resetUserPassword(${u.id}, ${JSON.stringify(u.username)})">reset password</button> `
      + `<button class="btn danger" style="padding:3px 8px" onclick="deleteUser(${u.id}, ${JSON.stringify(u.username)})">delete</button>`
      + `</td></tr>`;
  });
  el.innerHTML = h + "</tbody></table>";
}
async function createUser() {
  try {
    await api("/api/admin/users", { method: "POST", body: JSON.stringify({
      username: $("newUserName").value, password: $("newUserPassword").value, is_admin: $("newUserAdmin").checked,
    }) });
    $("newUserName").value = ""; $("newUserPassword").value = ""; $("newUserAdmin").checked = false;
    banner("adminBanner", true, "User created.");
    loadUsers();
  } catch (e) { banner("adminBanner", false, e.message); }
}
async function deleteUser(id, name) {
  if (!confirm(`Delete user "${name}"?`)) return;
  try { await api(`/api/admin/users/${id}`, { method: "DELETE" }); banner("adminBanner", true, "User deleted."); loadUsers(); }
  catch (e) { banner("adminBanner", false, e.message); }
}
async function resetUserPassword(id, name) {
  const pw = prompt(`New password for "${name}":`);
  if (!pw) return;
  try { await api(`/api/admin/users/${id}/password`, { method: "POST", body: JSON.stringify({ password: pw }) }); banner("adminBanner", true, "Password updated."); }
  catch (e) { banner("adminBanner", false, e.message); }
}
