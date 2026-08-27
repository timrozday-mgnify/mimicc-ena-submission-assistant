"use strict";

// ---------------------------------------------------------------------------
// Credentials — held in the browser for this tab only (sessionStorage), never
// persisted to disk and never sent to a server store; api() attaches them as
// headers on each request (see core.js webinHeaders).
// ---------------------------------------------------------------------------
const CREDS_KEY = "MIMICC_WEBIN_CREDS";

function credsConfigured() { return !!(CREDS.username && CREDS.password); }

function restoreCreds() {
  try {
    const raw = sessionStorage.getItem(CREDS_KEY);
    if (raw) CREDS = JSON.parse(raw);
  } catch (_) { CREDS = { username: "", password: "" }; }
  reflectCredStatus();
}

function reflectCredStatus() {
  const s = $("credStatus");
  if (!s) return;
  s.textContent = "credentials: " + (credsConfigured() ? "set" : "not set");
  s.className = "creds-status " + (credsConfigured() ? "on" : "");
}

async function saveCreds() {
  const username = $("username").value.trim(), password = $("password").value;
  if (!username || !password) { banner("credBanner", false, "Enter a Webin username and password."); return; }
  CREDS = { username, password };
  sessionStorage.setItem(CREDS_KEY, JSON.stringify(CREDS));
  // Also hand the credentials to the local helper so it can upload reads
  // (the helper holds them in memory only). Best-effort.
  if (HELPER_OK) { try { await pushCredsToHelper(username, password); } catch (_) {} }
  $("password").value = "";
  reflectCredStatus();
  banner("credBanner", true, `Credentials saved for ${TEST ? "TEST" : "PRODUCTION"} (this browser tab only). Validated on first submission.`);
}
async function clearCreds() {
  CREDS = { username: "", password: "" };
  sessionStorage.removeItem(CREDS_KEY);
  if (HELPER_OK) { try { await helperApi("/api/credentials", { method: "DELETE" }); } catch (_) {} }
  reflectCredStatus();
  banner("credBanner", true, "Credentials cleared.");
}
async function pushCredsToHelper(username, password) {
  await helperApi("/api/credentials", { method: "POST", body: JSON.stringify({ username, password }) });
}
async function refreshHealth() {
  HEALTH = await api("/api/health");
  reflectCredStatus();
  // Seed the default sample filter only when empty (don't clobber a restored
  // session value).
  if (!$("sampleFilter").value) $("sampleFilter").value = HEALTH.default_sample_filter || "";
  if (!HEALTH.dh_available) { $("dhWrap").style.display = "none"; $("dhMissing").style.display = "block"; }
  // Locate + probe the local reads upload helper.
  if (HEALTH.helper_port) HELPER_BASE = `http://localhost:${HEALTH.helper_port}`;
  detectHelper();
}

// ---------------------------------------------------------------------------
// Local reads upload helper detection
// ---------------------------------------------------------------------------
async function detectHelper() {
  const pill = $("helperStatus");
  try {
    const h = await helperApi("/api/health");
    HELPER_OK = h.status === "ok";
  } catch (_) { HELPER_OK = false; }
  if (pill) {
    pill.textContent = HELPER_OK ? "helper: running" : "helper: not detected";
    pill.className = "vf-badge" + (HELPER_OK ? " vf-badge--primary" : "");
  }
  const miss = $("helperMissing");
  if (miss) miss.style.display = HELPER_OK ? "none" : "block";
  if ($("scanReadsBtn")) $("scanReadsBtn").disabled = !HELPER_OK;
  return HELPER_OK;
}
