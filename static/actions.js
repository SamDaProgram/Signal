"use strict";
// Reusable action editors: OSC arguments, MIDI, scripts and linked presets.
function addArgument(
  arg = { type: "i", value: 1 },
  container = $("#arguments"),
) {
  const row = el("div", undefined, "arg"),
    type = el("select"),
    value = el("input"),
    remove = el("button", "×");
  type.setAttribute("aria-label", "Argument type");
  value.setAttribute("aria-label", "Argument value");
  for (const [key, label] of [
    ["i", "Integer"],
    ["f", "Float"],
    ["s", "Text"],
    ["T", "True"],
    ["F", "False"],
  ]) {
    const option = el("option", label);
    option.value = key;
    type.append(option);
  }
  type.value = arg.type;
  value.value = arg.value ?? "";
  remove.type = "button";
  remove.setAttribute("aria-label", "Remove argument");
  remove.onclick = () => row.remove();
  const update = () => {
    value.hidden = ["T", "F"].includes(type.value);
    value.disabled = value.hidden;
    value.type = ["i", "f"].includes(type.value) ? "number" : "text";
    value.step = type.value === "i" ? "1" : "any";
    value.required = !value.disabled && type.value !== "s";
  };
  type.onchange = update;
  update();
  row.append(type, value, remove);
  container.append(row);
  row.updateArgument = update;
}

function readArguments(container) {
  return Array.from(container.children).map((row) => {
    const type = row.querySelector("select").value,
      raw = row.querySelector("input").value;
    return ["T", "F"].includes(type)
      ? { type }
      : { type, value: ["i", "f"].includes(type) ? Number(raw) : raw };
  });
}
let midiPorts = [],
  midiRefresh = null;
function populateMidiPorts(field, selected = field.value) {
  field.replaceChildren();
  const placeholder = el("option", "Select a MIDI output");
  placeholder.value = "";
  field.append(placeholder);
  for (const name of new Set([...midiPorts, ...(selected ? [selected] : [])])) {
    const option = el(
      "option",
      name + (midiPorts.includes(name) ? "" : " (saved · unavailable)"),
    );
    option.value = name;
    field.append(option);
  }
  field.value = selected;
}
function refreshMidiPorts() {
  if (midiRefresh) return midiRefresh;
  midiRefresh = (async () => {
    const buttons = [
      $("#refresh-midi"),
      ...document.querySelectorAll(".refresh-midi"),
    ];
    buttons.forEach((button) => (button.disabled = true));
    let message;
    try {
      const data = await api("/api/midi");
      midiPorts = data.ports || [];
      for (const field of document.querySelectorAll('[name="midi_port"]'))
        populateMidiPorts(field);
      message =
        data.error ||
        (midiPorts.length
          ? "MIDI outputs updated."
          : "No MIDI outputs found. Connect a device or enable an IAC bus, then refresh.");
    } catch (error) {
      message = error.message;
    } finally {
      $("#midi-status").textContent = message;
      for (const status of document.querySelectorAll(".midi-port-status"))
        status.textContent = message;
      for (const button of buttons) {
        const form = button.closest("form");
        button.disabled = Boolean(
          button.closest('[data-action="midi"]')?.hidden ||
            (form?.id === "form" && form.elements.action.disabled) ||
            (form?.id === "step-form" && sequenceReadOnly),
        );
      }
      midiRefresh = null;
    }
  })();
  return midiRefresh;
}

const actionFields = {
  osc: ["host", "port", "address"],
  midi: ["midi_port", "midi_type", "midi_channel", "midi_number", "midi_value"],
  script: [
    "script_path",
    "script_args",
    "script_cwd",
    "script_timeout",
    "script_fields",
    "script_output",
  ],
  sequence: [
    "steps",
    "sequence_on_error",
    "sequence_source",
    "sequence_direction",
  ],
  fade: [
    "host",
    "port",
    "address",
    "fade_keyframes",
    "fade_type",
    "fade_curve",
    "fade_rate",
    "fade_range",
    "fade_input_display",
  ],
};
function renderScriptFields(form) {
  const hidden = form.elements.script_fields,
    container = form.querySelector(".script-custom-fields");
  const fields = JSON.parse(hidden.value || "[]");
  container.replaceChildren();
  const save = () => {
    hidden.value = JSON.stringify(fields);
  };
  for (const [index, item] of fields.entries()) {
    const row = el("fieldset"),
      legend = el("legend", item.label || item.key);
    row.append(legend);
    const definition = el("details"),
      summary = el("summary", "Edit field definition");
    definition.append(summary);
    row.append(definition);
    const input = (title, tag = "input") => {
      const label = el("label", title),
        control = el(tag);
      label.append(control);
      row.append(label);
      return control;
    };
    const key = input("Field key");
    key.value = item.key;
    key.pattern = "[A-Za-z_][A-Za-z0-9_]*";
    key.required = true;
    key.oninput = () => {
      item.key = key.value;
      save();
    };
    const label = input("Display label");
    label.value = item.label;
    label.required = true;
    label.oninput = () => {
      item.label = label.value;
      legend.textContent = label.value;
      save();
    };
    const type = input("Field type", "select");
    for (const kind of ["text", "number", "boolean", "preset"]) {
      const option = el("option", kind);
      option.value = kind;
      type.append(option);
    }
    type.value = item.type;
    for (const control of [key, label, type])
      definition.append(control.parentElement);
    type.onchange = () => {
      item.type = type.value;
      item.value =
        type.value === "boolean" ? false : type.value === "number" ? 0 : "";
      save();
      renderScriptFields(form);
    };
    const value = input("Value", item.type === "preset" ? "select" : "input");
    if (item.type === "preset") {
      const none = el("option", "Select a preset (optional)");
      none.value = "";
      value.append(none);
      const presets = (state?.presets || []).filter((p) =>
        ["osc", "midi"].includes(p.action || "osc"),
      );
      for (const preset of presets) {
        const option = el(
          "option",
          `${preset.name} · ${(preset.action || "osc").toUpperCase()}`,
        );
        option.value = preset.id;
        value.append(option);
      }
      if (item.value && !presets.some((p) => p.id === item.value)) {
        const missing = el("option", "Missing preset — choose another");
        missing.value = item.value;
        value.append(missing);
      }
      value.value = item.value;
    } else if (item.type === "boolean") {
      value.type = "checkbox";
      value.checked = item.value;
    } else {
      value.type = item.type === "number" ? "number" : "text";
      if (item.type === "number") value.step = "any";
      value.value = item.value;
    }
    value.oninput = () => {
      item.value =
        item.type === "boolean"
          ? value.checked
          : item.type === "number"
            ? Number(value.value)
            : value.value;
      save();
    };
    const remove = el("button", "Remove field");
    remove.type = "button";
    remove.onclick = () => {
      fields.splice(index, 1);
      save();
      renderScriptFields(form);
    };
    definition.append(remove);
    container.append(row);
  }
}
function initializeActionForms() {
  for (const form of [$("#form"), $("#preset-form"), $("#step-form")]) {
    const label = el("label", "Action type"),
      select = el("select");
    select.name = "action";
    for (const [value, title] of [
      ["osc", "OSC message"],
      ["midi", "MIDI output"],
      ["script", "Custom script"],
      ...(form.id === "step-form"
        ? []
        : [
            ["sequence", "Action sequence"],
            ["fade", "DMX fade → OSC"],
          ]),
    ]) {
      const option = el("option", title);
      option.value = value;
      select.append(option);
    }
    label.append(select);
    form.querySelector(".osc-fields").before(label);
    const midi = el("div");
    midi.dataset.action = "midi";
    const script = el("div");
    script.dataset.action = "script";
    function field(parent, name, title, initial, options) {
      const label = el("label", title),
        input = el(
          options ? "select" : name === "script_args" ? "textarea" : "input",
        );
      input.name = name;
      if (options)
        for (const [value, text] of options) {
          const option = el("option", text);
          option.value = value;
          input.append(option);
        }
      input.value = initial;
      label.append(input);
      parent.append(label);
      return input;
    }
    const port = field(midi, "midi_port", "MIDI output port", "", []);
    populateMidiPorts(port);
    port.required = true;
    const refresh = el("button", "Refresh MIDI outputs", "small refresh-midi");
    refresh.type = "button";
    refresh.onclick = refreshMidiPorts;
    midi.append(refresh);
    const portStatus = el("p", "", "preset-note midi-port-status");
    portStatus.setAttribute("role", "status");
    midi.append(portStatus);
    field(midi, "midi_type", "Message", "control_change", [
      ["control_change", "Control change"],
      ["note_on", "Note on"],
      ["note_off", "Note off"],
      ["program_change", "Program change"],
      ["start", "Start"],
      ["stop", "Stop"],
      ["continue", "Continue"],
    ]);
    for (const [name, title, value, min, max] of [
      ["midi_channel", "MIDI channel (1–16)", 1, 1, 16],
      ["midi_number", "Note / controller / program (0–127)", 1, 0, 127],
      ["midi_value", "Velocity / value (0–127)", 127, 0, 127],
    ]) {
      const input = field(midi, name, title, value);
      input.type = "number";
      input.min = min;
      input.max = max;
    }
    midi.append(
      el(
        "p",
        "Select an available MIDI output above. Note on stays on until a separate Note off action is sent. Start, Stop and Continue ignore channel, number and value; Program change ignores value.",
        "preset-note",
      ),
    );
    const template = el("button", "Use preshow gate template");
    template.type = "button";
    template.onclick = async () => {
      try {
        fillAction(form, await api("/api/scripts/preshow-template"));
      } catch (error) {
        notice(error.message);
      }
    };
    script.append(template);
    field(script, "script_path", "Absolute executable path", "").placeholder =
      "/usr/bin/python3";
    field(
      script,
      "script_args",
      "Arguments (JSON array of strings)",
      "[]",
    ).placeholder = '["/Users/you/scripts/cue.py", "go"]';
    field(script, "script_cwd", "Working directory (optional, absolute)", "");
    const timeout = field(
      script,
      "script_timeout",
      "Timeout (seconds, 1–300)",
      30,
    );
    timeout.type = "number";
    timeout.min = 1;
    timeout.max = 300;
    field(script, "script_output", "Script result", "ignore", [
      ["ignore", "Ignore output"],
      ["preset_actions", "JSON preset actions (OSC / MIDI)"],
    ]);
    const fields = field(script, "script_fields", "", "[]");
    fields.type = "hidden";
    fields.parentElement.hidden = true;
    const custom = el("div");
    custom.className = "script-custom-fields";
    script.append(custom);
    const add = el("button", "＋ Script field");
    add.type = "button";
    add.onclick = () => {
      const values = JSON.parse(fields.value);
      let n = values.length + 1;
      while (values.some((f) => f.key === `field_${n}`)) n++;
      values.push({
        key: `field_${n}`,
        label: "New field",
        type: "text",
        value: "",
      });
      fields.value = JSON.stringify(values);
      renderScriptFields(form);
    };
    script.append(add);
    script.append(
      el(
        "p",
        "Fields are passed to the script as SIGNAL_SCRIPT_FIELDS JSON. Preset fields select reusable OSC or MIDI outputs. The preshow template sends the selected presets only when preshow is false.",
        "preset-note",
      ),
    );
    script.append(
      el(
        "p",
        "For Python or shell scripts, use the interpreter as the executable and the script path as the first argument. Scripts must be allowed in Settings. Script result controls whether stdout requests preset outputs; completion and exit codes appear in Activity.",
        "preset-note",
      ),
    );
    form.querySelector(".osc-fields").after(midi, script);
    if (form.id !== "step-form") {
      createSequencePanel(form);
      createFadePanel(form);
    }
    select.onchange = () => configureAction(form);
    configureAction(form);
  }
}
function configureAction(form) {
  const action = form.elements.action.value;
  if (action === "midi") refreshMidiPorts();
  for (const kind of ["osc", "midi", "script", "sequence", "fade"]) {
    const panel =
      kind === "osc"
        ? form.querySelector(".osc-fields")
        : form.querySelector(`[data-action="${kind}"]`);
    if (!panel) continue;
    const visible = kind === action || (kind === "osc" && action === "fade");
    panel.hidden = !visible;
    if (kind === "sequence")
      panel.querySelector("button").textContent = "Edit sequence & timeline";
    if (kind === "fade")
      panel.querySelector("button").textContent = "Edit fade & keyframes";
    for (const field of panel.querySelectorAll("input,select,textarea,button"))
      field.disabled = !visible;
  }
  if (form.id === "form") {
    for (const selector of [".argument-heading", "#arguments"]) {
      const panel = form.querySelector(selector);
      panel.hidden = action === "fade";
      for (const field of panel.querySelectorAll("input,select,button"))
        field.disabled = action !== "osc";
    }
    updateFollowerFields();
  }
  if (action === "osc")
    for (const row of form.querySelectorAll(".arg")) row.updateArgument();
  if (form.id === "preset-form")
    form.elements.address.required = action === "fade";
}
function updateFollowerFields() {
  const form = $("#form"),
    follower =
      form.elements.action.value === "fade" ||
      (form.elements.action.value === "sequence" &&
        getSequenceData(form).sequence_source === "dmx");
  for (const name of [
    "operator",
    "threshold",
    "threshold_max",
    "cooldown_ms",
  ]) {
    const field = form.elements[name];
    field.closest("label").hidden =
      follower ||
      (name === "threshold_max" && form.elements.operator.value !== "range");
    field.disabled = follower;
  }
  form.querySelector(".form-note").hidden = follower;
  $("#trigger-advanced").hidden = follower;
  form.elements.cooldown_ms.closest("label").nextElementSibling.hidden =
    follower;
}
function fillAction(form, values) {
  form.elements.action.value = values.action || "osc";
  const defaults = {
    host: "127.0.0.1",
    port: 8000,
    address: form.id === "preset-form" ? "" : "/go",
    midi_port: "",
    midi_type: "control_change",
    midi_channel: 1,
    midi_number: 1,
    midi_value: 127,
    script_path: "",
    script_args: [],
    script_cwd: "",
    script_timeout: 30,
    script_fields: [],
    script_output: "ignore",
  };
  for (const [key, fallback] of Object.entries(defaults)) {
    const value = values[key] ?? fallback;
    if (key === "midi_port") populateMidiPorts(form.elements[key], value);
    form.elements[key].value = ["script_args", "script_fields"].includes(key)
      ? JSON.stringify(value)
      : value;
  }
  if (form.id !== "step-form") {
    setSequenceData(form, values);
    setFadeData(form, values);
  }
  renderScriptFields(form);
  configureAction(form);
}
function readAction(form) {
  const action = form.elements.action.value,
    data = { action };
  if (action === "sequence") return { action, ...getSequenceData(form) };
  if (action === "fade")
    return {
      action,
      host: form.elements.host.value.trim(),
      port: Number(form.elements.port.value),
      address: form.elements.address.value.trim(),
      ...getFadeData(form),
    };
  for (const key of actionFields[action]) {
    const field = form.elements[key];
    if (["script_args", "script_fields"].includes(key)) {
      try {
        data[key] = JSON.parse(field.value);
      } catch {
        throw Error(
          'Script arguments must be JSON, for example ["/path/to/script.py", "go"].',
        );
      }
    } else
      data[key] =
        field.type === "number" ? Number(field.value) : field.value.trim();
  }
  return data;
}
function applyPreset() {
  const form = $("#form"),
    preset = state?.presets.find((p) => p.id === $("#device-preset").value);
  form.elements.action.disabled = false;
  configureAction(form);
  if (!preset) return;
  const address = form.elements.address.value;
  fillAction(form, preset);
  if ((preset.action || "osc") === "osc" && !preset.address)
    form.elements.address.value = address;
  form.elements.action.disabled = true;
  if (preset.action === "fade") {
    for (const name of ["host", "port", "address"])
      form.elements[name].disabled = true;
    const button = form.querySelector("[data-action=fade] button");
    button.disabled = false;
    button.textContent = "View fade & keyframes";
    return;
  }
  if (preset.action === "sequence") {
    const button = form.querySelector("[data-action=sequence] button");
    button.disabled = false;
    button.textContent = "View sequence & timeline";
    return;
  }
  if (preset.action === "script")
    for (const field of form.querySelectorAll(
      "[data-action=script] input,[data-action=script] select,[data-action=script] button",
    ))
      field.disabled = true;
  for (const key of actionFields[preset.action || "osc"])
    if (key !== "address" || preset.address) form.elements[key].disabled = true;
}
function actionLabel(rule) {
  if (rule.action === "fade")
    return `DMX fade · ${rule.fade_keyframes.length} keyframes · ${rule.host}:${rule.port} ${rule.address}${rule.live && rule.level != null ? " → [" + sampleFade(rule, rule.level).map(fadeValueText).join(", ") + "]" : ""}`;
  if (rule.action === "sequence")
    return `${rule.sequence_source === "dmx" ? "DMX cues" : "Sequence"} · ${rule.steps.length} steps · ${formatSequenceTime(rule.steps.reduce((total, step) => total + step.delay_ms, 0))} timeline`;
  if (rule.action === "midi")
    return `MIDI · ${rule.midi_port} · ${rule.midi_type} · CH ${rule.midi_channel} · ${rule.midi_number} / ${rule.midi_value}`;
  if (rule.action === "script")
    return `Script · ${rule.script_path} ${(rule.script_args || []).map((v) => JSON.stringify(v)).join(" ")}`;
  return `OSC · ${rule.host}:${rule.port} ${rule.address || ""}`;
}
