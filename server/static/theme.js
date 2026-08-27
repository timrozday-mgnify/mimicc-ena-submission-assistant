"use strict";

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
