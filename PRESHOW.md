# Preshow control page and API

Double-click **Start Preshow.command**, or run `python3 preshow_server.py` in this folder. Requires Python 3.10+ with no extra packages.

Open **http://localhost:8081/** in your browser for the show control home page. The current status stays at the top, and one large button fills the remaining screen. The button offers the next action based on the current state:

- **Start preshow** runs the active Signal show’s **Start Preshow** cue, then sets `preshow=true` and displays **In preshow**. In Halloween 2026 this is one timed sequence on universe 0, channel 501, level ≥100: set `/preshow` to integer 1 on the local controller, wait 300 ms, send FPP `/preshow/reg` integer 0, and send Note On to QLab, channel 1, note 2, velocity 127. The former channel-512 duplicate has been removed.
- **Start show** sets `preshow=false` and displays **Running show**.

The page reads the API every second and follows changes from OSC or another browser. Keep Signal running with Halloween 2026 loaded and outputs enabled. The preshow button uses the saved cue settings. A timed sequence is acknowledged when queued; see Signal Activity for step completion or output errors. If the cue cannot start, the page shows an error. Start show changes status only. OSC updates and direct POST /api/status requests remain status-only, avoiding playback feedback loops. Repeated preshow starts while already in preshow do not resend the MIDI cue. Successful MIDI delivery means the message was handed to the output port; QLab does not acknowledge cue playback.

**GET http://localhost:8081/api/status** returns JSON:

```json
{"preshow": false}
```

Send OSC to this computer's IP, **UDP port 9000**, address **`/preshow`**:

| Argument | API value |
| --- | --- |
| Integer `1` | `true` |
| Integer `0` | `false` |

Native OSC boolean tags `T`/`F`, floats `1.0`/`0.0`, and strings `"true"`/`"false"` or `"1"`/`"0"` also work. Send exactly one argument. Invalid values and unrelated addresses are ignored. Immediate bundles (timetag 1) are supported; scheduled bundles and OSC address wildcards are not. Packet encoding follows the [OSC specification](https://opensoundcontrol.stanford.edu/spec-1_0.html).

In Signal, configure an OSC action with host `127.0.0.1`, port `9000`, address `/preshow`, and one integer argument, `1` or `0`.

From another device on the same network, read `http://<this-computer-IP>:8081/`. Both listeners accept connections from the local network without authentication. For access only from this computer, use `python3 preshow_server.py --host 127.0.0.1`.

The value starts at `false` each time you launch the server and stays in memory until the next OSC update or restart. Responses disable caching. The home page writes the same state using **POST /api/status** with a JSON body such as `{"preshow": true}`. Existing API clients can still read `/` (send `Accept: application/json`); browser requests receive the home page.

For a different Signal web port, add `--bridge-url http://127.0.0.1:8080`. To use another saved MIDI or timed sequence trigger name, add `--preshow-trigger "My Preshow"`. The controller sends `POST /api/start-preshow` to initiate preshow.

Change ports with `python3 preshow_server.py --port 8082 --osc-port 9001`. Keep the Terminal window open while running; press Control+C to stop.

Run its checks with `python3 -m unittest discover -s tests -p test_preshow.py -v`.

## DMX → preshow check → FPP

The **Check Preshow** trigger is FPP-only. It never sends to QLab, even if an older show still has a QLab field. The controller's separate **Start preshow** button and your other MIDI triggers are independent.

1. In **Settings → Action presets**, configure **FPP** with its OSC destination. Halloween 2026 uses `192.168.3.30:9000`, matching its FPP Preshow sequence.
2. Add or edit a trigger, choose **Custom script → Use preshow gate template**, or select the saved **Preshow gate** preset. Halloween's **Check Preshow** uses universe 0, channel 511, level ≥100.
3. Set the **Preshow status URL** (default `http://127.0.0.1:8081/api/status`) and select the **FPP preset**.
4. The FPP fields default to `/stoppre`, followed by `/preshow/fade`, both with no arguments.
5. Save and enable **Allow custom scripts for this session** in Settings.

When DMX selects the trigger, it reads the status once. Boolean `true` sends nothing. Boolean `false` sends `/stoppre`, then `/preshow/fade` to FPP, both with no arguments. Invalid or unavailable status sends nothing. The HTTP socket timeout is three seconds and the overall script timeout is ten seconds. Both requests are validated before dispatch. Paused outputs, disabled scripts, and canceled containing sequences suppress outputs.

Holding DMX at the same value does not repeat the check. Move below the condition and back in to check again; **Test** checks manually. Presets are captured at script start, so subsequent runs use the latest saved destination.

After updating Signal's Python code, stop and restart **Start.command**, then re-enable scripts. Refreshing the browser alone does not reload the bridge. An Activity error **Invalid script preset action** after adding OSC arguments indicates that an older bridge process may still be running.
