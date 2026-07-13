# Antibot CV Chrome Injector

This unpacked Chrome extension lets the Python controller send safe local commands into the game page without AppleScript JavaScript permissions.
The extension background worker talks to `127.0.0.1:17654` and forwards commands into the game page.

Install once:

1. Open `chrome://extensions/`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this repository's `browser_injector/` folder.
5. Open or refresh `https://3kingdoms.ru/main.php`.

After changing files in this folder, click reload on the extension card at `chrome://extensions/`, then refresh the game tab.

## Development

Do not edit `page_bridge.js` directly. It is the generated Chrome runtime
bundle. Edit the bounded domain sources in `page_bridge_modules/`, then rebuild:

```bash
python scripts/build_page_bridge.py
node --check browser_injector/page_bridge.js
```

The architecture test fails when the generated bundle is stale or an authored
runtime module exceeds 1500 lines.

Browser control panel:

1. Start the local control server:

```bash
cd /path/to/troecarstvie-antibot-cv
source .venv/bin/activate
python -m src.antibot_cv.automation.controller control-server --config config/automation.local.json --live
```

On Windows after running `setup_windows.cmd`, start the server with:

```bat
botcv
```

2. Click the Antibot CV extension icon.
3. Set cycles, mob levels, HP/prowess thresholds, and recovery settings.
4. Press `Старт` or `Стоп` in the popup.

Check connection while Chrome is on a `3kingdoms.ru` page:

```bash
python -m src.antibot_cv.automation.controller injector-status --timeout 10
```

Expected result has `"ok": true`. During live automation the controller starts the same local bridge automatically.
