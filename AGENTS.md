# Repository Instructions

This repository contains a local, visible, bounded CV automation harness for
generating controlled positive samples for anti-bot detector research.

Hard limits:
- Dry-run is the default.
- Live mode requires an explicit `--live` CLI flag.
- All actions go through the safety guard and action sink.
- The Chrome extension bridge is local-only and user-operated.
- Do not add stealth, anti-detection, CAPTCHA bypass, process-memory reading,
  packet interception, credential interception, or anti-cheat bypass logic.
