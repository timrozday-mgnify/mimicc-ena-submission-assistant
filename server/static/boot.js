"use strict";

// ---------------------------------------------------------------------------
// Boot: runs last, after every other script has defined its globals. Holds
// the only top-level statements that immediately call across files — the
// theme bootstrap (reaches postToDhtb) and init() (reaches nearly every
// section).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  await startApp();
}

async function startApp() {
  initTheme();               // stamp <html data-theme> before anything paints
  restoreCreds();            // pull Webin creds saved for this browser tab (if any)
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
