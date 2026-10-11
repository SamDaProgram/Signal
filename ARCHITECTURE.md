# Signal code map

Start Signal as before with `Start.command` or `python3 app.py --port 8080`.
`app.py` is now a small launcher that also preserves the original public imports
used by integrations and tests. The implementation lives in `signal_bridge/`.

## Backend

| File | Responsibility |
| --- | --- |
| `bridge.py` | Shared runtime state, input-level evaluation, status snapshots and shutdown |
| `configuration.py` | Trigger/preset editing, linked presets, group labels, show validation and CSV export |
| `actions.py` | OSC, MIDI and script execution, script result validation and output dispatch |
| `followers.py` | Continuous DMX fades, output throttling and DMX cue crossings |
| `sequences.py` | Timed playback, cancellation, step completion and bounded run history |
| `protocols.py` | Pure Art-Net, sACN and DDP packet decoding and OSC message encoding |
| `validation.py` | Action schemas, input settings, conditions and fade interpolation |
| `shows.py` | Show directory operations, imports, copies and active show switching |
| `storage.py` | Atomic file writes and the shared show size limit |
| `network.py` | Mac IP discovery, multicast subscriptions and UDP receiver loops |
| `http.py` | HTTP endpoints, local access checks and explicit static-file routing |
| `cli.py` | Startup arguments, port binding, privilege drop and receiver lifecycle |
| `constants.py`, `values.py` | Shared limits, project paths and primitive value checks |

`config_lock.py` remains responsible for the editing PIN. `preshow_server.py`
remains the independent preshow controller, and `scripts/preshow_gate.py` is the
script it can dispatch through Signal.

### Runtime coordination

`Bridge` owns the runtime state and one reentrant lock. Configuration, action
outputs, followers and sequence playback are feature mixins on that same object.
This extraction keeps existing method calls, lock boundaries and callback
behavior intact while giving each feature a focused source file.

When changing behavior:

- Keep shared configuration changes and output decisions under `bridge.lock`.
- Keep packet parsing and action validation free of network or filesystem writes.
- Keep the existing cancellation, pause and show-switch checks when adding outputs.
- Store show data through the atomic writer; preserve the version 1 JSON schema.

The mixins deliberately share state. Moving them into objects with independent
state or locks would require a separate concurrency redesign and regression work.

## Browser interface

| File | Responsibility |
| --- | --- |
| `core.js` | DOM helpers, page navigation, shared view state and API requests |
| `actions.js` | OSC arguments, MIDI port discovery, script fields and action editors |
| `triggers.js` | Trigger/preset editors, channel inspection, grouped trigger list and filters |
| `shows.js` | Show file dialog, output pause and session controls |
| `dashboard.js` | Polling, input selection, live levels and monitor filtering |
| `sequences.js`, `faders.js` | Sequence and fade builders and live previews |
| `config-controls.js` | Editing PIN and navigation to preshow |
| `app.js` | Initialize the editors and begin polling |

The page loads deferred scripts in a fixed order. These are classic scripts with
shared bindings, so feature functions can cooperate without a build step. Shared
helpers load first, editor definitions follow, and `app.js` boots last. If adding
an asset, register its route in `signal_bridge/http.py` and its script in
`static/index.html`; the HTTP asset test catches missing routes.

## UI workflow

The dashboard puts triggers before the full DMX monitor. Search matches words
across trigger names, group labels, channel assignments and destinations. Status
and action filters combine with search. Filtering only changes what is visible;
it never changes saved trigger order or overlap priority. Clearing filters
restores each group's previous expanded state.

The channel monitor and connection details open on demand. Select **Assigned
channels only** to inspect the channels used in the current universe/block.
Trigger editing is divided into input and action sections; cooldown and matching
explanations live under **Advanced**. Existing nonzero cooldowns open that section
automatically. Narrow screens display stacked trigger rows with labeled values.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --test tests/*.cjs
```

Tests include loopback HTTP/UDP delivery, protocol decoding, persistence, scripts,
MIDI mapping, fade behavior, sequence timing/cancellation, PIN enforcement,
frontend asset serving and trigger filtering. MIDI mocks patch
`signal_bridge.actions.midi_module`, where execution now resolves the dependency.
Physical controller reception and connected device playback still need the actual
show hardware.
