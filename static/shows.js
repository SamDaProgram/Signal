"use strict";
// Show files and session output controls.
function renderShow(data) {
  const show = data.show;
  if (show) {
    $("#current-show").textContent = show.current;
    $("#active-show-name").textContent = show.current;
    $("#show-files").title = "Show files · " + show.current;
    $("#show-directory").textContent = show.directory;
    const signature = JSON.stringify(show.files),
      select = $("#show-select");
    if (select.dataset.signature !== signature) {
      const selected = select.value;
      select.replaceChildren();
      for (const name of show.files) {
        const option = el("option", name);
        option.value = name;
        select.append(option);
      }
      select.value = show.files.includes(selected) ? selected : show.current;
      select.dataset.signature = signature;
    }
    if (select.dataset.current !== show.current) {
      select.value = show.current;
      select.dataset.current = show.current;
    }
  }
  $("#toggle-outputs").textContent = data.armed
    ? "Pause outputs"
    : "Enable outputs";
  $("#toggle-outputs").classList.toggle("primary", !data.armed);
  $("#output-state").textContent = data.armed
    ? "Outputs enabled"
    : "Outputs paused";
  $("#output-indicator").classList.toggle("armed", data.armed);
  $("#show-status").textContent = data.armed
    ? "Ready to run your actions."
    : "Input monitoring continues.";
  $("#scripts-enabled").checked = data.scripts_enabled;
  const addresses = data.receive_addresses || [];
  $("#receive-endpoint").textContent =
    `${protocols[data.input.protocol].name} → ${addresses.map((ip) => `${ip}:${data.input_port}`).join(" or ")} (UDP)`;
  $("#receive-help").textContent =
    "Use a network IP reachable by your lighting controller. 127.0.0.1 is for senders on this Mac only." +
    (data.input.protocol === "e131"
      ? ` Multicast for the monitored universe: 239.255.${data.input.monitor_universe >> 8}.${data.input.monitor_universe & 255}:${data.input_port}.`
      : "");
}
async function runtimeChange(changes) {
  try {
    await api("/api/runtime", "PUT", changes);
    inputRevision++;
    render(await api("/api/state?universe=" + selectedUniverse));
  } catch (error) {
    notice(error.message);
    pollSoon = true;
  }
}
$("#toggle-outputs").onclick = () => {
  if (state) runtimeChange({ armed: !state.armed });
};
$("#scripts-enabled").onchange = () =>
  runtimeChange({ scripts_enabled: $("#scripts-enabled").checked });
$("#refresh-network").onclick = async () => {
  try {
    await api("/api/network", "POST", {});
    pollSoon = true;
    notice("Network addresses refreshed.");
  } catch (error) {
    notice(error.message);
  }
};
$("#refresh-midi").onclick = refreshMidiPorts;

function showFileError(message) {
  $("#show-file-error").textContent = message;
  $("#show-file-error").hidden = false;
}
$("#show-files").onclick = () => {
  $("#show-file-error").hidden = true;
  $("#show-files-dialog").showModal();
};
$("#close-show-files").onclick = () => $("#show-files-dialog").close();
function showFilename(fallback) {
  const value = $("#show-name").value.trim() || fallback;
  if (!value) throw Error("Enter a new show filename.");
  return value.endsWith(".json") ? value : value + ".json";
}
async function showOperation(operation, extra = {}) {
  if (switching) return;
  switching = true;
  inputRevision++;
  $("#show-file-error").hidden = true;
  const buttons = [
    ...document.querySelectorAll("#show-files-dialog button, #show-files"),
  ];
  buttons.forEach((b) => (b.disabled = true));
  try {
    const result = await api("/api/shows", "POST", { operation, ...extra });
    for (const dialog of document.querySelectorAll("dialog[open]"))
      dialog.close();
    const data = await api("/api/state");
    selectedUniverse = data.input.monitor_universe;
    ruleSignature = "";
    presetSignature = "";
    render(data);
    $("#show-name").value = "";
    notice(
      `${result.current} ${operation === "save" ? "saved" : "opened"}${data.armed ? "" : ". Outputs are paused"}.`,
    );
  } catch (error) {
    showFileError(error.message);
  } finally {
    switching = false;
    buttons.forEach((b) => (b.disabled = false));
    pollSoon = true;
  }
}
$("#load-show").onclick = () =>
  showOperation("load", { name: $("#show-select").value });
$("#save-show").onclick = () => showOperation("save");
$("#copy-show").onclick = () => {
  try {
    showOperation("save_as", { name: showFilename() });
  } catch (error) {
    showFileError(error.message);
  }
};
$("#new-show").onclick = () => {
  try {
    showOperation("new", { name: showFilename() });
  } catch (error) {
    showFileError(error.message);
  }
};
$("#upload-show").onclick = () => $("#show-upload").click();
$("#show-upload").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 8 * 1024 * 1024) throw Error("Show file exceeds 8 MB.");
    const show = JSON.parse(await file.text());
    await showOperation("upload", { name: file.name, show });
  } catch (error) {
    showFileError(error.message);
  } finally {
    event.target.value = "";
  }
};
$("#download-show").onclick = () => {
  const link = el("a");
  link.href = "/api/shows/download";
  link.download = state?.show?.current || "signal-show.json";
  document.body.append(link);
  link.click();
  link.remove();
};
