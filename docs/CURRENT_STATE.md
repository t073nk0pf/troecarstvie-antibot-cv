# Current State

Updated: 2026-07-17

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
- Chrome bridge version: `2026-07-13-quest-scope-v52`
- Chrome extension version: `0.3.21`
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
- The injected UTF-8 bridge is decoded correctly on the game's legacy-encoded
  pages, so Cyrillic navigator sections and targets remain intact.
- Quest snapshots distinguish active and available quest cards, preserve the
  quest ID and route labels, and parse explicit objective progress such as
  `5/5`. Completed objectives stop safely before an unimplemented turn-in.
- The global `user_quest.php?mode=avail` catalogue is parsed across bounded
  pages with stable numeric IDs, descriptions, rewards, locations, and quest
  givers. The autonomous director builds a deduplicated accept-all queue,
  refreshes after five completed quests, and permits profit farming only after
  a fresh empty active/available observation.
- Quest chains can be pinned and persisted across refreshes. A bounded work
  scheduler keeps one leased quest authoritative, permits an explicit pin to
  replace stale chain state, and releases the lease only on confirmed terminal
  evidence. Opportunistic local monster matching, the bounded world registry,
  and guarded instance-entry routing are available offline.
- Exact NPC quest intake is implemented as a snapshot-bound action chain:
  resolve one area NPC, open the matching quest, click the exact
  `Взять задание` control, and re-read every active-quest page before
  acknowledging success. Ambiguous names, IDs, controls, or stale snapshots
  fail closed. Autonomous mode remains disabled by default.
- The first executable objective slice is implemented offline: from a complete
  active catalogue the director selects the first supported exact monster
  target at or below the character level, preserves its navigator link label,
  routes to that monster, constrains hunt selection to its exact name and
  level, and requires a complete active-list refresh after every victory and
  after resurrection. It never skips an earlier navigation step to reach a
  later monster, and stops after ten confirmed victories without observable
  quest progress.
- A bounded dialogue-objective executor is implemented offline. It recognizes
  two evidenced Russian objective word orders, routes to one exact location,
  resolves one exact NPC, advances only snapshot-bound dialogue actions, and
  requires a fresh active-catalogue verification before accepting completion
  or step advancement. Ambiguous or malformed alternatives fail closed; when
  several valid replies are visible, only a unique non-refusal choice is
  eligible.
- Deferred quest work and gathering groundwork preserve unsupported work
  without silently farming through it. Gathering snapshots and pure inventory
  progress planning handle inflected resource names conservatively, but node
  discovery and a complete gathering executor remain pending.
- An explicitly preferred quest is treated as an intake preference until it is
  confirmed active, after which the persisted chain lease owns scheduling.
  Catalogue reconciliation waits do not fall through into legacy navigation
  failures.
- Quest navigation may use the generic location navigator only after the exact
  quest-link action returns the explicit `quest_navigator_link_missing` error.
  Other failures remain blocked.
- Completed quest progress takes priority over a missing current-location
  observation and stops at the unimplemented turn-in boundary.
- M1 recovery telemetry now correlates every required phase under one
  `recovery_id`. A deterministic offline validator reports missing or
  out-of-order evidence without claiming that the live gate passed.
- The extension has an idle-only self-update lifecycle. It compares its own
  bridge version with the control server, writes a one-shot update marker,
  reloads the extension, and refreshes the primary game tab with cache bypass.
  Navigator child tabs are excluded from primary-tab selection, and a failed
  version pair uses a five-minute retry backoff.

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

The 2026-07-13 bounded M1 live run on bridge v36 completed the requested fair
scenario end to end. The level-5 character selected the exact monster
`Белая Рысь [6]` (`botId=1102`), entered one battle, died naturally, used only
the explicit free resurrection option, closed the resurrection notice, restored
100% health and prowess, and returned from `Городская площадь Арсы` to the
saved checkpoint `Порт безбрежного моря`. Hunt reopened at the destination and
the complete recovery evidence was recorded. The external polling stop then
raced with the resumed farm loop: one second attack request was emitted before
the stop reached the controller, although a second battle was not observed.
The runtime now stops internally and synchronously after terminal recovery when
the configured death limit is reached, so a one-death session cannot issue that
extra attack; this boundary fix is covered by regression tests but still needs
its next live proof.

All nine required phases were recorded in order under recovery ID
`mriuwwj6-47`. The offline assessment for
`runs/c3fc0238e6c049548af0b610cbd9b07d/events.jsonl` reports
`complete_attempts=1`, `consecutive_complete_attempts=1`,
`latest_attempt_passed=true`, and `offline_ready=true`. No controller error or
paid action was recorded. This is the first of the three consecutive natural
recoveries required by the formal M1 exit gate.

The 2026-07-13 quest-intake live run on bridge v46 parsed 42 available quests
and 23 active quests across three pages each. It selected quest `187`
`Заблудшие враги`, travelled to `Лес призраков`, resolved
`Хранителя леса Франка` to the unique clickable `Дом Франка`,
opened the exact quest, clicked `Взять задание`, and confirmed quest
`187` in a fresh complete active catalogue. The active count increased from 23
to 24 and telemetry recorded `quest_accept_confirmed`.

## Current Blocker

For autonomous questing, exact monster-hunt selection, route handoff, and the
first bounded dialogue executor are implemented and covered offline. The next
blocker is bounded live proof followed by completed-quest turn-in. Pure travel,
location-action, collection execution/completion, and further chained
objective shapes still require typed executors before level-20 operation can
be unattended.

Route construction and full guarded multi-step movement are confirmed live, and
the route loop is integrated into the main controller recovery state.

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

Bridge v46 was active for the quest-intake acceptance run. The integrated v36
sequence
`death -> free revive -> close notice -> checkpoint route -> original route ->
hunt` has passed once without manual game input. The remaining formal M1 gate
is two more consecutive natural recoveries, bringing the current streak from
`1/3` to `3/3`. The next run must also confirm the new internal
`max_deaths_recovered` stop, rather than relying on an external polling stop.
This validation should not be replaced by repeated startup-only runs.

Bridge v52 adds the bounded quest-scope work described above. These additions
have offline regression coverage but no new live acceptance evidence. It does
not yet execute arbitrary quest objectives or turn in completed quests. This
work must not displace the M1 live gate.

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
- The updater handles later bridge changes only after a context containing the
  updater has loaded. The exact-tab retry is bounded, but automatic primary-tab
  refresh still needs a dedicated live proof; the v36 acceptance used
  computer-controlled reload after the extension source update.
  Python and extension bridge versions must still match before a run starts.
- Bridge v36 has passed a bounded end-to-end M1 live run. Final regression,
  JavaScript syntax, generated-bundle, architecture-limit, and whitespace
  results are recorded in `DEVELOPMENT_LOG.md`.

## Architecture Map

- `src/antibot_cv/automation/controller.py`: orchestration and run lifecycle.
- `src/antibot_cv/automation/controller_cli.py`: CLI commands and parsing.
- `src/antibot_cv/automation/screen_runtime.py`: page synchronization.
- `src/antibot_cv/automation/leveling_runtime.py`: leveling and death flow.
- `src/antibot_cv/automation/navigation_runtime.py`: hunt and route execution.
- `src/antibot_cv/automation/combat_runtime.py`: combat and battle outcomes.
- `src/antibot_cv/automation/resource_runtime.py`: resource gates and resting.
- `src/antibot_cv/automation/resource_action_helpers.py`: page-bridge resource
  action polling helpers.
- `src/antibot_cv/automation/quest_active_catalog.py`: fail-closed active-list
  pagination.
- `src/antibot_cv/automation/quest_intake_runtime.py`: exact NPC acceptance
  state machine.
- `src/antibot_cv/automation/quest_dialogue_runtime.py`: bounded dialogue
  objective state machine.
- `src/antibot_cv/automation/quest_dialogue_choice_policy.py`: conservative
  dialogue reply selection.
- `src/antibot_cv/automation/quest_chain_runtime.py`: persisted quest lease and
  terminal reconciliation.
- `src/antibot_cv/automation/quest_work_scheduler.py`: bounded deferred quest
  work scheduling.
- `src/antibot_cv/automation/gathering_activity_runtime.py`: pure gathering
  plan and inventory-progress policy.
- `src/antibot_cv/automation/world_registry.py`: bounded location and instance
  registry.
- `src/antibot_cv/automation/navigator_action_runtime.py`: explicit bounded
  navigator fallback policy.
- `src/antibot_cv/automation/quest_giver.py`: conservative catalogue-giver to
  area-NPC resolution.
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
- `browser_injector/update_runtime.js`: idle-only extension self-update policy.
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

`offline_ready: true` means the recorded phases are internally complete. The
formal M1 gate requires a trailing streak of three natural recovery passes;
the current verified streak is `1/3`.

## Next Session Checklist

1. Read this file, then `docs/DEVELOPMENT_LOG.md`.
   Use `docs/DEVELOPMENT_PLAN.md` to confirm the current milestone and avoid
   expanding scope before its exit gate passes.
2. Start the control server and confirm bridge v52 with `version_ok: true`.
3. Start one bounded live run for the selected game tab.
4. Trigger or observe one natural death while traveling/farming.
5. Verify the full recovery sequence reaches the original destination and
   opens hunt without manual input.
6. Record two more consecutive natural recoveries to complete the `3/3` M1
   gate, then continue with the stable farm/resource loop.

## Safety Boundary

Dry-run remains the default. Live actions require explicit `--live` and must go
through the safety guard/action sink. Do not add stealth, CAPTCHA bypass,
process-memory access, credential interception, packet interception, or
anti-cheat bypass behavior.
