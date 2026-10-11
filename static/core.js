"use strict";
// Shared DOM helpers, view state and API access.
const $ = (s) => document.querySelector(s);
function showPage(focusHeading = false) {
  const settings = location.hash === "#settings";
  $("#dashboard-view").hidden = settings;
  $("#settings-view").hidden = !settings;
  if (settings) $("#settings-link").setAttribute("aria-current", "page");
  else $("#settings-link").removeAttribute("aria-current");
  document.title = settings
    ? "Settings · Signal"
    : "Signal · Network channels → Actions";
  if (focusHeading) {
    $(settings ? "#settings-title" : "#dashboard-title").focus({
      preventScroll: true,
    });
    window.scrollTo(0, 0);
  }
}
window.addEventListener("hashchange", () => showPage(true));
showPage();
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
let state = null,
  editing = null,
  selectedUniverse = 0,
  ruleSignature = "",
  eventSignature = "",
  noticeTimer,
  switching = false,
  inputRevision = 0,
  editorInput = null,
  presetEditing = null,
  presetSignature = "";
const protocols = {
  artnet: {
    name: "Art-Net",
    min: 0,
    max: 32767,
    label: "Universe",
    hint: "Art-Net universes start at 0. Send unicast or broadcast to this Mac on UDP 6454.",
  },
  e131: {
    name: "E1.31 / sACN",
    min: 1,
    max: 63999,
    label: "Universe",
    hint: "E1.31 universes start at 1. Send unicast or multicast on UDP 5568. Set any existing universe 0 triggers to your sACN universe.",
  },
  ddp: {
    name: "DDP",
    min: 0,
    max: 255,
    label: "Block",
    hint: "DDP uses byte offsets. Block 0 = channels 1–512; block 1 = 513–1024. Send 8-bit data to output ID 1 on UDP 4048, with Push on the final packet.",
  },
};
async function api(path, method = "GET", body) {
  if (configMutation(path, method, body) && !requireConfigEdit())
    throw Error("Unlock editing, then try the change again.");
  const response = await fetch(path, {
    method,
    headers: {
      "X-Signal-Edit": editToken,
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(state?.show ? { "X-Signal-Show": state.show.current } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(5000),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed");
  return data;
}
function notice(message) {
  $("#notice").textContent = message;
  $("#notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => ($("#notice").hidden = true), 6000);
}
