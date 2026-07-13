# troecarstvie_antibot_cv

Local macOS CV automation harness for generating controlled game-session
samples for anti-bot detector development.

Current implementation status and the next verified development step are kept
in [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md). Confirmed changes and live
test conclusions are recorded in
[`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md).
The target product, current milestone, and staged exit criteria are defined in
[`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).

This project is intentionally not a stealth automation tool. It does not bypass
anti-cheat, CAPTCHA, game restrictions, browser internals, process memory, or
network traffic. Dry-run mode is the default and records intended actions
without moving the mouse or clicking.

## Install

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Windows Quick Start

From Git CMD in the cloned project folder:

```bat
setup_windows.cmd
```

Then close Git CMD, open a new Git CMD, and start the local control server:

```bat
botcv
```

Keep the `botcv` terminal open while using the Chrome extension. The setup file
creates `.venv`, installs dependencies, updates the Windows bridge branch when
the folder is a git clone, and adds `%USERPROFILE%\bin\botcv.cmd` to the user
PATH.

## CLI

Calibration:

```bash
python -m src.antibot_cv.automation.controller calibrate
```

Dry-run:

```bash
python -m src.antibot_cv.automation.controller run \
  --config config/automation.example.json \
  --preview
```

Live limited run:

```bash
python -m src.antibot_cv.automation.controller run \
  --config config/automation.example.json \
  --live \
  --max-cycles 3
```

Long local run:

```bash
python -m src.antibot_cv.automation.controller run \
  --config config/automation.local.json \
  --live \
  --max-session-minutes 480 \
  --start-delay 3
```

`config/automation.local.json` is tuned for 60 FPS and an 8 hour session. The
`resources` section controls the pre-fight rest gate: if `health_bar_roi` or
`prowess_bar_roi` is detected below the configured threshold, the controller
enters `RESTING` and waits until both recover to `recover_to_percent`.
The `recovery` section controls watchdog timeouts: exhausted viewport searches
restart instead of stopping the session, and stale battle/statistics states
return to location search after their timeout.
`activate_app` can bring Chrome to the front before capture so the harness does
not accidentally inspect or click the Codex window.

Chrome injector check:

```bash
python -m src.antibot_cv.automation.controller injector-status --timeout 10
```

If this prints `"ok": false`, load the unpacked extension from
the repository's `browser_injector/` folder in `chrome://extensions/`, then
refresh `https://3kingdoms.ru/main.php` and rerun the check. The live controller
uses this local bridge to enter the hunt frame without AppleScript JavaScript
access.

Resource ROI check:

```bash
python -m src.antibot_cv.automation.controller inspect-resources \
  --config config/automation.local.json
```

For calibration, save a raw capture and an overlay with configured and detected
resource boxes:

```bash
python -m src.antibot_cv.automation.controller inspect-resources \
  --config config/automation.local.json \
  --output-frame runs/resource-frame.png \
  --output-overlay runs/resource-overlay.png
```

Template validation:

```bash
python -m src.antibot_cv.automation.controller validate-templates \
  --config config/automation.example.json
```

Replay:

```bash
python -m src.antibot_cv.automation.controller replay \
  --input tests/fixtures/session_frames/
```

## macOS permissions

Live mode requires macOS permissions in:

System Settings -> Privacy & Security -> Screen & System Audio Recording

Also grant Accessibility, and Input Monitoring when using the global hotkey
listener. Missing permissions fail closed before live actions.
