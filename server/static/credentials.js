"use strict";

// ---------------------------------------------------------------------------
// Credentials
// ---------------------------------------------------------------------------
async function saveCreds() {
  const username = $("username").value, password = $("password").value;
  try {
    await api("/api/credentials", { method: "POST", body: JSON.stringify({ username, password, test: TEST }) });
    // Also hand the credentials to the local helper so it can upload reads
    // (the helper holds them in memory only). Best-effort — reads can be
    // re-credentialed later if the helper isn't up yet.
    if (HELPER_OK) { try { await pushCredsToHelper(username, password); } catch (_) {} }
    $("password").value = "";
    banner("credBanner", true, `Credentials validated and saved for ${TEST ? "TEST" : "PRODUCTION"} (memory only).`);
    refreshHealth();
  } catch (e) { banner("credBanner", false, e.message); }
}
async function clearCreds() {
  await api("/api/credentials", { method: "DELETE" });
  if (HELPER_OK) { try { await helperApi("/api/credentials", { method: "DELETE" }); } catch (_) {} }
  banner("credBanner", true, "Credentials cleared.");
  refreshHealth();
}
async function pushCredsToHelper(username, password) {
  await helperApi("/api/credentials", { method: "POST", body: JSON.stringify({ username, password }) });
}
async function refreshHealth() {
  HEALTH = await api("/api/health");
  const s = $("credStatus");
  s.textContent = "credentials: " + (HEALTH.credentials_configured ? "set" : "not set");
  s.className = "creds-status " + (HEALTH.credentials_configured ? "on" : "");
  // Account UI: show the signed-in user + admin tab.
  if (HEALTH.username) {
    $("userBox").style.display = "inline-flex";
    $("userName").textContent = HEALTH.username + (HEALTH.is_admin ? " (admin)" : "");
    // Hide the explicit Log out button in local single-user mode.
    $("logoutBtn").style.display = HEALTH.deployment_mode === "hosted" ? "inline-block" : "none";
  }
  $("adminTabBtn").style.display = HEALTH.is_admin ? "inline-block" : "none";
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
    pill.className = "tag" + (HELPER_OK ? " high" : "");
  }
  const miss = $("helperMissing");
  if (miss) miss.style.display = HELPER_OK ? "none" : "block";
  if ($("scanReadsBtn")) $("scanReadsBtn").disabled = !HELPER_OK;
  return HELPER_OK;
}
