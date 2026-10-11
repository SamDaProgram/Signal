"use strict";
// Channel inspection, trigger/preset editors and trigger list rendering.
let inspectedChannel = null;
function channelRules(universe, channel) {
  return state.rules.filter(
    (rule) => rule.universe === universe && rule.channel === channel,
  );
}
function openChannel(channel) {
  if (!state || switching) return;
  inspectedChannel = {
    universe: selectedUniverse,
    channel,
    protocol: state.input.protocol,
  };
  renderChannel();
  $("#channel-editor").showModal();
}
function renderChannel(offline = false) {
  if (!inspectedChannel) return;
  const { universe, channel, protocol } = inspectedChannel;
  if (protocol !== state.input.protocol || universe !== selectedUniverse) {
    $("#channel-editor").close();
    inspectedChannel = null;
    return;
  }
  const rules = channelRules(universe, channel),
    label = state.group_labels?.[`${universe}:${channel}`];
  $("#channel-title").textContent =
    `${protocols[protocol].label} ${universe} · Channel ${channel}`;
  $("#channel-label").textContent = label || "Connected triggers";
  const live = !offline && state.live_slots[channel - 1],
    level = state.levels[channel - 1];
  $("#channel-level").textContent =
    `${level == null ? "No value received" : "Level " + level}${live ? " · Live" : " · Not live"} · ${rules.length} trigger${rules.length === 1 ? "" : "s"}`;
  $("#channel-empty").hidden = rules.length > 0;
  const list = $("#channel-triggers"),
    ids = new Set(rules.map((rule) => rule.id));
  for (const card of [...list.children])
    if (!ids.has(card.dataset.id)) card.remove();
  for (const rule of rules) {
    let card = [...list.children].find((card) => card.dataset.id === rule.id);
    if (!card) {
      card = el("div", undefined, "channel-trigger");
      card.dataset.id = rule.id;
      card.append(el("strong"), el("p"), el("p"), el("p"));
      const edit = el("button", "Edit trigger", "small");
      edit.onclick = () => {
        const current = state.rules.find((item) => item.id === rule.id);
        if (current) {
          $("#channel-editor").close();
          openEditor(current);
        }
      };
      card.append(edit);
      list.append(card);
    }
    const status = !rule.enabled
      ? "Disabled"
      : offline || !rule.live
        ? "Waiting for data"
        : rule.active
          ? rule.action === "fade"
            ? "Following DMX"
            : rule.sequence_source === "dmx"
              ? "Following cues"
              : "Selected"
          : rule.matches
            ? "Overlap · not selected"
            : "Not matched";
    card.children[0].textContent = rule.name;
    card.children[1].textContent = `${conditionLabel(rule)} · ${status}`;
    card.children[1].className =
      "state" + (status === "Selected" ? " high" : "");
    card.children[2].textContent = actionLabel(rule);
    card.children[3].textContent =
      (rule.action || "osc") !== "osc"
        ? ""
        : rule.args.length
          ? "Arguments: " +
            rule.args
              .map(
                (arg) =>
                  `${arg.type}: ${JSON.stringify(arg.value ?? arg.type === "T")}`,
              )
              .join(", ")
          : "No arguments";
  }
}
$("#close-channel").onclick = () => $("#channel-editor").close();
$("#channel-editor").addEventListener("close", () => {
  inspectedChannel = null;
});
$("#channel-add").onclick = () => {
  if (!inspectedChannel) return;
  const channel = inspectedChannel.channel;
  $("#channel-editor").close();
  openEditor(null, channel);
};
function openEditor(rule = null, channel = 1, duplicate = false) {
  if (!requireConfigEdit()) return;
  if (!state || switching) return;
  editorInput = state.input.protocol;
  const info = protocols[editorInput];
  editing = duplicate ? null : rule?.id || null;
  $("#form").reset();
  $("#arguments").replaceChildren();
  $("#form-error").textContent = "";
  const values = rule || {
    name: "",
    universe: selectedUniverse,
    channel,
    threshold: 100,
    operator: "gte",
    threshold_max: 149,
    host: "127.0.0.1",
    port: 8000,
    address: "/go",
    enabled: true,
    args: [],
  };
  for (const key of [
    "name",
    "universe",
    "channel",
    "threshold",
    "host",
    "port",
    "address",
    "operator",
    "threshold_max",
  ])
    $("#form").elements[key].value = values[key];
  fillAction($("#form"), values);
  $("#form").elements.cooldown_ms.value = values.cooldown_ms || 0;
  $("#trigger-advanced").open = Boolean(values.cooldown_ms);
  $("#device-preset").value = values.preset_id || "";
  applyPreset();
  updateConditionFields();
  if (duplicate)
    $("#form").elements.name.value = rule.name.slice(0, 95) + " copy";
  $("#form").elements.universe.min = info.min;
  $("#form").elements.universe.max = info.max;
  $("#editor-universe-label").textContent =
    info.label + (info.min === 0 ? " (0-based)" : " (1-based)");
  $("#editor-protocol").textContent =
    info.name +
    " input · " +
    (duplicate ? "Another selectable message on this channel." : info.hint);
  $("#form").elements.enabled.checked = values.enabled;
  (values.args || []).forEach((arg) => addArgument(arg));
  $("#dialog-title").textContent = duplicate
    ? "Add trigger on channel"
    : rule
      ? "Edit trigger"
      : "New trigger";
  $("#editor").showModal();
  $("#form").elements.name.focus();
}
function openPreset(preset = null) {
  if (!requireConfigEdit()) return;
  presetEditing = preset?.id || null;
  $("#preset-form").reset();
  $("#preset-error").textContent = "";
  if (preset) $("#preset-form").elements.name.value = preset.name;
  fillAction($("#preset-form"), preset || {});
  $("#preset-title").textContent = preset
    ? "Edit action preset"
    : "New action preset";
  $("#preset-editor").showModal();
  $("#preset-form").elements.name.focus();
}
$("#add-preset").onclick = () => openPreset();
$("#close-preset").onclick = $("#cancel-preset").onclick = () =>
  $("#preset-editor").close();
$("#preset-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  let data;
  try {
    data = { name: form.elements.name.value.trim(), ...readAction(form) };
  } catch (error) {
    $("#preset-error").textContent = error.message;
    return;
  }
  $("#save-preset").disabled = true;
  $("#preset-error").textContent = "";
  try {
    await api(
      "/api/presets" + (presetEditing ? "/" + presetEditing : ""),
      presetEditing ? "PUT" : "POST",
      data,
    );
    $("#preset-editor").close();
    notice("Device preset saved.");
    pollSoon = true;
  } catch (error) {
    $("#preset-error").textContent = error.message;
  } finally {
    $("#save-preset").disabled = false;
  }
};
$("#device-preset").onchange = applyPreset;
function removePreset(preset) {
  if (!requireConfigEdit()) return;
  $("#delete-preset-name").textContent =
    `Remove “${preset.name}”? Existing triggers will keep their destinations.`;
  $("#delete-preset-dialog").returnValue = "cancel";
  $("#delete-preset-dialog").showModal();
  $("#delete-preset-dialog").onclose = async () => {
    if ($("#delete-preset-dialog").returnValue !== "delete") return;
    try {
      await api("/api/presets/" + preset.id, "DELETE");
      notice("Device preset removed.");
      pollSoon = true;
    } catch (error) {
      notice(error.message);
    }
  };
}
function renderPresets(presets) {
  const signature = JSON.stringify(presets);
  if (signature === presetSignature) return;
  presetSignature = signature;
  const selected = $("#device-preset").value;
  $("#device-preset").replaceChildren();
  const custom = el("option", "Custom action");
  custom.value = "";
  $("#device-preset").append(custom);
  for (const preset of presets) {
    const option = el(
      "option",
      `${preset.name} · ${(preset.action || "osc").toUpperCase()}`,
    );
    option.value = preset.id;
    $("#device-preset").append(option);
  }
  $("#device-preset").value = presets.some((p) => p.id === selected)
    ? selected
    : "";
  if ($("#editor").open) applyPreset();
  $("#presets").querySelector(".preset-empty")?.remove();
  const ids = new Set(presets.map((p) => p.id));
  for (const card of [...$("#presets").children])
    if (!ids.has(card.dataset.id)) card.remove();
  if (!presets.length) {
    $("#presets").replaceChildren(
      el(
        "p",
        "No presets yet. Save an OSC, MIDI, or script configuration to reuse it.",
        "preset-empty",
      ),
    );
    return;
  }
  for (const preset of presets) {
    let card = [...$("#presets").children].find(
      (c) => c.dataset.id === preset.id,
    );
    if (!card) {
      card = el("div", undefined, "preset-card");
      card.dataset.id = preset.id;
      card.append(el("div"));
      const actions = el("div", undefined, "row-actions");
      for (const label of ["Edit preset", "Remove preset"]) {
        const button = el("button", label);
        button.onclick = () => {
          const current = state.presets.find((p) => p.id === card.dataset.id);
          if (current)
            (label === "Edit preset" ? openPreset : removePreset)(current);
        };
        actions.append(button);
      }
      card.append(actions);
      $("#presets").append(card);
    }
    card.firstChild.replaceChildren(
      el("strong", preset.name),
      el("p", actionLabel(preset)),
    );
  }
}
function updateConditionFields() {
  const range = $("#operator").value === "range";
  $("#upper-level").hidden = !range;
  $("#form").elements.threshold_max.required = range;
  $("#level-label").textContent = range ? "From level" : "Level";
  if ($("#form").elements.action) updateFollowerFields();
}
$("#operator").onchange = updateConditionFields;
function conditionLabel(rule) {
  if (
    rule.action === "fade" ||
    (rule.action === "sequence" && rule.sequence_source === "dmx")
  )
    return "DMX 0–255 · continuous";
  return rule.operator === "range"
    ? `${rule.threshold}–${rule.threshold_max}`
    : `${{ gte: "≥", gt: ">", lte: "≤", lt: "<", eq: "=" }[rule.operator || "gte"]} ${rule.threshold}`;
}
$("#add").onclick = $("#first-add").onclick = () => openEditor();
$("#close").onclick = $("#cancel").onclick = () => $("#editor").close();
$("#add-arg").onclick = () => addArgument();
$("#form").onsubmit = async (event) => {
  event.preventDefault();
  if (editorInput !== state.input.protocol) {
    $("#form-error").textContent =
      "Input protocol changed. Close and reopen this editor.";
    return;
  }
  const form = event.currentTarget,
    data = {};
  for (const key of ["name", "host", "address"])
    data[key] = form.elements[key].value.trim();
  for (const key of [
    "universe",
    "channel",
    "threshold",
    "threshold_max",
    "port",
  ])
    data[key] = Number(form.elements[key].value);
  data.operator = form.elements.operator.value;
  data.enabled = form.elements.enabled.checked;
  try {
    Object.assign(data, readAction(form), {
      preset_id: $("#device-preset").value,
      cooldown_ms: Number(form.elements.cooldown_ms.value),
    });
  } catch (error) {
    $("#form-error").textContent = error.message;
    return;
  }
  data.args = readArguments($("#arguments"));
  $("#save").disabled = true;
  $("#form-error").textContent = "";
  try {
    await api(
      "/api/rules" + (editing ? "/" + editing : ""),
      editing ? "PUT" : "POST",
      data,
    );
    $("#editor").close();
    notice("Trigger saved.");
    pollSoon = true;
  } catch (error) {
    $("#form-error").textContent = error.message;
  } finally {
    $("#save").disabled = false;
  }
};
async function removeRule(rule) {
  if (!requireConfigEdit()) return;
  $("#delete-name").textContent =
    `“${rule.name}” will be removed from your saved triggers.`;
  $("#delete-dialog").returnValue = "cancel";
  $("#delete-dialog").showModal();
  $("#delete-dialog").onclose = async () => {
    if ($("#delete-dialog").returnValue !== "delete") return;
    try {
      await api("/api/rules/" + rule.id, "DELETE");
      notice("Trigger removed.");
      pollSoon = true;
    } catch (error) {
      notice(error.message);
    }
  };
}
let groupEditing = null;
function openGroup(key) {
  if (!requireConfigEdit()) return;
  groupEditing = key;
  const [universe, channel] = key.split(":");
  $("#group-location").textContent =
    `${protocols[state.input.protocol].label} ${universe} · Channel ${channel}`;
  $("#group-form").elements.label.value = state.group_labels?.[key] || "";
  $("#group-error").textContent = "";
  $("#group-editor").showModal();
  $("#group-form").elements.label.focus();
}
$("#close-group").onclick = $("#cancel-group").onclick = () =>
  $("#group-editor").close();
$("#group-form").onsubmit = async (event) => {
  event.preventDefault();
  const [universe, channel] = groupEditing.split(":").map(Number);
  $("#save-group").disabled = true;
  $("#group-error").textContent = "";
  try {
    await api("/api/groups", "PUT", {
      universe,
      channel,
      label: event.currentTarget.elements.label.value.trim(),
    });
    $("#group-editor").close();
    notice("Group label saved.");
    pollSoon = true;
  } catch (error) {
    $("#group-error").textContent = error.message;
  } finally {
    $("#save-group").disabled = false;
  }
};
$("#export-channels").onclick = async () => {
  $("#export-channels").disabled = true;
  try {
    const response = await fetch("/api/export/channels", {
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw Error("Could not export channels.");
    const url = URL.createObjectURL(await response.blob()),
      link = el("a");
    link.href = url;
    link.download = "signal-channels.csv";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notice("Channel CSV exported.");
  } catch (error) {
    notice(error.message);
  } finally {
    $("#export-channels").disabled = false;
  }
};
function renderRules(rules) {
  const info = protocols[state.input.protocol];
  const signature =
    state.input.protocol +
    JSON.stringify(state.group_labels) +
    JSON.stringify(rules);
  if (signature === ruleSignature) return;
  ruleSignature = signature;
  $("#empty").hidden = rules.length > 0;
  $("#rule-count").textContent = rules.length;
  const groups = new Map();
  for (const rule of rules) {
    const key = `${rule.universe}:${rule.channel}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(rule);
  }
  for (const details of [...$("#rules").children])
    if (!groups.has(details.dataset.key)) details.remove();
  for (const [key, group] of groups) {
    let details = [...$("#rules").children].find((d) => d.dataset.key === key);
    if (!details) {
      details = el("details", undefined, "trigger-group");
      details.dataset.key = key;
      details.open = group.length === 1;
      const summary = el("summary");
      summary.append(
        el("strong"),
        el("span", undefined, "group-count"),
        el("span", undefined, "group-status"),
      );
      const labelButton = el("button", "Label group", "small");
      labelButton.type = "button";
      labelButton.onclick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        openGroup(key);
      };
      summary.append(labelButton);
      const wrap = el("div", undefined, "table-wrap"),
        table = el("table"),
        head = el("thead"),
        headRow = el("tr");
      for (const label of [
        "Trigger",
        "Condition",
        "Action",
        "State",
        "Actions",
      ])
        headRow.append(el("th", label));
      head.append(headRow);
      table.append(head, el("tbody"));
      wrap.append(table);
      details.append(summary, wrap);
      $("#rules").append(details);
    }
    if (details.dataset.count === "1" && group.length > 1) details.open = false;
    details.dataset.count = group.length;
    const sample = group[0],
      winner = group.find((r) => r.active && r.enabled && r.live),
      summary = details.querySelector("summary");
    const label = state.group_labels?.[key];
    const groupTitle = label || (group.length === 1 ? sample.name : "");
    summary.children[0].textContent = `${groupTitle ? groupTitle + " · " : ""}${info.label} ${sample.universe} · Channel ${sample.channel}`;
    summary.children[3].textContent = label ? "Edit label" : "Label group";
    summary.children[1].textContent = `${group.length} trigger${group.length === 1 ? "" : "s"}`;
    summary.children[2].textContent = sample.live
      ? `Level ${sample.level} · ${winner ? winner.name : "No match"}`
      : "No live data";
    const tbody = details.querySelector("tbody"),
      ids = new Set(group.map((r) => r.id));
    for (const row of [...tbody.children])
      if (!ids.has(row.dataset.id)) row.remove();
    for (const rule of group) {
      let row = [...tbody.children].find((r) => r.dataset.id === rule.id);
      if (!row) {
        row = el("tr");
        row.dataset.id = rule.id;
        for (const label of [
          "Trigger",
          "Condition",
          "Action",
          "State",
          "Actions",
        ]) {
          const cell = el("td");
          cell.dataset.label = label;
          row.append(cell);
        }
        tbody.append(row);
      }
      const td = row.children;
      td[0].replaceChildren(
        el("strong", rule.name),
        el("small", rule.enabled ? "Enabled" : "Disabled"),
      );
      td[1].textContent = conditionLabel(rule);
      td[2].replaceChildren(
        el("span", actionLabel(rule)),
        el(
          "small",
          rule.preset_id ? "Linked preset · fields locked" : "Custom action",
        ),
      );
      const status = !rule.enabled
        ? "Disabled"
        : rule.universe < info.min || rule.universe > info.max
          ? "Edit universe / block"
          : !rule.live
            ? "Waiting"
            : rule.active
              ? rule.action === "fade"
                ? "Following DMX"
                : rule.sequence_source === "dmx"
                  ? "Following cues"
                  : "Selected"
              : rule.matches
                ? "Overlap · not selected"
                : "Not matched";
      td[3].replaceChildren(
        el(
          "span",
          status,
          "state" + (rule.live && rule.active && rule.enabled ? " high" : ""),
        ),
      );
      if (!td[4].firstChild) {
        const actions = el("div", undefined, "row-actions");
        for (const label of ["Test", "Edit", "Add on channel", "Remove"]) {
          const button = el("button", label);
          button.onclick = async () => {
            const current = state.rules.find((r) => r.id === row.dataset.id);
            if (!current) return;
            if (label === "Edit") return openEditor(current);
            if (label === "Add on channel")
              return openEditor(current, current.channel, true);
            if (label === "Remove") return removeRule(current);
            button.disabled = true;
            try {
              const result = await api(
                "/api/rules/" + current.id + "/test",
                "POST",
              );
              notice(
                result.status === "sent"
                  ? "Action sent. Check the receiving application."
                  : result.status === "running"
                    ? "Action started. See Sequence playback or Activity for progress."
                    : result.status === "skipped"
                      ? "Outputs are paused."
                      : "Action failed. See Activity for details.",
              );
              pollSoon = true;
            } catch (error) {
              notice(error.message);
            } finally {
              button.disabled = false;
            }
          };
          actions.append(button);
        }
        td[4].append(actions);
      }
    }
  }
  applyTriggerFilters();
}

// Filter only the view; never change saved order or trigger-selection priority.
function triggerMatches(rule, query, status, action, labels) {
  if (status === "enabled" && !rule.enabled) return false;
  if (status === "disabled" && rule.enabled) return false;
  if (action !== "all" && (rule.action || "osc") !== action) return false;
  const label = labels?.[`${rule.universe}:${rule.channel}`] || "";
  const text = [
    rule.name,
    label,
    rule.address,
    rule.host,
    rule.midi_port,
    `channel ${rule.channel}`,
    `ch ${rule.channel}`,
    `universe ${rule.universe}`,
    `block ${rule.universe}`,
    `${rule.universe}:${rule.channel}`,
  ]
    .join(" ")
    .toLowerCase();
  return query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .every((word) => text.includes(word));
}

function applyTriggerFilters() {
  if (!state) return;
  const query = $("#trigger-search").value;
  const status = $("#trigger-status").value;
  const action = $("#trigger-action").value;
  const filtering = Boolean(
    query.trim() || status !== "all" || action !== "all",
  );
  const matches = new Set(
    state.rules
      .filter((rule) =>
        triggerMatches(rule, query, status, action, state.group_labels),
      )
      .map((rule) => rule.id),
  );
  for (const group of $("#rules").children) {
    let visible = 0;
    for (const row of group.querySelectorAll("tbody tr")) {
      row.hidden = !matches.has(row.dataset.id);
      if (!row.hidden) visible++;
    }
    group.hidden = visible === 0;
    if (filtering && group.dataset.beforeFilter === undefined) {
      group.dataset.beforeFilter = String(group.open);
      group.open = true;
    } else if (!filtering && group.dataset.beforeFilter !== undefined) {
      group.open = group.dataset.beforeFilter === "true";
      delete group.dataset.beforeFilter;
    }
  }
  const summary = filtering
    ? `${matches.size} of ${state.rules.length} triggers`
    : `${state.rules.length} saved trigger${state.rules.length === 1 ? "" : "s"}`;
  if ($("#trigger-results").textContent !== summary)
    $("#trigger-results").textContent = summary;
  $("#no-trigger-matches").hidden = !state.rules.length || matches.size > 0;
  $("#reset-trigger-filters").hidden = !filtering;
  $("#expand-groups").disabled = $("#collapse-groups").disabled =
    matches.size === 0;
}

$("#trigger-search").addEventListener("input", applyTriggerFilters);
$("#trigger-status").addEventListener("change", applyTriggerFilters);
$("#trigger-action").addEventListener("change", applyTriggerFilters);
$("#reset-trigger-filters").onclick = () => {
  $("#trigger-search").value = "";
  $("#trigger-status").value = $("#trigger-action").value = "all";
  applyTriggerFilters();
  $("#trigger-search").focus();
};
for (const [id, open] of [
  ["expand-groups", true],
  ["collapse-groups", false],
]) {
  $("#" + id).onclick = () => {
    for (const group of $("#rules").children)
      if (!group.hidden) group.open = open;
  };
}
