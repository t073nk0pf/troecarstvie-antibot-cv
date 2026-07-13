# Development Log

## 2026-07-13 - Exact Monster Quest Objective Slice

- Inspected all 24 currently active quests across the complete three-page live
  catalogue and separated monster combat/collection, NPC dialogue, travel,
  location-action, return, and chained objective shapes.
- Added an action-free objective runtime that selects the first supported exact
  `Name [level]` monster target from the complete active catalogue, rejects
  ambiguous or above-level targets, fingerprints the step, and compares fresh
  progress without treating a partial catalogue as authoritative.
- Wired the selected quest target and exact navigator link label through the
  quest orchestrator, safety action, and browser bridge. Quest name and level
  override generic farming filters only while that typed objective is active.
- Added a complete active-catalogue revision and mandatory refresh after every
  confirmed victory. Same-step progress can continue; completion, a changed
  step, removal, or regression stops safely for the next executor slice.
- Restricted execution to a single monster target in the first actionable
  navigation entry, bounded unchanged progress to ten confirmed victories, and
  required a complete new active catalogue after resurrection before quest
  execution can resume.
- Aligned the generated bridge, content script, background worker, and Python
  broker at `2026-07-13-quest-objective-v47`; extension version is `0.3.16`.
- Added focused parser, director, action, JavaScript, and controller integration
  coverage. Live mutation proof is intentionally pending until the updated
  extension is active and the accept-all intake queue is exhausted or a
  separately bounded objective test is started.

## 2026-07-13 - Exact NPC Quest Intake And Live Acceptance

- Added a dedicated NPC quest bridge module with fail-closed area-NPC and
  dialogue snapshots. Every mutation is bound to fresh location, NPC, quest,
  action, and snapshot evidence and performs at most one click.
- Added full pagination for `mode=started`, a fail-closed active-catalogue
  accumulator, conservative Russian giver-name resolution, and an autonomous
  intake runtime that acknowledges acceptance only after the numeric quest ID
  appears in a fresh complete active catalogue.
- Fixed quest-route handoff through the compass and explicit area reopening so
  the director can travel from catalogue pages to the intended giver.
- Live bridge v46 parsed 42 available and 23 active quests, selected quest 187
  `Заблудшие враги`, travelled to `Лес призраков`, clicked the
  exact `Взять задание` button, and confirmed active count 24 plus
  `quest_accept_confirmed` without manual game input.
- Aligned the generated bridge, content script, background worker, and Python
  broker at `2026-07-13-npc-proxy-v46`; extension version is `0.3.15`.
- Validation: full `424 passed` regression suite, JavaScript syntax, Python
  compilation, deterministic six-module bridge build, authored-source line
  limits, and `git diff --check` passed.

## 2026-07-13 - Global Quest Catalogue And Autonomous Director Slice

- Inspected the authenticated global catalogue at
  `user_quest.php?mode=avail`: 43 visible quests across three pages in the
  current live session. The page exposes location navigation and giver facts,
  but no direct quest-accept action.
- Added stable numeric quest IDs from `quest_folding.toggle(ID)`/`#quest_ID`,
  descriptions, rewards, giver links, route facts, objective kinds, and
  canonical pagination to the page snapshot.
- Added guarded `open_quest_catalog(page)` navigation with a bounded page and
  exact `mode=avail`/page postcondition.
- Added a validated multi-page accumulator and autonomous director policy:
  initial discovery, accept-all queue with deduplication, refresh after five
  completed quests, active-quest execution handoff, and profit-farm fallback
  only after a fresh empty catalogue.
- Integrated the catalogue refresh loop behind disabled-by-default
  `autonomous_quest_director`. A non-empty queue currently stops safely at
  `quest_accept_executor_pending`; the next slice is exact NPC dialogue and
  active-list verification.
- Aligned the bridge at `2026-07-13-quest-catalog-v38` and the extension at
  `0.3.7`, allowing the existing idle update lifecycle to deploy the bridge.


## 2026-07-13 - Bridge v36 And First Complete M1 Natural Recovery

- Made the page bridge load explicitly as UTF-8 on the game's legacy-encoded
  pages, preserving Cyrillic navigator labels such as `Монстры` and the exact
  target `Белая Рысь [6]`.
- Hardened exact autocomplete section resolution, delayed route rendering,
  hidden duplicate route-button filtering, navigator child handoff, and strict
  parent-page route confirmation after a child ACK timeout.
- Added a guarded `open_area` preparation action so a run starting on hunt can
  capture a semantic location checkpoint before configured navigation.
- Added a bounded exact-tab extension refresh retry and lifecycle ordering;
  aligned Python, content, background, and generated bridge versions at
  `2026-07-13-visibility-v36` with manifest version `0.3.5`.
- Live session `c3fc0238e6c049548af0b610cbd9b07d` selected and attacked one
  `Белая Рысь` level 6, observed one natural death, used the confirmed free
  resurrection, restored resources, returned to `Порт безбрежного моря`, and
  reopened hunt. No paid action or controller error was recorded. The external
  polling stop arrived after one second attack request, exposing a bounded-run
  race; no second battle was observed before the stop.
- Fixed that race inside the controller: a positive reached death limit now
  stops synchronously after `death_recovery_completed`, and the leveling policy
  no longer grants an off-by-one extra farm attempt. A focused full-recovery
  regression proves that `max_deaths_per_session=1` opens hunt, records all
  evidence, enters `STOPPED`, and emits no further attack.
- Recovery ID `mriuwwj6-47` contains all nine ordered M1 phases. Offline
  assessment passed with `complete_attempts=1`,
  `consecutive_complete_attempts=1`, and `offline_ready=true`; the formal M1
  streak is now `1/3`.
- Validation: full `349 passed` regression suite, deterministic generated
  bridge, JavaScript syntax, authored-source line limits, and
  `git diff --check` passed. Independent review found no unresolved blocking
  finding.

## 2026-07-13 - Extension Self-Update And Navigator Child Hardening

- Added an idle-only extension update runtime with a one-shot persistent
  marker, exact primary-tab selection, cache-bypassing tab refresh, a
  five-minute version-pair retry backoff, and no new Chrome permissions.
- The background worker checks the control server automatically and also wakes
  the throttled update check from normal content-script traffic, so MV3 worker
  suspension does not make the timer the only trigger.
- Navigator child binding now accepts one unique new same-profile navigator
  when Chrome omits `openerTabId`, while failing closed on ambiguity and
  snapshotting pre-existing navigator tabs.
- Target selection waits for the child input to settle and allows one bounded
  retry only when the bridge reports one unique exact candidate with a late
  section classification.
- Bumped the aligned Python/content/background/page bridge version to
  `2026-07-13-self-update-v31` and the extension manifest version to `0.3.0`.
- The full 338-test suite, JavaScript syntax checks, Python compilation,
  generated-bundle consistency, architecture limits, and `git diff --check`
  pass. The loaded pre-v31 Chrome context still needs lifecycle activation
  before the bounded level-6 death/revive/return live test can continue.

## 2026-07-13 - M1 Recovery Evidence And Obsolete Sprite Cleanup

- Removed the obsolete `mob_sprite_01..03` assets, template configuration,
  `sprite_template_ids` policy, CV template fallback, and its asset-coupled
  regression. Live target selection remains the guarded JS path; the separate
  `green_sprite` color fallback remains available for replay/CV diagnostics.
- Added one `recovery_id` across the M1 evidence chain: death, revive request,
  revive confirmation, resurrection notice close, resources ready, checkpoint
  arrival, original destination arrival, hunt open, and terminal completion.
- Terminal `death_recovery_completed` is emitted only after a successful hunt
  open and only when every required earlier phase was observed in order.
- Added a pure offline validator plus `assess-m1-recovery --events <path>` for
  deterministic post-run evidence checks. The result is deliberately named
  `offline_ready`; it never closes the live acceptance gate.
- Added positive and fail-closed runtime regressions plus validator and CLI
  tests. The full automated suite, architecture checks, Python compilation,
  template validation, and `git diff --check` pass. The complete live
  death-to-return run and three consecutive natural recoveries are still
  required before M1 can be marked complete.

## 2026-07-13 - Quest Snapshot And Post-Revive Resource Gate

- Added an explicit `POST_REVIVE_RECOVERY` state that confirms configured
  health/prowess thresholds and uses bounded allowlisted recovery items before
  route or hunt resumes.
- Fail closed when the death checkpoint lacks a semantic location, and preserve
  the interrupted destination across navigator, battle, and death transitions.
- Added a dedicated quest runtime plus structured active/available quest
  snapshots, objective progress parsing, and the `OBJECTIVE_COMPLETE` intent.
- Completed objectives intentionally stop at
  `quest_objective_complete_turn_in_pending`; accepting and turning in quests
  are not implemented yet.
- Aligned Python, content, background, and generated bridge sources at
  `2026-07-13-quest-sections-v29`.
- Validation: `314 passed`; Python compilation, JavaScript syntax, generated
  bridge consistency, architecture limits, and `git diff --check` passed.
- No v29 live run was performed during this validation. The M1 live gate remains
  the next acceptance step.

## 2026-07-13 - Modular Runtime And Victory Gate

- Split the former controller into bounded screen, leveling, navigation,
  combat, resource, recovery-item, desktop, CLI, helper, and constant modules.
- Split the editable page bridge into five ordered domain sources and made
  `page_bridge.js` a reproducible generated artifact.
- Added architecture tests enforcing the 1500-line authored-file ceiling and
  generated-bundle consistency.
- Added fail-closed target filtering, per-tab battle/resource probes, guarded
  zero-skill fallback, battle-item readiness checks, and explicit battle
  outcome tracking.
- Live acceptance on bridge `2026-07-13-battle-outcome-v24`: one strict
  level-1 target, one battle, one confirmed victory, one exit, one hunt return,
  `completed_cycles=1`, `incomplete_cycles=0`, `errors=0`.
- Full automated suite passed after the live fix.

Only confirmed changes and test results belong here. Raw telemetry stays in the
ignored `runs/` directory and may be deleted after its conclusions are recorded.

## 2026-07-13

- Split the 5867-line `AutomationController` implementation into a 972-line
  compatibility orchestrator and bounded screen, leveling/death, navigation,
  combat, resource, CLI, helper, and constants modules.
- Preserved the public controller imports and `python -m
  src.antibot_cv.automation.controller` command surface.
- Split editable `page_bridge.js` source into five ordered domain modules of
  574-1347 lines. Added `scripts/build_page_bridge.py`; the Chrome-facing
  `page_bridge.js` is now a deterministic generated bundle.
- Added repository rules and automated tests that reject authored runtime
  source files over 1500 lines or a stale browser bundle.
- Isolated injector reads from test sinks and controllers without an explicit
  browser client, preventing open Chrome tabs from changing test outcomes.

## 2026-07-12

- Removed approximately 3.7 GB of old run telemetry plus recovery/inventory
  debug dumps, Python caches, pytest cache, output artifacts, and `.DS_Store`
  files. No tracked source, config, test, template, or extension file was
  removed.
- Added `output/` to `.gitignore`.
- Added `GET /api/location-route` to the local control API. It sends the
  existing `location_route_snapshot` command to the selected Chrome tab and
  returns parsed JSON without requiring a second server on port `17654`.
- Created this durable context pair: `CURRENT_STATE.md` for the present system
  and `DEVELOPMENT_LOG.md` for verified progress.
- Validation after cleanup: full `pytest -q` suite passed, `node --check` for
  `page_bridge.js` passed, and `git diff --check` reported no whitespace errors.
- Repository working size decreased from approximately 4.0 GB to 341 MB. The
  local `.venv` is intentionally retained because it is required for launch.
- Added bridge `2026-07-12-route-step-v20` with semantic area names, compass
  route fields, exact next-transition validation, timer guards, and the
  `location_route_step` action routed through the safety guard/action sink.
- Live transition test: submitted one guarded move from location `102`
  (`Городская площадь Арсы`) to location `171` (`Пригород Арсы`). The target
  location appeared, confirming movement. Another player immediately initiated
  PvP, which interrupted the route and returned the character to the previous
  recovery context. This is now treated as a route interruption, not a failed
  transition.
- Route-step verification now accepts either a changed location ID or an exact
  semantic match with the expected next transition. This covers the short
  interval before the new compass model publishes its numeric location ID.
- Corrected route timing: `area.ftime` is the fixed transition duration, not a
  countdown. Readiness now uses `finishTimeLocal - Date.now()` together with
  transition-specific `ltime`/`dtime` values.
- Full live route test passed:
  `109 -> 110 -> 121 -> 122 -> 123 -> 125 -> 200`, ending at `Порт Барбуса`.
  The controller respected observed waits of 2, 4, 5, 3, and 4 seconds.
- Destination contract confirmed: after arrival the game reports current
  location `200`, resets compass target to `0`, clears the path, and exposes no
  next transition. Automation must compare against its saved destination.
- Integrated the confirmed route mechanics into controller state
  `ROUTE_RECOVERY`. The controller now stores the original destination and
  transition count, polls the route snapshot, waits for `timerReady`, and
  submits exactly one guarded `location_route_step` per current location ID.
- Route completion persists the destination ID before the game resets its
  compass target. Arrival can be confirmed by either the exact semantic name or
  the saved destination ID.
- Incoming battles now pause route execution and preserve the destination.
  After the battle returns to hunt, the controller reopens the compass for the
  remaining destination instead of counting the interruption as a farm cycle.
- Death during travel now preserves both the death checkpoint and the original
  destination. Recovery returns to the checkpoint first, then constructs a new
  route to the original destination.
- Added regression tests for timer waiting, one-step-per-location submission,
  destination confirmation after compass reset, battle interruption, and death
  interruption. Full `pytest -q`, Python compilation, JavaScript syntax checks,
  and `git diff --check` passed.
- First integrated live acceptance attempt stopped safely with zero actions
  because an explicit location route started from the quest page and the
  generic leveling policy returned `unknown_observation`. Explicit
  `target_location_name` routing now leaves quests/inventory/statistics for the
  area view before invoking the compass.
- Second live attempt reached the compass and stopped safely because the bridge
  only searched the `Локации` result section. The confirmed long-path target is
  a monster. `navigator_select_target` now accepts only the explicit kinds
  `location` and `monster`, selects one exact result from the corresponding
  section, and still fails closed on zero or multiple matches.
- Added a JavaScript regression for exact monster selection and Python action
  routing for `target_kind=monster`. Full `pytest -q` passed after this change.

## 2026-07-11

- Bridge version aligned at `2026-07-11-leveling-mvp-v18` across Python,
  content script, background service worker, and page bridge.
- Added exact post-resurrection notice detection and closing.
- Added fallback parsing for a semantic location name from area-page text.
- Added compass commands for opening the location navigator, selecting an exact
  target, and opening the area view.
- Added `location_route_snapshot` diagnostics.
- Controller now remembers the last confirmed semantic alive location instead
  of relying only on a generic `area` checkpoint.
- Post-revive recovery can enter `NAVIGATOR_PENDING` when the current location
  differs from the saved location.
- An isolated live run confirmed free resurrection with zero errors. It also
  proved that route recovery must not treat opening hunt as successful arrival.
- Manual compass validation confirmed this sequence:
  `open child window -> enter exact monster -> select exact result -> choose
  destination -> build route -> child closes -> focus returns to main.php`.
- Manual validation also confirmed that building a route does not itself move
  the character. Each marked area transition must be activated after its timer
  reaches zero.

## Earlier Stable Capabilities

- Per-tab Chrome client identities and popup controls.
- JS hunt candidate scanning and level filtering.
- JS battle skill activation and battle-result exit handling.
- Resource thresholds and inventory recovery configuration.
- Direct health/prowess recovery item command.
- Windows setup and `botcv` launcher scripts.

## Documentation Rule

When a live test produces a useful result, add a short entry with:

- exact behavior tested;
- success or failure;
- relevant client/bridge version;
- action count and error count when available;
- the next code decision.

Do not keep an unlimited raw-log history inside the repository. Preserve a raw
run only when it is a deliberate regression fixture referenced by a test.
