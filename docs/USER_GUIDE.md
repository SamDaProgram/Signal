# Signal user guide

[← Project home](../README.md) · [Preshow guide](../PRESHOW.md) · [Code map](../ARCHITECTURE.md)

A local web dashboard and native UDP bridge. Python 3.10 or later. OSC and script actions use the standard library; MIDI uses the optional packages described below. No internet connection is needed at runtime.

## Start

Double-click **Start.command** in Finder, then open **http://localhost**. Keep its Terminal window open while the bridge runs; press Control+C to stop it.

The web interface defaults to **TCP port 80**, accessible only from this Mac. If macOS requires administrator permission for port 80, the launcher asks for your Mac password in Terminal. It opens the ports and immediately drops administrator privileges before handling web requests or writing settings. Python must be installed; use https://www.python.org/downloads/macos/ if necessary.

From Terminal:

```sh
cd "/path/to/your/Signal-folder"
./Start.command
```

For an alternative port without administrator permission:

```sh
python3 app.py --port 8080
```

Then visit http://localhost:8080. The app listens on UDP ports 6454 (Art-Net), 5568 (E1.31), and 4048 (DDP). Only the selected protocol drives triggers. If another process owns a port, its receiver error appears in the dashboard; the other protocols remain available. Stop any preview instance before starting the port 80 launcher. If macOS asks to allow incoming network connections or local network access for Python/Terminal, allow it for incoming lighting data.

## Dashboard and code organization

Triggers appear first on the dashboard. Use **Find a trigger** to search by name,
channel, group label, or OSC destination, and combine it with **Status** and
**Action** filters. **Clear filters** restores the full list. Use **Expand groups**
or **Collapse groups** to control how much detail is visible.

Open **Channel monitor & connection** to find this Mac’s receiving address or
inspect live levels. **Assigned channels only** hides unused channels in the
selected universe/block. In the trigger editor, choose the input channel and
then the action. Cooldown and detailed matching rules are under **Advanced**;
an existing nonzero cooldown opens that section automatically.

The backend now lives in focused modules under `signal_bridge/`, and the browser
code is divided by feature. Launch commands and show file formats stay compatible.
See [ARCHITECTURE.md](../ARCHITECTURE.md) for the file map and maintenance notes.

## Connect and configure

1. Put the Mac and lighting controller on the same network. Open **Settings** in the header and choose **Input protocol**: **Art-Net**, **E1.31 / sACN**, or **DDP**. The choice is saved and takes effect without restarting.
2. Set the universe (or DDP block) in the channel monitor. See the addressing table below. Click **Add trigger**, or click a channel.
3. Set the trigger's universe/block, channel (1–512), and activation level (default **100**, range 0–255).
4. Enter the receiving application's IPv4 address, UDP listening port, OSC address such as `/go`, and optional typed arguments. Use `127.0.0.1` for an application on the same Mac. Triggers and sequence steps both provide an **＋ Argument** button with type dropdowns and value fields; no JSON entry is needed. Arguments support integer, float, text, true, and false; select Float explicitly if the receiver expects `1.0` rather than integer `1`.
5. Save. Use **Test** to send that trigger manually, even while its individual Enable checkbox is off. Global output pause and the script permission still apply. Use **Edit** to change or disable it, and **Remove** to delete it.

### Protocol addressing

| Input        | UDP port | Addressing and setup                                                                                                                         |
| ------------ | -------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Art-Net      | 6454     | Universe 0–32767, channels 1–512. Send unicast to this Mac or broadcast. Net/Sub-Net/Universe maps to `Net × 256 + Sub-Net × 16 + Universe`. |
| E1.31 / sACN | 5568     | Universe **1–63999**, channels 1–512. Accepts unicast and joins multicast for enabled trigger universes plus the monitored universe.         |
| DDP          | 4048     | Output ID **1** (or all devices, 255), byte-oriented 8-bit channel data, displayed in blocks **0–255** of 512 channels each.                 |

For sACN multicast, enter the Mac's network IPv4 in **Mac network IPv4 for multicast**, then click **Save interface**. `0.0.0.0` lets macOS choose its default interface. Membership changes are automatic when you edit triggers or select another monitored universe. An OS membership limit or unavailable interface is reported in the dashboard; unicast still works.

DDP offsets are zero-based bytes. DDP byte offset `N` maps to **block `N // 512`, channel `(N % 512) + 1`**. For example, absolute channel 513 (offset 512) is block 1, channel 1. RGB components are individual channels, so pixel 1's R/G/B are channels 1/2/3. The app supports the first 131,072 channels. Multi-packet frames are staged until the **Push** flag arrives; partial packets preserve other values, and only updated channels are evaluated. Unreceived channels display a dash.

Your trigger list is shared across protocols, and switching **does not renumber or delete rules**. Adjust universe 0 to your actual sACN universe when switching from Art-Net. Rules outside the selected protocol's address range show **Edit universe / block**. Switching protocols clears old input levels and re-arms triggers; the first new high sample may fire.

Existing Art-Net settings load automatically with no conversion step. For an Art-Net interface bind, the existing `--artnet-bind 192.168.1.20` option remains available.

### Linked action presets

Open **Settings → Action presets → Add preset**. Choose OSC, MIDI, Script, Action sequence, or DMX fade and enter the configuration. You can store up to 128 presets per show.

Selecting a preset in the trigger editor creates a persistent link. The action type and fields supplied by the preset are **locked** and update in every linked trigger when the preset changes. For OSC, IP and port are always linked; a nonblank default address is linked too. A blank default keeps each trigger's address editable. MIDI and script presets link all of their action fields. Name, input assignment, condition, cooldown, enabled state, and OSC arguments belong to each trigger.

Choose **Custom action** to detach and edit the current values. Removing a preset detaches its triggers while preserving their last values. Existing triggers from older versions remain custom until you select a preset and save; they are not linked by matching their IP address.

### MIDI actions

From the project directory, install optional MIDI support once:

```sh
python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements-midi.txt
```

**Start.command** automatically uses this local environment when present. For a custom web port, use `./Start.command --port 8080` or `.venv/bin/python3 app.py --port 8080`.

In a trigger, preset, or sequence step, select **MIDI output** and choose a port from the automatically populated dropdown. Each editor includes **Refresh MIDI outputs**; the same refresh is available in Settings. Saved ports remain selected even when disconnected and are marked unavailable. Connect your MIDI device or enable an IAC bus in macOS Audio MIDI Setup for communication between applications. Refresh after changing devices. A missing package, backend, or disconnected port produces an actionable status/error without disabling OSC or scripts.

Supported messages: Note on, Note off, Control change, Program change, Start, Stop, and Continue. Channels in the UI are 1–16; note/controller/program numbers and velocity/value are 0–127. Program change ignores value. Start/Stop/Continue ignore all three numeric fields. **Note on does not automatically send Note off**: use a separate Note off rule when needed. This is MIDI output driven by lighting channels, not MIDI input or MIDI-file playback. The implementation follows [Mido's message and port APIs](https://mido.readthedocs.io/en/latest/api.html).

### Custom script actions

Select **Custom script**, enter an absolute executable path, an argument array, an optional absolute working directory, and a timeout from 1–300 seconds. For example, use `/bin/sh` with arguments `["/Users/you/scripts/cue.sh", "go"]`, or your Python interpreter with `["/Users/you/scripts/cue.py"]`. A directly executable script needs executable permissions and an appropriate shebang. Script files themselves are not embedded in show downloads: transfer them separately and adjust paths on another Mac.

Enable **Allow custom scripts for this session** in Settings. This permission is off after restarting or loading another show. Scripts run as your normal user with no implicit shell expansion; arguments are passed literally. They run asynchronously, with at most four concurrent scripts and no backlog. Activity records start, completion, nonzero exit codes, missing executables, and timeouts. Standard output/error are discarded. Timeout and app shutdown kill the launched process group; scripts should remain in that group and should not daemonize. Pausing outputs prevents new actions; already running scripts finish. Show switching waits for running scripts to finish.

### DMX faders and OSC keyframes

Choose **DMX fade → OSC** as the action on a trigger. Select the input universe/block and channel, then enter the receiving application's OSC IPv4 address, UDP port, and OSC address. **Edit fade & keyframes** maps that channel's DMX level (0–255) to numeric OSC arguments. This is a continuous mapping: it does not use the trigger's threshold condition or cooldown. Multiple fades and a threshold trigger can share a channel; fades do not compete for the threshold trigger selection.

Start with **Brightness 0–1**, **RGB color sweep**, or **Bipolar −1 to 1**. Templates replace the draft curve. Each keyframe has a DMX level and values. Click inside the graph to add a point at that level and value, or click the bottom ruler to insert the current interpolated values. For RGB, choose **Click edits** to select R, G, or B; clicking a colored curve selects that component. Other components use their interpolated values. Clicking an occupied level selects the existing keyframe. Drag graph points or enter exact numbers to edit them. Left/Right nudges a point's DMX level, Up/Down nudges its value, and Shift gives finer value nudging. Move **Preview DMX** and choose **Add keyframe at preview level** to insert a point with the current interpolated values. Use **Use fade**, then **Save trigger** (or **Save preset**) to keep the changes. Closing without applying discards the draft.

- **Linear** blends directly between keyframes. **Smooth** eases into and out of each keyframe. **Hold / stepped** keeps the previous value until the next keyframe. These describe blending across DMX levels, not an additional fade over time.
- Brightness uses one OSC argument; RGB sends three numeric arguments in R, G, B order. The destination must accept that address and argument layout. The color preview uses the selected output range. **Output range** converts all existing keyframe values between 0–1, 0–100, 0–255, −1 to 1, or a custom minimum/maximum while keeping their positions and relative shape. Choose **Custom** and **Apply range** for other limits. OSC type is independent of range; converting integer output to a normalized or fractional range switches it to Float to preserve useful precision. Integer conversions round values. Keyframe values must remain within the chosen range.
- **Input level display** switches the ruler and position fields between DMX 0–255, normalized 0–1, and 0–100%. This changes display units only: incoming DMX still has 256 positions, and entered positions snap to the nearest DMX level. Range and display settings are saved with the fade and its presets.
- **OSC message preview** shows the UDP destination, OSC address, type tags, and arguments at the selected or live DMX level. For example, `/brightness ,i 128` previews one integer argument. Float values reflect OSC float32 encoding. Expand **Typed JSON** for structured arguments or use **Copy message**. This is a readable OSC message representation, not a shell command, and previewing never sends it.
- Curves hold their first/last value outside the keyframe range. Levels must be unique and increasing; a fade supports 2–64 keyframes and 1–8 numeric values per frame in imported configurations (the built-in editor templates use one or three).
- **Max updates/s** defaults to 30 and can be set to 1–60. Rapid input changes are coalesced, including the final value even if no more packets arrive. Identical encoded OSC outputs are not resent. This remains an 8-bit input mapping with 256 possible DMX positions.
- **Follow live DMX** shows the current input and corresponding draft values without sending preview outputs. Input preview works while bridge outputs are paused. Saved, enabled fades run when bridge outputs are enabled, independently of the editor. After input goes stale, the receiving application holds its last value; no blackout/reset is sent. Resuming outputs sends the current fade on the next input packet.
- Fade outputs increment the action count; successful continuous updates do not flood Activity. Errors and manual tests appear there. **Test** sends the current live input's mapped OSC values; live input is required. Linked fade presets lock their curve and destination until edited through Settings or detached.

The same mapping works with the bridge's Art-Net, sACN, and DDP inputs. For a controller such as MadMapper, route its DMX output to this bridge and match the universe and channel; configure the OSC destination separately. No application-specific OSC address is assumed.

### Action sequences and visual timeline

Choose **Action sequence** in a trigger or preset, then **Edit sequence & timeline**. Add up to 64 steps using OSC, MIDI, or scripts. Each step has its own name, action settings, and **Delay before this step (ms)**. The first delay is measured from the trigger; subsequent delays are measured from the previous step's scheduled start. For example, delays 0, 500, and 1500 place actions at 0, 0.5, and 2 seconds. The total timeline can be up to 10 minutes. Zero-delay steps dispatch in saved order; this is best-effort scheduling, not a hard real-time clock.

The visual timeline shows each step's scheduled start and the delay leading into it. Click a timeline row or a step name to edit it. Use the up/down buttons to reorder steps and × to remove a step; a moved step retains its delay. **Apply step** updates the timeline. Moving to another step also applies valid edits. **Use sequence** applies the draft to its parent editor; then **Save trigger** or **Save preset** writes it to the show. **Discard changes** leaves the previous sequence unchanged.

**Preview timing** animates a playhead and highlights steps without sending any OSC/MIDI or launching scripts. Drag the ruler or an empty track, or use the **Inspect time** slider, to examine a point in the timeline. Preview starts from that point and supports pause/resume, reset, and 0.25×–2× speed. Drag a numbered marker to change its delay; later steps shift by the same amount while preserving their gaps. Snap offers 1, 10, 100, 500, or 1000 ms increments; hold Shift while dragging for 1 ms precision. Focus a marker and use Left/Right to nudge it, or Home/End to move to its allowed limits. Zoom up to 8× for finer editing and scroll horizontally to reach later times. Use ⧉ to duplicate a step with all its settings. Timing edits preserve step order and the ten-minute limit. A linked sequence preset locks its steps and error policy; **View sequence & timeline** still lets you inspect and preview it. Edit the preset to update linked triggers, or select Custom action to detach.

To drive discrete actions with a fader instead, set **Drive sequence with → DMX fader / cue crossings**. DMX 0–255 maps onto 0–100% of the sequence's scheduled duration; the timeline ruler switches to DMX levels. A nonzero duration is required. The first received level establishes a baseline without firing cues. Forward movement fires every crossed step, including steps skipped over by a large jump. A step at time zero fires when leaving DMX 0. Repeated identical levels do nothing. Reverse movement re-arms forward cues; **Both directions** also fires crossed cues in reverse order. Cue followers run independently of threshold selection and ignore cooldown. After a pause, edit, input change, or stale input, the current level establishes a new baseline rather than replaying old crossings. **Follow live DMX** shows the input position in the visualizer; manual preview remains output-free.

DMX cue crossings appear in **Activity**, rather than creating timed playback runs. **Stop remaining crossed cues** stops the rest of the current crossing batch on an action error; later fader movement can cross new cues. Scripts still run asynchronously, so a later script failure cannot undo cues already dispatched. Scripts require the existing session permission and obey the four-script concurrency limit. Use real DMX movement to test these cues; the dashboard's manual Test does not simulate a full sweep.

When a sequence fires, **Sequence playback** on the dashboard shows live progress, per-step results, and a timeline. It retains the last 32 runs. Up to eight sequences may run at once. Re-triggering the same sequence trigger while it is running is ignored and logged; different triggers can run independently. The trigger's cooldown applies to the start of a run. Editing a trigger or preset affects future runs; a running sequence keeps the configuration it started with.

**Cancel pending steps**, **Pause outputs**, disabling/removing its trigger, loading a different show, or app shutdown cancels future steps. Resuming outputs does not resume canceled sequences. Changing the input value alone does not cancel a sequence. Already running scripts finish normally on cancellation; app shutdown and script timeouts retain their process-group termination behavior.

Scripts launch asynchronously on the timeline, so later steps do **not** wait for script completion. After the last scheduled step, the sequence waits for launched scripts to report their results. Choose **Stop remaining steps** or **Continue remaining steps** on error. Script failures stop future steps as soon as the failure is reported when Stop is selected; already sent actions cannot be undone. Script permissions, concurrency limits, and timeouts still apply. If you need one script to wait on another process, implement that dependency in the script itself.

Sequence presets and full step configurations travel in show files and CSV exports. Steps contain their own action configurations; nested sequences and per-step preset links are not supported. The completed-action counter counts successful individual actions, without adding an extra count for the sequence itself.

### Show files and backups

The header shows the active filename. Click it to open **Show files** for saving, downloading, opening, uploading, and creating shows. Expand **Show directory** there to see the full path. By default this is `shows/` beside the app. The app creates the directory and `default.json` if missing, and restores the last selected filename on restart. If that selected file is missing, it creates a blank show with that name. Invalid existing files are reported rather than silently overwritten.

On first launch, an existing `settings.json` is imported into the default show without changing the original. `--config /path/to/settings.json` chooses a legacy source for that first import; after initialization, select files through the header’s Show files button. Use `--show-dir /path/to/shows` to choose a different directory.

- **Save** writes the active show; trigger, preset, group-label, and input edits also save automatically using atomic file replacement.
- **Save a copy** uses the New filename field to create and open a separate copy, preserving the current output state.
- **New blank show** creates and opens an empty show with default Art-Net settings.
- **Download** saves the complete current show as a JSON file through your browser.
- **Upload show** validates a JSON file, saves it into the directory, and opens it. The upload’s filename is used; rename the file before uploading if that name already exists.
- **Saved shows → Open selected** opens a JSON file already in the show directory. The list refreshes automatically.

New, loaded, and uploaded shows open with outputs paused and scripts disabled. Click **Enable outputs** when ready. Existing shows are never overwritten by uploads, copies, or new-show creation: choose another filename if it already exists. Names use letters, numbers, spaces, dots, underscores, or hyphens and end in `.json`; nested paths and symlinks are not accepted. Maximum file size is 8 MB. Invalid uploads leave the current show unchanged.

A show includes input protocol/interface/monitored universe, all triggers and action configurations, preset links and definitions, cooldowns, and group labels. Activity, live levels, counters, detected IP addresses, output pause, and script permission are session state. Back up the show directory or download individual shows.

### Output pause, cooldowns, and network destination

**Pause outputs** stops automatic and manual actions while input monitoring continues. **Enable outputs** baselines the current live values so they do not fire immediately; the next selection change fires normally. Runtime pause is session-only; a fresh app start enables OSC/MIDI outputs, while script permission remains off.

Each trigger has an optional **Cooldown (milliseconds)**. Zero disables it. If that trigger is selected again within its cooldown, the transition is skipped and logged; it is not queued or replayed later. Manual **Test** bypasses cooldown, but respects output pause and script permission.

**Send data here** inside **Channel monitor & connection** lists this Mac's detected IPv4 addresses and the selected protocol's UDP port. Use a reachable LAN address on your controller; `127.0.0.1` only works for senders on this Mac. **Refresh IPs** updates the list after network changes. sACN also displays the monitored universe's multicast destination. Receiver binding errors still appear in the input status.

### Multiple triggers on one channel

Channels with several triggers appear as a collapsed dropdown. Click the channel header to expand its list; the dropdown stays open while levels refresh. Each header shows the channel level, trigger count, and selected trigger. Single-trigger channels start expanded.

Click **Add on channel** beside a trigger to create another message on the same input assignment. Give it a new name and its own OSC settings and condition. Use **Edit** or **Remove** inside the expanded list to manage individual triggers.

Conditions include **≥, >, ≤, <, =**, and **Between (inclusive)**. For example, put one message on channel 1 at **100–149** and another at **150–199**. Level 125 selects the first; 175 selects the second; 200 selects neither.

**Only one automatic trigger is selected per channel.** If conditions overlap, the narrowest matching interval wins; equal-width ties use the first saved trigger. For existing `≥ 100` and `≥ 200` triggers, a jump straight to 220 sends only the `≥ 200` trigger. An exact `= 220` condition wins over both. Disabled triggers are excluded. The table labels a matching but unselected rule **Overlap · not selected**.

A message sends when the selected trigger changes. Holding a value, or moving within the same selected range, does not resend. Returning to a previously selected trigger sends it again. Moving through several ranges during a fade can still send each in sequence; use direct level changes when selecting a cue. Manual **Test** sends the chosen action regardless of selection, subject to global output pause and script permission.

Click **Clear log** in Activity to remove the session's log entries. It does not reset message counters, change settings, or re-arm triggers. Newly sent messages appear normally afterward.

Changes are saved atomically in the active show file. Use Download or back up the show directory to preserve the complete configuration.

## Trigger behavior

- Each accepted input update selects at most one matching enabled trigger per channel, using the interval rule above. Different channels may each send a message in the same input frame.
- On a fresh app start, the first matching sample fires. Adding, editing, deleting, or enabling a trigger uses the current live level to recompute the affected channel's selection without sending. A subsequent change of selection sends normally.
- Existing saved triggers default to `≥` with their original threshold. Settings are read without modifying the original file; the new fields are included on the next save.
- Data becomes stale after 3 seconds. The monitor dims and keeps last-known values. Signal loss does not re-arm triggers or fabricate zero values; channels absent from short packets are not evaluated.
- OSC uses UDP: “Sent” means the operating system accepted the datagram, not that the receiver acknowledged it. Send failures appear in Activity; failed automatic sends are not retried until selection changes away and back.

## Scope and verification

Art-Net consumes ArtDmx without ArtPoll advertisement or ArtSync. sACN supports ordinary, zero-start-code DMX data; preview, terminated streams, nonzero start codes, and packets requesting synchronization are ignored. This is a trigger bridge, not a full lighting-console receiver: it does not merge controller sources, arbitrate sACN priorities, or reorder sequenced frames. Use one sender per universe; the last accepted packet wins.

DDP supports v1 output ID 1/all, undefined raw bytes or explicitly 8-bit RGB/HSL/RGBW/grayscale. Queries, replies, storage, JSON/control messages, DMX-transit ID 254, and nonzero scheduled timecodes are ignored. A zero timecode is treated as immediate. Incomplete DDP staging expires after 3 seconds without a packet and is isolated by sender IPv4 (up to four senders).

The monitor refreshes twice per second; evaluation happens on each Art-Net/sACN frame or committed DDP Push. Up to 256 recently observed universes/blocks and 512 triggers are retained. Activity and counters are session-only; input packet count resets when changing protocol or multicast interface. “Sent” is not a delivery acknowledgement.

The interface is intentionally loopback-only, with same-origin mutation checks. No cloud service or external frontend assets are used.

Run checks:

```sh
python3 -m unittest discover -s tests -v
node --test tests/*.cjs
```

The automated tests cover all three parsers, malformed/control packets, OSC bytes, exclusive trigger selection across all protocols, comparison boundaries, overlapping ranges, log clearing, device preset CRUD and persistence, DDP offsets and Push assembly, stale/partial frames, multicast membership management, saved protocol selection, legacy settings, real HTTP/UDP delivery, show import/export and persistence, invalid-file rejection, linked preset updates, MIDI mapping/reconnect errors, script execution/timeouts, cooldowns, pause behavior, sequence timing over real UDP, cancellation, retriggers, error policies, preset round trips, and bounded run history. Network tests use loopback; verify reception from your physical controller on your network.

Protocol references: [Art-Net specification](https://art-net.org.uk/downloads/art-net.pdf) , [E1.31 packet layout](https://github.com/hhromic/libe131/blob/master/src/e131.h), [DDP protocol specification mirror](https://docs.rs/crate/ddp-rs/1.3.0/source/DDPSPEC.md), and [OSC 1.0 specification](https://opensoundcontrol.stanford.edu/spec-1_0.html).

### Group labels and channel export

Use **Label group** beside a channel group to give it a name, or **Edit label** to change it. Clear the name to remove it. Labels are saved in the active show file, stay with their universe/block and channel when triggers move or are removed, and remain available when switching input protocols.

**Export CSV** downloads `signal-channels.csv` containing all configured triggers across all universes/blocks, including disabled triggers. Each row includes the selected input protocol, universe/block, channel, group label, trigger name, enabled state, condition, OSC destination, typed arguments as JSON, and saved order (for equal-width condition ties). DDP exports also include the absolute channel number. Unassigned channels are omitted. Text that could be interpreted as a spreadsheet formula is prefixed with an apostrophe.

Click a channel in the monitor to see its group label and all connected triggers, including conditions, OSC destinations, arguments, and current status. Use **Edit trigger** or **Add trigger on this channel** from that view. Hover over a monitor channel for a quick list of connected trigger names, conditions, and OSC addresses; disabled triggers are marked.

CSV exports also include the action type, action configuration as JSON, linked preset ID, and cooldown; OSC columns are blank for MIDI and script actions.

### Script GUI fields and conditional preset outputs

Custom scripts support up to 32 GUI fields with unique keys, display labels, and types **text**, **number**, **boolean**, and **preset**. Use **＋ Script field** and **Edit field definition** to configure them. Definitions and values are saved with the show, including linked script presets and sequence steps. A preset field lists OSC and MIDI presets; deleted presets must be reselected before they can be dispatched.

The script receives `SIGNAL_SCRIPT_FIELDS` as a JSON object mapping keys to typed values, plus `SIGNAL_SCRIPT_PRESETS` as a JSON array of selected preset IDs. Existing executable arguments remain literal and unchanged. For example:

```python
import json, os
fields = json.loads(os.environ['SIGNAL_SCRIPT_FIELDS'])
print(fields['message'])
```

The default **Script result → Ignore output** preserves existing script behavior. To request bridge outputs, choose **JSON preset actions (OSC / MIDI)** and print one JSON object to stdout on successful exit:

```python
print(json.dumps({'preset_actions': [
    {'preset_id': fields['fpp'], 'address': '/preshow/reg', 'args': [{'type': 'i', 'value': 1}]},
    {'preset_id': fields['qlab']}
]}))
```

An empty `preset_actions` array requests no output. Requests may only reference presets selected in that script's preset fields. Only OSC/MIDI actions are accepted (no recursive scripts or sequences), up to 32 requests and a 64 KB result. A nonzero exit or invalid result sends nothing. OSC uses the preset's address unless overridden, with optional typed `args` (omitting them sends no arguments); MIDI uses its full saved message. Outputs obey the bridge's output pause, session script permission, and containing sequence cancellation. Failed delivery stops later requests; earlier deliveries are not rolled back. Standard error remains discarded, and Activity reports completion, validation errors, or exit codes.

**Use preshow gate template** configures the bundled `scripts/preshow_gate.py` automatically. See [PRESHOW.md](../PRESHOW.md) for the FPP-only DMX → preshow check setup. The template checks for an explicit JSON boolean `false` before requesting any outputs.

## Screen tabs and configuration PIN

Use the **Preshow** and **Triggers & monitor** tabs to switch screens. Both servers must be running. For custom addresses, use `app.py --preshow-url http://127.0.0.1:8081/` and `preshow_server.py --bridge-url http://127.0.0.1` with the appropriate ports.

Click **Set editing PIN** in Triggers & monitor and choose 4–12 digits. Protection starts after you set the PIN. **Lock editing** locks all editing sessions; **Unlock editing** unlocks the current page. Reloading or closing the page drops its editing access. Sessions expire after eight hours, and restarting the server invalidates all sessions.

The server requires editing access for trigger, preset, group-label, show-file, input-protocol, interface, and script-permission changes. Monitoring, selecting the monitored universe, viewing/exporting data, preshow, manual Test, pause/resume outputs, and canceling running sequences remain available without a PIN. The lock does not stop incoming DMX or running cues.

The salted PIN hash is stored in `.config-lock.json` in the show directory, separately from exported show files. If you forget the PIN, stop Signal, remove that file, restart, and set a new PIN. This is a local editing guard; someone with access to the Mac's files can reset it.

After updating Python code, restart Signal and the preshow server, then refresh the pages. Restarting interrupts running cues, so update between runs.
