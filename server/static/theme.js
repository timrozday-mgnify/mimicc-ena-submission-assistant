"use strict";

// ---------------------------------------------------------------------------
// Theme (light/dark/system)
// ---------------------------------------------------------------------------
const THEME_KEY = "mimicc-theme";
const prefersLight = window.matchMedia("(prefers-color-scheme: light)");

function applyTheme(theme) {
  const effective = theme === "system" ? (prefersLight.matches ? "light" : "dark") : theme;
  document.documentElement.setAttribute("data-theme", effective);
  propagateThemeToFrames(effective);
}

function currentEffectiveTheme() {
  return document.documentElement.getAttribute("data-theme") || "dark";
}

// Push the active theme into the embedded DataHarmonizer iframes (same-origin,
// so the attribute can be set directly) and the dhtb iframe (cross-origin,
// so it goes through the postMessage bridge — see dhtb.setTheme below).
function propagateThemeToFrames(effective) {
  for (const frameId of ["dhFrame", "expDhFrame"]) {
    try {
      const doc = $(frameId).contentDocument;
      if (doc) doc.documentElement.setAttribute("data-theme", effective);
    } catch { /* iframe not loaded yet */ }
  }
  postToDhtb("dhtb.setTheme", { theme: effective });
}

function stabilizeDataHarmonizerFrameRows(frameId) {
  const frame = $(frameId);
  if (!frame) return;
  const poll = setInterval(() => {
    try {
      const win = frame.contentWindow;
      const doc = frame.contentDocument;
      if (!win?.dataHarmonizer?.ready || !doc?.querySelector(".handsontable")) return;
      clearInterval(poll);

      let style = doc.getElementById("mimicc-dh-stable-row-sizing");
      if (!style) {
        style = doc.createElement("style");
        style.id = "mimicc-dh-stable-row-sizing";
        doc.head.appendChild(style);
      }
      style.textContent = `
        .handsontable td {
          height: 30px;
          max-height: 30px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .handsontable tbody tr {
          height: 30px;
        }
      `;
    } catch {
      clearInterval(poll);
    }
  }, 250);
  setTimeout(() => clearInterval(poll), 15000);
}
