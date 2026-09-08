"use strict";

// ---------------------------------------------------------------------------
// Light/dark: <html data-theme> is the single source of truth. <ena-browser>
// and the dhtb sidecar (see schema.js syncDhtbTheme) both follow it; the
// DataHarmonizer bundle is light-only and stays light.
// ---------------------------------------------------------------------------
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("mimicc-theme", theme);
  const btn = $("themeToggle");
  if (btn) {
    btn.textContent = theme === "dark" ? "Light" : "Dark";
    btn.setAttribute("aria-pressed", String(theme === "dark"));
  }
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

function initTheme() {
  applyTheme(localStorage.getItem("mimicc-theme")
    || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
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
