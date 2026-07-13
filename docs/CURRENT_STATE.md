# Current State

Updated: 2026-07-13

The staged roadmap and acceptance criteria are in
[`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md).

## Goal

Build a bounded, user-operated automation controller that can level a selected
character to a configured level. The complete loop is:

1. restore health and prowess when required;
2. find an allowed target;
3. enter and complete the battle;
4. exit the result screen;
5. recover from death and return to the interrupted location/activity;
6. continue until the cycle, time, or level limit is reached.

The implementation is still an experimental harness, not a finished unattended
leveling product.

## Runtime

- Branch: `codex/leveling-mvp`
- Python entry point: `src.antibot_cv.automation.controller`
- Chrome bridge version: `2026-07-13-quest-sections-v29`
- Main config: `config/automation.local.json`
- Local bridge: `http://127.0.0.1:17654`
- Chrome extension source: `browser_injector/`

## Confirmed Working

- A Chrome tab registers with the local server and is addressed by its own
  `client_id`.
- The popup can start and stop a run for the selected tab.
- Player HP, prowess, level, battle state, skills, and hunt candidates are read
  through the page bridge.
- Battle skills are invoked through game-page JavaScript rather than desktop
  coordinate clicks.
- Target filtering supports configured monster levels.
- Inventory recovery has a direct JS path for health and prowess items.
- A confirmed free resurrection can be submitted through the page bridge.
- The post-resurrection notice can be closed by its exact `Закрыть` control.
- The compass child window accepts an exact monster/location target and can
  build a route. Focus then returns to `main.php`.
- Quest snapshots distinguish active and available quest cards, preserve the
  quest ID and route labels, and parse explicit objective progress such as
  `5/5`. Completed objectives stop safely before an unimplemented turn-in.
- M1 recovery telemetry now correlates every required phase under one
  `recovery_id`. A deterministic offline validator reports missing or
  out-of-order evidence without claiming that the live gate passed.

## Confirmed Live Evidence

The 2026-07-11 isolated death test detected death, selected the explicit free
revive option, confirmed resurrection, and performed two guarded actions with
zero controller errors. The old raw run was removed during cleanup; the durable
result is recorded here because the run itself was not a regression fixture.

The same test exposed an incorrect fallback: after revival the controller
opened hunt immediately because the checkpoint location was recorded as
`area`. Later code now preserves the last semantic alive location and routes a
different post-revive location through the compass flow.

The 2026-07-13 bounded farm acceptance run used a strict level-1 target filter
on the configured character. It completed exactly one target, one battle, one
confirmed victory, one result exit, and one return to hunt. The summary was
`completed_cycles=1`, `incomplete_cycles=0`, `errors=0`. Victory was confirmed
by the finished battle plus a live nonzero player-health observation; unknown
outcomes no longer count as completed cycles.

## Current Blocker

Route construction and full guarded multi-step movement are confirmed live, and
the route loop is now integrated into the main controller recovery state.

After the compass builds a route, the main `area.php` view displays the next
marked transition and a 3-5 second transition timer. The bridge currently
returns `semanticName: null` in the generic location snapshot on this page.
The bridge reads the marked transition from `area.controller.compass` and
exposes one guarded `location_route_step` action. A complete live route was
confirmed across locations `109 -> 110 -> 121 -> 122 -> 123 -> 125 -> 200`,
including real transition timers of 2, 4, 5, 3, and 4 seconds.

At the destination the game resets compass target ID to `0`, clears
`foundPath`, and removes `nextTransition`. Route completion must therefore use
the saved original destination plus the current location, not the live compass
target after arrival.

The integrated controller now calls `location_route_step` once per confirmed
location, persists the original destination, and reconstructs the remaining
route after battle or death interruptions. Automated regressions cover all of
these branches.

The current blocker is live acceptance of the complete integrated sequence:
`death -> free revive -> close notice -> checkpoint route -> original route ->
hunt`. One successful run must be followed by three consecutive natural death
recoveries before M1 is considered complete.

Bridge v29 also contains the first bounded quest-progress slice. It observes
active/available quests and can identify a completed combat objective, but it
does not accept or turn in quests. This work must not displace the M1 live gate.

Compass targets are runtime inputs. Recovery is not tied to a specific monster
name: the bridge enters the supplied target, selects the exact matching result,
builds the route, and persists its destination for the controller.

Required guards for every route step:

- character is alive;
- no battle is active;
- page kind is `area`;
- exactly one marked transition exists;
- transition timer is zero;
- after activation, either the semantic location changes or the timer becomes
  positive;
- hunt must not resume until arrival at the saved destination is confirmed.

Incoming PvP can interrupt travel. In that case route execution must pause,
allow battle/death recovery to finish, and then resume from the saved route.

## Known Risks

- Recovery items work through the direct command, but the same operation in a
  long automatic cycle still needs a fresh regression test.
- Multi-window addressing exists, but concurrent long runs need soak testing.
- Some UI and replay CV fallbacks still coexist with JS control. Mob sprite
  template targeting has been removed; keep any remaining fallback changes
  inside their owning runtime module with focused replay coverage.
- A bridge code change requires reloading the unpacked extension and refreshing
  the game page. The Python and extension bridge version must match.
- Bridge v29 passes automated bridge and policy tests but still needs a fresh
  live extension reload and bounded acceptance run.

## Architecture Map

- `src/antibot_cv/automation/controller.py`: orchestration and run lifecycle.
- `src/antibot_cv/automation/controller_cli.py`: CLI commands and parsing.
- `src/antibot_cv/automation/screen_runtime.py`: page synchronization.
- `src/antibot_cv/automation/leveling_runtime.py`: leveling and death flow.
- `src/antibot_cv/automation/navigation_runtime.py`: hunt and route execution.
- `src/antibot_cv/automation/combat_runtime.py`: combat and battle outcomes.
- `src/antibot_cv/automation/resource_runtime.py`: resource gates and resting.
- `src/antibot_cv/automation/recovery_items_runtime.py`: between-battle items.
- `src/antibot_cv/automation/desktop_runtime.py`: bounded desktop fallback.
- `src/antibot_cv/automation/state_machine.py`: allowed state transitions.
- `src/antibot_cv/automation/actions.py`: guarded action routing.
- `src/antibot_cv/automation/browser_injector.py`: local command broker.
- `src/antibot_cv/automation/control_server.py`: popup HTTP API and per-tab runs.
- `src/antibot_cv/automation/death_recovery.py`: revive policy.
- `src/antibot_cv/automation/route_planner.py`: route policy/checkpoints.
- `browser_injector/page_bridge_modules/*.js`: editable page behavior by domain.
- `browser_injector/page_bridge.js`: generated Chrome bundle; never edit it
  manually.
- `browser_injector/content.js`: page bridge transport.
- `browser_injector/background.js`: local network requests and tab identity.
- `browser_injector/popup.*`: user controls.

## Commands

Start the macOS control server:

```bash
# From the repository root:
source .venv/bin/activate
python -m src.antibot_cv.automation.controller control-server \
  --config config/automation.local.json \
  --live
```

Check bridge registration:

```bash
python -m src.antibot_cv.automation.controller injector-status --timeout 5
```

Run the test suite:

```bash
python -m pytest -q
```

Assess one recorded M1 recovery run without performing any game action:

```bash
python -m src.antibot_cv.automation.controller assess-m1-recovery \
  --events runs/<session-id>/events.jsonl
```

`offline_ready: true` means the recorded phases are internally complete. M1
still requires the documented live run and three consecutive natural recovery
passes.

## Next Session Checklist

1. Read this file, then `docs/DEVELOPMENT_LOG.md`.
   Use `docs/DEVELOPMENT_PLAN.md` to confirm the current milestone and avoid
   expanding scope before its exit gate passes.
2. Start the control server and confirm `version_ok: true`.
3. Start one bounded live run for the selected game tab.
4. Trigger or observe one natural death while traveling/farming.
5. Verify the full recovery sequence reaches the original destination and
   opens hunt without manual input.
6. Repeat for three consecutive natural deaths and record the results.

## Safety Boundary

Dry-run remains the default. Live actions require explicit `--live` and must go
through the safety guard/action sink. Do not add stealth, CAPTCHA bypass,
process-memory access, credential interception, packet interception, or
anti-cheat bypass behavior.
