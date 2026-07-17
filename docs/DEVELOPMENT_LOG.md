# Development Log

## 2026-07-17 - Durable NPC Quest Action Journal (v61)

- Aligned bridge `2026-07-17-npc-action-journal-v61` and extension `0.3.30`.
  The generated ten-module bundle is byte-reproducible at SHA-256
  `13b6fa8e0ec8efd3f70ee81864526afda9d3337c8aedf15c89013084c8a2ae73`
  and is `286292` bytes.
- Added one durable causal journal for every intake mutation kind: exact
  `OPEN`, `ANSWER`, and final `ACCEPT`. Each action is staged before dispatch;
  uncertain delivery remains `ACK_PENDING`, while only an explicit
  `NOT_ISSUED` outcome permits rollback.
- Restart handling issues zero repeated NPC mutations. `OPEN` and `ANSWER`
  settle only from a fresh exact NPC snapshot with a changed semantic action
  fingerprint and one bounded successor. `ACCEPT` settles only from one exact
  quest in a complete active catalogue. Expired journals stop without injector
  reads or actions.
- P0/P1/P2 hardening rejects wrong client/profile/tab, quest, NPC, action,
  title/ref/text, timestamp, origin, schema, and non-canonical checkpoint
  shapes. Ambiguous or unchanged successor evidence waits or stops fail-closed.
  Checkpoint/CAS write failures restore all pending journal, dialogue, lease,
  and acceptance state instead of exposing partial in-memory advancement.
- The independent critic completed with PASS. Final verification passed exactly
  `968` tests and all `5` architecture tests, Python compilation, JavaScript
  syntax checks, deterministic bundle reproduction, version agreement,
  `git diff --check`, and the authored-source line gate: `101` files, none over
  1500 lines.
- Status: **PARTIAL**. Registered browser tabs still run v59; manual extension
  Reload is required before v61 live evidence. The next guarded order is exact
  quest-269 legacy recovery proof, optional bounded apply only on exact identity,
  then one bounded live journal acknowledgement run. Composite objectives and
  procurement mutation remain unimplemented/unproved.

## 2026-07-17 - Durable NPC Open Acknowledgement And Legacy Recovery (v60)

- Aligned bridge `2026-07-17-npc-open-ack-v60` and extension `0.3.29`.
  The generated ten-module bundle is byte-reproducible at SHA-256
  `162f1e69a084890fda9e1f37bdc0901192dc4ec1998a119a8fbe3fe370c94c0f`.
- Live run `4e5b3e3a00ed4a7b95fbdf441545227d` completed all three
  available-catalogue pages (`31` cards) and all three active pages (`30`
  cards). Quest `236` reached its exact local NPC and was quarantined on an
  ambiguous/missing dialogue-open action. Quest `267` then exhausted its
  bounded giver-not-observed settle window and was quarantined. The scheduler
  continued to quest `269`, routed to `След Велета`, and attempted the exact
  Vargard NPC open without manual routing.
- The pre-v60 bridge reported `npc_open_postcondition_failed` for quest `269`,
  while the exact dialogue was later observed open. This is recorded as a
  false-negative postcondition. It is not converted retroactively into a live
  PASS and does not authorize a repeated mutation.
- Added durable causal NPC-open acknowledgement, systemic compare-and-swap
  checkpoint transitions, and an explicit legacy recovery workflow. The
  recovery command is read-only unless `--apply` is supplied and requires a
  unique bounded event, exact client/profile/tab identity, fresh exact NPC
  snapshot, and compatible pending quest state. These v60 paths pass offline;
  they have not run live.
- Independent validation passed exactly `935` tests and all `5` architecture
  tests, Python compilation, JavaScript syntax checks, ten-module byte
  reproduction, version agreement, authored-source line limits, and
  `git diff --check`.
- Status: **PARTIAL**. The registered Chrome extension tabs remain on v59.
  Chrome's extension manager was blocked and idle self-update did not trigger;
  the operator must manually Reload the extension before bounded v60 live
  evidence can be collected. Composite quest execution and procurement mutation
  remain unimplemented/unproved.

## 2026-07-17 - Causal Catalogue Acknowledgement And Durable Quarantine (v59)

- Aligned bridge `2026-07-17-catalog-ack-v59` and extension `0.3.28`.
  The generated ten-module bundle is byte-reproducible at SHA-256
  `e0844ffbf9651a47ca6839e369d3e7f998f7047842fdc4346d1615156bc8086e`.
- Added causal acknowledgement for catalogue navigation, finite numeric and
  client-binding hardening, mixed eligible/ineligible catalogue handling, and
  bounded durable intake quarantine. Offline tests cover expired-stage
  recovery and preserve fail-closed behavior for malformed authoritative
  references.
- Live run `373ffbde69a9492da14f43a15eb79029` stopped safely on an ambiguous
  quest-263 dialogue. Run `3f6d823f4ab147b08ecd8d4424f0256c` then selected
  exact reply `3808`, accepted quest `263`, and confirmed it through a complete
  three-page active catalogue (`29 -> 30`). The composite objective was not
  executed and remains quarantined.
- Run `ce0f2757d95142d7acad1b214cafe7b0` exposed a false catalogue-open timeout.
  After the causal ACK fix, run `8e956985037a42fdafe90446fa21d24c`
  traversed pages `0 -> 1 -> 2` and exposed the mixed-card authoritative-ref
  blocker, which is now fixed offline. Expired-stage recovery still needs live
  proof.
- Independent validation passed exactly `911` tests, `5` architecture tests,
  Python compilation, JavaScript syntax checks, bundle reproduction, version
  agreement, authored-source line limits, and `git diff --check`.
- Status: **PARTIAL**. Exact quest intake is live-proven; composite execution,
  procurement mutation, recovery, and an unattended quest chain are not.

## 2026-07-17 - Generic Collection Progress And Guarded Spellbook Runtime (v55)

- Completed bounded live diagnostic run
  `07be41fd715c40a39e04c0215d2d3876`: quest `31`, one target, one victory,
  one exit, and one hunt return in 40.85 seconds with zero errors or manual game
  input. The run proved that the old fight model still returned
  `ready/cooldown=null`, while server chat reported that enough wasp wings had
  been collected.
- Added a generic read-only collection-progress observer for the strict server
  template `Вы набрали достаточное количество <ресурс>`. Python accepts it only
  when the extracted resource uniquely matches the current authoritative quest
  fingerprint. It never declares completion; it keeps evidence until a strictly
  newer complete active catalogue arrives, tolerates an in-flight pre-trigger
  refresh, performs at most three delayed same-fingerprint retries, and then
  fails closed.
- Connected the typed spellbook policy through a fail-closed adapter. Complete
  five-slot identity, authoritative turn state, and domain-bound exact
  readiness/cooldown evidence are required. Confirmed setup slots are latched
  per battle, recovery items retain priority, and eight unknown observations
  stop unsafe instead of falling through to generic rotation.
- Aligned bridge `2026-07-17-autonomy-evidence-v55` and extension `0.3.24`.
  The generated bridge is reproducibly built from nine authored modules
  (SHA-256 `952a0ab2674258836d29f6c675e2e379a8527700376887d49ccb5cfe05094aad`).
  Final validation passed `588` tests, independent review with no findings,
  Python compilation, all JavaScript syntax checks, architecture/version
  boundaries, and `git diff --check`. Live v55 validation remains pending the
  Chrome extension update.

## 2026-07-17 - Spellbook-Aware Combat Policy Scaffold (v54)

- Read the five equipped level-5 Guardian abilities from the live spellbook
  tooltip models without applying or rearranging them. The exact facts are
  preserved in `docs/3kingdoms/SPELLBOOK_SKILLS.md`.
- Added an action-free typed policy for the current spellbook. It plans eligible
  instant setup slots 1/4, conditional defensive slot 2, then the strongest
  explicitly ready turn action (`3 > 5`). Unknown readiness/cooldown waits with
  zero actions; slot 0 requires an explicit strict-boolean low-prowess fallback.
  This was the action-free precursor to the guarded v55 runtime adapter.
- Added bounded combat observation for the next live evidence slice: allowlisted
  primitive stance/position fields and non-sensitive return type/length metadata
  for synchronous or Promise-returning `useSkill`. These observations do not
  participate in `changed()` or confirmation, and arbitrary strings/objects are
  not persisted.
- Aligned bridge `2026-07-17-combat-observe-v54` and extension `0.3.23`.
  Validation: full `554 passed` suite, independent review with no findings,
  Python compilation, JavaScript syntax checks, reproducible eight-module
  bridge build, architecture boundaries, and `git diff --check` passed. No v54
  live mutation was run; Chrome still needs the extension update before the
  diagnostic battle.

## 2026-07-17 - Two-Cycle Quest Loop And Full Skill Rotation Proof

- Fixed a P0 CLI configuration-shadow bug discovered during live acceptance.
  `--config` before the subcommand was overwritten by a subparser `None`, which
  silently loaded the example config and disabled the autonomous quest director.
  The affected run was stopped before battle.
- Global and subcommand config flags now use separate parser destinations and
  one resolver. Either position preserves the explicit path, equal duplicates
  are accepted, and conflicting duplicates fail with exit code 2 before the
  handler. The `control-server` local default applies only when no explicit path
  was provided.
- Tightened repeated Navigator popup reuse: a new unique child remains
  preferred; an existing popup is reused only when exactly one client has the
  current parent tab as its opener. Existing unlinked or ambiguous popup sets
  fail closed.
- Enabled the complete configured combat rotation `1,2,3,4,5`. Bounded live run
  `791bc2a2919f4360bd8003824140090e` completed two autonomous quest-directed
  battles and two cycles with zero errors, incomplete cycles, recoveries, or
  viewport moves in 89.18 seconds. It refreshed all three active-quest pages
  between victories and repeated exact quest/Navigator/target selection without
  manual game input.
- Policy selected all five slots in both battles. Slots 1, 3, 4, and 5 received
  live `useSkill_confirmed` evidence. Slot 2 (`Смена позиции I`) was attempted
  but remained `useSkill_unconfirmed`, so the mutation path stopped fail-closed;
  stance-specific confirmation is the next combat slice.
- Validation after the CLI and Navigator safety fixes: full `546 passed` suite,
  independent review with no findings, Python compilation, three JavaScript
  syntax checks, architecture boundaries, deterministic eight-module bridge
  rebuild, bundle consistency, and `git diff --check` all passed.

## 2026-07-17 - First Bounded Live Quest Route And Combat Proof

- The first v53 live attempt selected quest `31` (`Поиски Рокоша`) and the
  exact objective target `Гигантская оса [2]`, but exposed a route handoff gap:
  after `navigator_go` the parent remained on the active-quest page, so no
  location transition could run. The session was stopped without a battle.
- Added a guarded `quest_location` parent handoff. Both MOVE and authoritative
  current-location navigator results open `area.php`; route steps remain gated
  until the parent is confirmed as area/hunt/main. A current monster target is
  no longer compared with the semantic location name.
- Validation after the fix: full `538 passed` suite, independent review with no
  findings, Python compilation, three JavaScript syntax checks, architecture
  boundaries, deterministic bundle rebuild, and `git diff --check` passed.
- Bounded live run `d5e07ca5670a48cea0e348c1921f35bb` then completed the
  automatic sequence `active catalogue -> exact navigator target -> open area
  -> route 124/121/110/111 -> hunt -> exact botId 1096 attack -> battle ->
  confirmed victory -> exit -> hunt return`. Summary: one requested and one
  completed cycle, zero incomplete cycles, zero errors, five confirmed combat
  actions, one exit action, and no recovery.
- The run stopped internally at `max_cycles=1`. That proves the route/combat
  handoff but intentionally stops before a post-victory active-catalogue refresh,
  so observable quest-progress advancement still needs a separate bounded live
  proof. No purchase or paid resurrection action was allowed.

## 2026-07-17 - Guarded Automated Quest Loop And CLI Safety (v53)

- Routed every mutating injector CLI command through the shared safety guard,
  `ActionExecutor`, and action sink. Each command requires its own explicit
  `--live`; dry-run invocation returns before injector mutation or client lookup.
- Strengthened architecture tests with a runtime/policy/helper AST import graph
  and a fail-closed allowlist for mutating injector calls outside the approved
  action path.
- Persisted the authoritative quest intake reference before the accept mutation
  and recover it only from a fresh matching active catalogue after restart.
- Integrated the bounded completed-step turn-in coordinator: exact persisted
  identity, route, NPC, dialogue, guarded completion action, and newer complete
  active-catalogue reconciliation. Terminal absence releases the quest; a new
  fingerprint advances the pinned chain; unchanged evidence retries only inside
  a bounded phase deadline.
- Scoped every giver/location reference to the fingerprint that established it.
  A later chain step cannot reuse the intake giver without new authoritative
  evidence and therefore stops before route or NPC mutation.
- Staged final completion evidence durably before the guarded `done` mutation.
  Restart recovery distinguishes terminal absence, continued fingerprint, and
  an unconfirmed same step, then runs the normal scheduler cleanup path.
- Reworked navigator selection around one strict deadline with dynamically
  bounded delays and retry count. Removed the content transport's accidental
  five-second minimum for commands without an inventory delay.
- Aligned bridge `2026-07-17-quest-loop-v53` and extension `0.3.22`. Validation:
  full `537 passed` suite, Python compilation, JavaScript syntax checks for the
  generated bridge/background/content scripts, deterministic eight-module
  bundle rebuild, architecture boundaries, and `git diff --check` passed. No
  live acceptance claim is included.

## 2026-07-17 - Completed Quest Turn-In Safety Contract

- Added a pure offline turn-in domain runtime with explicit route, NPC lookup,
  dialogue, completion, and active-catalogue verification phases.
- Turn-in begins only when a completed active entry, current persisted chain
  lease, saved giver, and exact location agree. It emits only existing guarded
  action contracts and performs no side effects itself.
- Malformed or ambiguous action collections fail closed. Terminal completion
  requires a complete active catalogue with a revision newer than the submitted
  completion action and with the pinned quest absent.
- Controller wiring is intentionally pending because intake does not yet
  guarantee persistence of the authoritative quest reference required by the
  turn-in contract. No bridge change or live claim is included in this slice.

## 2026-07-17 - Bounded Quest Scope And Safety Hardening

- Added an offline-covered dialogue-objective executor that parses two
  evidenced Russian objective word orders, routes through the existing guarded
  navigation runtime, resolves one exact NPC, submits one snapshot-bound action
  at a time, and verifies progress from a fresh complete active catalogue.
- Added a conservative dialogue-choice policy. Exact duplicate controls are
  deduplicated, a unique non-refusal reply may advance, and tied, unknown, or
  malformed alternatives fail closed.
- Made an explicit preferred quest an intake preference until fresh available
  and active catalogues confirm acceptance; the resulting persisted chain lease
  then owns scheduling without redundant catalogue refreshes.
- Added a bounded generic-navigator fallback only for the explicit
  `quest_navigator_link_missing` result. All other injector failures remain
  blocked through the existing safety action sink.
- Corrected quest-policy ordering so confirmed completion stops at the pending
  turn-in boundary even when the current location is unavailable. Transitional
  director waits no longer fall through to a false missing-navigation stop.
- Tightened gathering inventory matching so derived names cannot be mistaken
  for one unambiguous required resource, and expanded objective classification
  for evidenced Russian dialogue verbs.
- Aligned the generated bridge, content script, background worker, and Python
  broker at `2026-07-13-quest-scope-v52`; extension version is `0.3.21`.
- Validation on the migrated arm64 Python 3.11 environment: full `490 passed`
  regression suite, Python compilation, JavaScript syntax checks for the bridge
  and extension scripts, deterministic bundle consistency, architecture limits,
  and `git diff --check` passed. Independent review reported no findings. No new
  live acceptance claim is made by this entry.

## 2026-07-17 - Deferred Quest And Gathering Groundwork (v51)

- Added deferred quest-work scheduling, gathering snapshots, pure gathering
  plan/progress policy, and quest-chain support for non-combat work that is not
  yet executable end to end.
- Added conservative inflected resource-name matching and complete-catalogue
  refresh waits. Gathering node discovery and mutation remain pending.
- Aligned bridge `2026-07-13-quest-scope-v51` and extension `0.3.20` before the
  subsequent v52 safety hardening.

## 2026-07-17 - Typed Pinned Dialogue Execution (v50)

- Added the first typed pinned-NPC dialogue executor: exact location and NPC
  verification, snapshot-bound bounded actions, duplicate-answer prevention,
  route return to the area page, and fresh active-catalogue verification.
- Transitional snapshot retries are bounded, and dialogue work is dispatched
  before legacy combat/intake fallbacks.
- Aligned bridge `2026-07-13-quest-scope-v50` and extension `0.3.19`.

## 2026-07-17 - Pinned Quest Chains And Instance Routing (v49)

- Added persisted pinned quest chains, explicit pin scheduling, opportunistic
  exact local-monster matching, a bounded world registry, guarded instance
  entry/actions, and a dedicated quest-work scheduler.
- Preserved dry-run isolation, allowed an explicit pin to replace stale chain
  state, and restricted lease release to confirmed terminal evidence.
- Aligned bridge `2026-07-13-instance-entry-v49` and extension `0.3.18`.
  There was no separate v48 release.

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
