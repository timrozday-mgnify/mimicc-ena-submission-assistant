"use strict";

// ---------------------------------------------------------------------------
// Boot: runs last, after every other script has defined its globals. Holds
// the only top-level statements that immediately call across files — the
// theme bootstrap (reaches postToDhtb) and init() (reaches nearly every
// section).
// ---------------------------------------------------------------------------

// Theme bootstrap (depends on applyTheme / propagateThemeToFrames / postToDhtb)
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
// Init
// ---------------------------------------------------------------------------
async function init() {
  HEALTH = await api("/api/health");
  if (HEALTH.deployment_mode === "hosted" && !HEALTH.authenticated) {
    $("loginModal").classList.add("show");  // gate on login; startApp() runs after doLogin()
    return;
  }
  await startApp();
}

async function startApp() {
  await refreshHealth();
  captureInitialDefaults();  // pristine blank-slate snapshot, used to reset between sessions
  initDhFrames();            // point both DH iframes at explicit ?template= paths
  refreshSchemaList();       // schema library + the Samples/Reads grid selectors
  refreshEnaSources();       // bundled ENA checklist/XSD options for "Build a new schema"
  initSchemaEditorFrame();   // point the Schema tab's editor iframe at the dhtb sidecar
  setSessionChip();          // no session yet -> body.no-session (blurs/locks tabs)
  openSessionModal();        // force a session pick on load
}
init();
