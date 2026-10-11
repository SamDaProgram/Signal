"use strict";
// Input selection, live monitoring and server polling.
const cells = Array.from({ length: 512 }, (_, i) => {
  const node = el("button", undefined, "channel");
  node.append(
    el("span", String(i + 1).padStart(3, "0"), "number"),
    el("span", "—", "level"),
  );
  node.addEventListener("click", () => openChannel(i + 1));
  $("#channels").append(node);
  return node;
});
$("#clear-log").onclick = async () => {
  $("#clear-log").disabled = true;
  try {
    await api("/api/log", "DELETE");
    inputRevision++;
    eventSignature = "";
    render(await api("/api/state?universe=" + selectedUniverse));
    notice("Activity log cleared.");
  } catch (error) {
    notice(error.message);
  } finally {
    $("#clear-log").disabled = false;
  }
};
async function updateInput(changes) {
  if (!state || switching) return;
  switching = true;
  inputRevision++;
  $("#protocol").disabled = true;
  $("#universe").disabled = true;
  $("#save-interface").disabled = true;
  try {
    const settings = await api("/api/input", "PUT", changes);
    selectedUniverse = settings.monitor_universe;
    state.input = settings;
    ruleSignature = "";
    render(await api("/api/state?universe=" + selectedUniverse));
    notice("Input settings saved.");
  } catch (error) {
    notice(error.message);
    $("#protocol").value = state.input.protocol;
    $("#universe").value = state.input.monitor_universe;
  } finally {
    switching = false;
    $("#protocol").disabled = false;
    $("#universe").disabled = false;
    $("#save-interface").disabled = false;
    paintConfigLock();
    pollSoon = true;
  }
}
$("#protocol").onchange = () => {
  const protocol = $("#protocol").value,
    info = protocols[protocol];
  updateInput({
    protocol,
    monitor_universe: Math.max(info.min, Math.min(info.max, selectedUniverse)),
  });
};
$("#save-interface").onclick = () =>
  updateInput({ sacn_interface: $("#sacn-interface").value.trim() });
$("#universe").onchange = () => {
  const value = Number($("#universe").value),
    info = protocols[state?.input.protocol || "artnet"];
  if (Number.isInteger(value) && value >= info.min && value <= info.max)
    updateInput({ monitor_universe: value });
  else {
    notice(
      `Enter a ${info.label.toLowerCase()} from ${info.min} to ${info.max}.`,
    );
    $("#universe").value = selectedUniverse;
  }
};
function render(data) {
  if (state?.show?.current && state.show.current !== data.show?.current) {
    for (const dialog of document.querySelectorAll("dialog[open]"))
      dialog.close();
  }
  state = data;
  renderShow(data);
  renderPresets(data.presets || []);
  const info = protocols[data.input.protocol];
  selectedUniverse = data.input.monitor_universe;
  $("#protocol").value = data.input.protocol;
  if (document.activeElement !== $("#universe"))
    $("#universe").value = selectedUniverse;
  $("#universe").min = info.min;
  $("#universe").max = info.max;
  $("#sacn-settings").hidden = data.input.protocol !== "e131";
  if (document.activeElement !== $("#sacn-interface"))
    $("#sacn-interface").value = data.input.sacn_interface;
  $("#protocol-help").textContent = info.hint;
  $("#monitor-label").textContent = info.label;
  $("#input-name").textContent = info.name.toUpperCase() + " INPUT";
  $("#input-port").textContent = data.input_port;
  $("#monitor-help").textContent =
    `${info.label} ${selectedUniverse}. Hover for connected triggers; click to view details or add a trigger. A dash means no value received; dimmed values are stale.`;
  $("#channel-range").textContent =
    data.input.protocol === "ddp"
      ? `DDP channels ${selectedUniverse * 512 + 1}–${(selectedUniverse + 1) * 512}`
      : "Channels 1–512";
  $("#connection").textContent = "● Bridge online";
  $("#connection").className = "badge";
  $("#packets").textContent = data.packets.toLocaleString();
  $("#sent").textContent = data.sent.toLocaleString();
  $("#enabled").textContent = data.rules.filter(
    (r) => r.enabled && r.universe >= info.min && r.universe <= info.max,
  ).length;
  const live = data.universes.filter((u) => u.live).length;
  $("#input-status").textContent = data.receiver_error
    ? `Receiver error: ${data.receiver_error}`
    : live
      ? `${live} live universe${live === 1 ? "" : "s"}`
      : "Waiting for a source";
  const frame = data.universes.find((u) => u.universe === selectedUniverse);
  $("#source").textContent = frame?.source
    ? `Source ${frame.source} · ${data.levels.length} channels · ${frame.live ? "receiving" : `last packet ${Math.floor(frame.age)}s ago`}`
    : `Send ${info.name} to this Mac on UDP port ${data.input_port}`;
  $("#frame-status").textContent = frame?.live
    ? "● LIVE"
    : frame?.source
      ? "● STALE · LAST KNOWN VALUES"
      : "○ NO SIGNAL";
  $("#frame-status").style.color = frame?.live
    ? "var(--accent)"
    : "var(--muted)";
  $("#channels").classList.toggle("stale", !frame?.live);
  const assigned = new Map();
  data.rules
    .filter((r) => r.universe === selectedUniverse)
    .forEach((r) =>
      assigned.set(r.channel, (assigned.get(r.channel) || 0) + 1),
    );
  cells.forEach((node, i) => {
    const value = data.levels[i],
      count = assigned.get(i + 1) || 0;
    node.classList.toggle("slot-stale", value != null && !data.live_slots[i]);
    node.lastChild.textContent = value ?? "—";
    node.classList.toggle("assigned", assigned.has(i + 1));
    node.style.background =
      value == null ? "" : `hsl(88 27% ${13 + (value / 255) * 22}%)`;
    node.title = `${info.label} ${selectedUniverse}, channel ${i + 1}: ${value ?? "no data"}${data.live_slots[i] ? "" : " (not live)"} · ${count} trigger${count === 1 ? "" : "s"}`;
    const label = data.group_labels?.[`${selectedUniverse}:${i + 1}`];
    const connected = channelRules(selectedUniverse, i + 1);
    node.title +=
      (label ? "\n" + label : "") +
      "\n" +
      (connected.length
        ? connected
            .map(
              (rule) =>
                `${rule.name} · ${conditionLabel(rule)} → ${actionLabel(rule)}${rule.enabled ? "" : " (disabled)"}`,
            )
            .join("\n")
        : "No connected triggers. Click to add one.");
    node.setAttribute("aria-label", node.title);
    node.setAttribute("aria-haspopup", "dialog");
  });
  filterMonitorChannels();
  renderRules(data.rules);
  renderChannel();
  renderSequenceRuns(data.sequences || []);
  renderFadeLive(data);
  renderSequenceLive(data);
  const signature = JSON.stringify(data.events);
  if (signature !== eventSignature) {
    eventSignature = signature;
    $("#events").replaceChildren();
    if (!data.events.length)
      $("#events").append(
        el("p", "Messages will appear here when a trigger fires.", "muted"),
      );
    for (const event of data.events) {
      const row = el("div", undefined, "event " + event.status),
        name = el("div", event.name),
        result = el(
          "div",
          {
            sent: "✓ Completed / sent",
            running: "↗ Running",
            completed: "✓ Sequence complete",
            canceled: "– Canceled",
            skipped: "– Skipped",
            error: "Action error",
          }[event.status] || event.status,
          "result",
        );
      name.append(el("small", event.reason));
      result.append(el("small", event.detail));
      row.append(
        el("time", new Date(event.time * 1000).toLocaleTimeString()),
        name,
        result,
      );
      $("#events").append(row);
    }
  }
}
let pollSoon = false;
async function poll() {
  try {
    if (switching) return;
    const revision = inputRevision;
    const universe = selectedUniverse;
    const data = await api("/api/state?universe=" + universe);
    if (!switching && revision === inputRevision) {
      if (data.input.monitor_universe !== universe) {
        selectedUniverse = data.input.monitor_universe;
        pollSoon = true;
      } else render(data);
    }
  } catch (error) {
    $("#connection").textContent = "● Bridge disconnected";
    $("#connection").className = "badge offline";
    $("#frame-status").textContent = "OFFLINE · VALUES NOT LIVE";
    $("#channels").classList.add("stale");
    $("#input-status").textContent = "Connection lost";
    if (state) {
      renderRules(state.rules.map((r) => ({ ...r, live: false })));
      renderChannel(true);
      if (fadeOwner && $("#fade-follow").checked)
        $("#fade-live-status").textContent =
          "Bridge disconnected · preview holds last received level";
    }
  } finally {
    const delay = pollSoon ? 0 : 500;
    pollSoon = false;
    setTimeout(poll, delay);
  }
}

function filterMonitorChannels() {
  const assignedOnly = $("#channel-visibility").value === "assigned";
  let visible = 0;
  for (const cell of cells) {
    cell.hidden = assignedOnly && !cell.classList.contains("assigned");
    if (!cell.hidden) visible++;
  }
  $("#monitor-empty").hidden = visible > 0;
}
$("#channel-visibility").onchange = filterMonitorChannels;
