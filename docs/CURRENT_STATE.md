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
- Chrome bridge version: `2026-07-17-npc-action-journal-v61`
- Chrome extension version: `0.3.30`
- Generated bridge SHA-256:
  `13b6fa8e0ec8efd3f70ee81864526afda9d3337c8aedf15c89013084c8a2ae73`
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
  `5/5`. Completed supported steps enter the bounded turn-in coordinator.
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
  observation and enters turn-in only with a persisted authoritative quest
  reference. Legacy leases without that reference stop with zero actions.
- The completed-step turn-in coordinator is wired offline end to end: the
  completed entry, persisted lease, saved giver, and exact location must agree;
  NPC and dialogue decisions are snapshot-bound; malformed alternatives fail
  closed; and verification requires a newer complete active catalogue. Absence
  releases the chain, while the same quest ID with a new fingerprint advances
  the pinned multi-step chain. The intake giver reference is scoped to the
  fingerprint on which it was confirmed and is never reused for a later step;
  a later completed step without its own authoritative giver reference stops
  with zero route/NPC mutations. Same-step and page-transition waits are
  bounded with phase-specific deadlines that start after route arrival.
- Quest intake stages its authoritative reference durably before the accept
  mutation. A restart between the action and active-list acknowledgement can
  bind that reference only after a fresh matching active entry is observed.
- The guarded final `done` action is also staged durably before mutation. After
  restart, one fresh complete active catalogue can release a terminal absence,
  reconcile a new fingerprint, or clear unconfirmed same-step evidence for a
  bounded retry. Terminal recovery uses the same scheduler cleanup and counters
  as the uninterrupted path.
- Mutating injector CLI commands now require their own explicit `--live` flag
  and execute through the shared safety guard, action executor, and action
  sink. Without `--live` they produce no injector mutations.
- The CLI now resolves `--config` identically before or after the subcommand.
  An explicit path can no longer be silently replaced by a subparser default;
  conflicting duplicate values stop before the command handler.
- Navigator target selection now derives command delay and retry count from one
  strict remaining deadline. The content transport no longer imposes an
  unrelated five-second floor on non-inventory commands.
- M1 recovery telemetry now correlates every required phase under one
  `recovery_id`. A deterministic offline validator reports missing or
  out-of-order evidence without claiming that the live gate passed.
- The extension has an idle-only self-update lifecycle. It compares its own
  bridge version with the control server, writes a one-shot update marker,
  reloads the extension, and refreshes the primary game tab with cache bypass.
  Navigator child tabs are excluded from primary-tab selection, and a failed
  version pair uses a five-minute retry backoff.

## Confirmed Live Evidence

Status through v61 is **PARTIAL**. Run
`373ffbde69a9492da14f43a15eb79029` opened quest `263`
`Милость Громовержца` and submitted the first exact reply, then stopped
fail-closed at `quest_accept_dialog_action_ambiguous`; it did not confirm
acceptance. Run `3f6d823f4ab147b08ecd8d4424f0256c` selected the uniquely evidenced
progress reply `ref=3808`, submitted the exact `Взять задание` action, refreshed
all three active-catalogue pages, observed the active count change from 29 to
30, and emitted `quest_accept_confirmed` for quest `263`. This proves exact
dialogue intake and catalogue acknowledgement, not execution of its composite
three-NPC stone objective. That objective was preserved as an unsupported
durable quarantine rather than cancelled or guessed.

Two later bounded startup runs refined the catalogue contract. Run
`ce0f2757d95142d7acad1b214cafe7b0` stopped after 2.54 seconds on a false
`quest_catalog_open_failed`; v59 replaced the elapsed-time inference with a
causal catalogue-navigation acknowledgement. Run
`8e956985037a42fdafe90446fa21d24c` then proved the causal page sequence
`0 -> 1 -> 2` before stopping fail-closed at
`quest_director_snapshot:intake quest reference is not authoritative` on a
mixed eligible/ineligible catalogue. Mixed-card eligibility, finite numeric
validation, client binding, intake quarantine, and causal catalogue ACK are
now covered offline. The expired-stage recovery path still needs a fresh
bounded live proof.

Run `4e5b3e3a00ed4a7b95fbdf441545227d` added a 126.86-second
live scheduler proof with zero controller errors. It completed all available
catalogue pages (`31` eligible cards) and all active pages (`30` cards),
reconstructed staged intake quest `236`, routed locally, and opened the exact
NPC. The quest then entered durable quarantine because the local dialogue had
no unique open action. Quest `267` was selected next and quarantined only after
the bounded `quest_accept_giver_not_observed` settle window. The scheduler then
selected quest `269`, built and executed its route to `След Велета`, and
attempted the exact NPC open. The old bridge returned
`npc_open_postcondition_failed`, although the exact Vargard dialogue was later
observed open; this is a false-negative postcondition, not proof that the
mutation failed.

v60 replaces that inference with a durable causal NPC-open acknowledgement,
systemic compare-and-swap state transitions, and a bounded legacy recovery
proof for the recorded pre-v60 event. Those paths pass offline only. The
currently registered game/extension tabs still report v59: Chrome's extension
manager could not be controlled and the idle self-update did not trigger, so a
manual extension **Reload** is the current external blocker before any v60 live
or v61 live claim.

v61 extends the durable boundary from opening the NPC to every intake dialogue
mutation. Exact `OPEN`, `ANSWER`, and final `ACCEPT` actions are staged before
dispatch and remain journaled until a causal successor proves them. Restart
paths perform zero reissue: they settle from read-only exact snapshots or stop
when evidence expires. P0/P1/P2 coverage rejects wrong client/profile/tab,
quest/NPC/action identity, non-canonical schema, stale timestamps, ambiguous
successors, and incomplete or non-unique active catalogues. Explicit
`NOT_ISSUED` may roll back safely; unknown delivery remains `ACK_PENDING`.
Checkpoint compare-and-swap/write failures restore all in-memory journal and
dialog state. This journal and the critic review pass offline only.

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

The 2026-07-17 bounded v53 quest-route run
`d5e07ca5670a48cea0e348c1921f35bb` executed without manual game input. It
refreshed the pinned active quest `31` (`Поиски Рокоша`), selected the exact
monster `Гигантская оса [2]`, built a three-transition navigator route, returned
the parent to `area.php`, and executed guarded transitions
`124 -> 121 -> 110 -> 111`. It opened hunt, attacked exact `botId=1096`, won
the battle, exited, and confirmed return to hunt. The summary was
`completed_cycles=1`, `incomplete_cycles=0`, `errors=0`.

The preceding attempt exposed and reproduced the missing parent-page handoff
after `navigator_go`; the controller stayed on `user_quest.php` and could not
perform a route step. The v53 Python runtime now performs a single guarded
`open_area` for `quest_location` and gates route observations/actions until the
parent page is confirmed. The corrected path passed the live run above.

Because the acceptance run used `max_cycles=1`, it stopped immediately after
the confirmed farm cycle and before the next active-catalogue refresh. It proves
automatic quest-directed navigation and combat, but not yet observable quest
progress advancement or terminal turn-in.

The 2026-07-17 bounded two-cycle run
`791bc2a2919f4360bd8003824140090e` closed that repeat-loop gap. With no manual
game input, it twice executed `active catalogue (three pages) -> pinned quest
31 -> exact Navigator target -> hunt -> exact Giant Wasp -> battle -> exit ->
hunt`. Between the victories it performed the mandatory complete active-list
refresh and selected the same authoritative objective again. The summary was
`completed_cycles=2`, `incomplete_cycles=0`, `errors=0`, `recoveries=0`, with
two detected targets, two battles, two exits, and zero viewport moves in 89.18
seconds. The final snapshot showed the character alive on hunt at level 5,
98.7% health and 80.1% prowess.

Combat policy selected every configured skill slot `1,2,3,4,5` in both
battles. The bridge confirmed `Элитная выучка I` (slot 1), `Прорубание II`
(slot 3), `Глубокий порез II` (slot 4), and `Танцующее лезвие III` (slot 5).
`Смена позиции I` (slot 2) was requested in both battles but remained
`useSkill_unconfirmed`; the action path therefore failed closed instead of
claiming success. A stance-specific confirmation signal is still required
before slot 2 can be counted as live-confirmed for this character.

The v54 combat-observation slice recorded only bounded, non-confirming evidence:
the type/length of a `useSkill` return value and primitive values from a small
allowlist of player stance/position fields. Arbitrary returned strings and
opaque objects are never persisted. This evidence does not participate in the
success predicate, so a request, Promise resolution, or stance-only change
cannot become a false `useSkill_confirmed` event.

The v55 runtime connects the typed level-5 spellbook profile to combat only
after the complete five-slot identity and authoritative turn/readiness evidence
agree. Slots 1 and 4 are instant setup actions, slot 2 is conditional defense,
and slot 3 is preferred over slot 5 as the stronger turn-consuming attack.
Confirmed setup actions are latched per battle so a short cooldown cannot starve
later setup or attacks. Loose DOM attributes cannot authorize a skill: a visible
control must be inside a battle-ability container and bind to the exact model
slot and available identity. Unknown evidence waits for at most eight
observations and then stops unsafe instead of falling back to slot rotation.
This policy path originated in v55 and remains covered offline. A dedicated
live combat proof of its scoped readiness/cooldown signals is still pending.

The bridge also observes the exact server collection-progress template
`Вы набрали достаточное количество <ресурс>`. The resource must match exactly
one phrase in the current authoritative quest objective after conservative
Russian inflection normalization. The chat line never completes a step; it only
requires a strictly newer complete active catalogue. Evidence survives an
already-running refresh, retries the same fingerprint at most three times with
bounded delays, and then stops unsafe. Stable message identity, immutable
first-seen time, baseline handling, TTL, and bounded histories prevent old chat
lines from becoming new evidence after a rolling-window shift or reload.

The bounded v54 diagnostic run `07be41fd715c40a39e04c0215d2d3876`
completed one autonomous quest-directed battle in 40.85 seconds with one
victory, one exit, one returned hunt state, and zero errors. It confirmed the
negative discovery that `useSkill` returns `undefined`, stance state is empty,
and the old ability summaries expose `ready/cooldown=null`. During that battle
the server chat reported `Вы набрали достаточное количество осиных крыльев.`,
which is the live example used to design the generic v55 refresh evidence.

The immediately preceding 38-second attempt exposed a CLI parser safety bug:
placing `--config` before `run` was silently shadowed by the subcommand default,
so the example configuration was loaded and the quest director was absent. The
session was stopped before battle. The parser now preserves explicit config in
both positions and rejects conflicting duplicates; the full suite after this
fix is `546 passed` with independent review reporting no findings.

## Current Blocker

Autonomous questing remains **PARTIAL**. Exact quest `263` acceptance is proven
live, while its composite objective is not implemented or completed. Run
`4e5b3e3a00ed4a7b95fbdf441545227d` proves complete catalogue traversal,
bounded quarantine of quests
`236` and `267`, and scheduler continuation to routed quest `269`. It also
exposes the pre-v60 NPC-open false negative. Durable v61 NPC action journaling,
legacy recovery, restart zero-reissue, and systemic compare-and-swap rollback
are offline PASS, not live PASS.

The immediate external blocker is deployment: registered extension tabs still
run v59. A manual Chrome extension Reload is required because the extension
manager was blocked and idle self-update did not fire. Only after exact
v61/0.3.30 registration may the exact quest-269 legacy recovery be evaluated,
followed by a bounded live run proving journalled NPC action acknowledgement.

Terminal turn-in, additional composite/NPC/location objective executors, and
numeric before/after progress evidence remain open. Procurement currently has
an action-free exact-deficit policy and a read-only shop/auction observer only;
no purchase mutation has been implemented or proven. Combat also needs live
confirmation for stance-like slot 2 and the scoped readiness/cooldown path.
Inferred cooldown clicks remain disallowed.

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

Bridge v53 supplied the bounded quest-loop and navigator deadline work proven
by the two live cycles above. Bridge v54 adds observation only and has no live
acceptance evidence yet. Neither version executes arbitrary quest objective
shapes. This work must not displace the M1 live gate.

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
- `src/antibot_cv/automation/controller_cli.py`: CLI parsing plus guarded live
  command adaptation through `ActionExecutor`.
- `src/antibot_cv/automation/screen_runtime.py`: page synchronization.
- `src/antibot_cv/automation/leveling_runtime.py`: leveling and death flow.
- `src/antibot_cv/automation/navigation_runtime.py`: hunt and route execution.
- `src/antibot_cv/automation/combat_runtime.py`: combat and battle outcomes.
- `src/antibot_cv/automation/resource_runtime.py`: resource gates and resting.
- `src/antibot_cv/automation/resource_action_helpers.py`: page-bridge resource
  action polling helpers.
- `src/antibot_cv/automation/quest_active_catalog.py`: fail-closed active-list
  pagination.
- `src/antibot_cv/automation/quest_catalog_navigation.py` and
  `quest_catalog_recovery.py`: causal catalogue acknowledgement and bounded
  expired-stage recovery.
- `src/antibot_cv/automation/quest_available_eligibility.py`: pure mixed-card
  eligibility filtering before authoritative intake references are built.
- `src/antibot_cv/automation/quest_intake_runtime.py`: exact NPC acceptance
  state machine.
- `src/antibot_cv/automation/quest_acceptance_coordinator.py`: bounded intake
  handoff, catalogue acknowledgement, and fail-closed quarantine coordination.
- `src/antibot_cv/automation/quest_acceptance_settle.py`: pure causal
  catalogue-settle policy.
- `src/antibot_cv/automation/quest_intake_quarantine.py`: durable bounded
  quarantine evidence for exact intake references.
- `src/antibot_cv/automation/quest_npc_open_navigation.py`: durable causal
  exact-NPC-open mutation and acknowledgement state.
- `src/antibot_cv/automation/quest_npc_legacy_recovery.py`: read-only proof and
  bounded explicit checkpoint recovery for one recorded pre-v60 false negative.
- `src/antibot_cv/automation/quest_npc_action_journal.py`: durable exact
  `OPEN`/`ANSWER`/`ACCEPT` mutation journal, read-only settlement, restart
  zero-reissue, and strict schema/identity/expiry contracts.
- `src/antibot_cv/automation/quest_dialogue_runtime.py`: bounded dialogue
  objective state machine.
- `src/antibot_cv/automation/quest_dialogue_choice_policy.py`: conservative
  dialogue reply selection.
- `src/antibot_cv/automation/quest_chain_runtime.py`: persisted quest lease and
  terminal reconciliation.
- `src/antibot_cv/automation/quest_objective_router.py`: typed, fail-closed
  objective classification and capability routing.
- `src/antibot_cv/automation/quest_route_binding.py`: exact objective-to-route
  identity binding.
- `src/antibot_cv/automation/quest_chat_progress.py` and
  `quest_chat_progress_coordinator.py`: bounded collection-chat evidence and
  causal active-catalogue refresh.
- `src/antibot_cv/automation/quest_work_scheduler.py`: bounded deferred quest
  work scheduling.
- `src/antibot_cv/automation/gathering_activity_runtime.py`: pure gathering
  plan and inventory-progress policy.
- `src/antibot_cv/automation/gathering_node_policy.py`: action-free bounded
  gathering-node selection policy.
- `src/antibot_cv/automation/procurement_policy.py`: action-free exact-deficit
  procurement planning; it does not purchase resources.
- `src/antibot_cv/automation/spellbook_combat_adapter.py` and
  `combat_skill_mutation.py`: guarded spellbook decision adaptation and exact
  pre-mutation battle/skill binding.
- `src/antibot_cv/automation/world_registry.py`: bounded location and instance
  registry.
- `src/antibot_cv/automation/navigator_action_runtime.py`: explicit bounded
  navigator fallback policy.
- `src/antibot_cv/automation/quest_turnin_runtime.py`: pure completed-quest
  turn-in transaction and terminal-verification policy.
- `src/antibot_cv/automation/quest_turnin_coordinator.py`: controller handoff
  for route, exact NPC dialogue, and fresh active-list reconciliation.
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

## Latest Offline Validation

The current v61 snapshot passed exactly `968` tests and all `5` architecture
tests. Python compilation, JavaScript syntax checks for the generated bridge,
background, and content scripts, ten-module byte reproduction, bridge/extension
version agreement, authored-source line limits, and `git diff --check` also
passed. The generated bundle is `286292` bytes with SHA-256
`13b6fa8e0ec8efd3f70ee81864526afda9d3337c8aedf15c89013084c8a2ae73`.
All `101` authored sources stay at or below 1500 lines. Independent critic
review passed. These offline results do not promote the quest chain,
procurement, recovery, or action journal gates to live PASS.

## Commands

Start the macOS control server:

```bash
# From the repository root:
source .venv/bin/activate
python -m src.antibot_cv.automation.controller_cli control-server \
  --config config/automation.local.json \
  --live
```

Check bridge registration:

```bash
python -m src.antibot_cv.automation.controller_cli injector-status --timeout 5
```

Assess a recorded pre-v60 NPC-open false negative without changing checkpoint
state; add `--apply` only after the command returns an exact eligible proof and
the intended checkpoint/client have been independently verified:

```bash
python -m src.antibot_cv.automation.controller_cli recover-legacy-npc-open \
  --config config/automation.local.json \
  --client-id <exact-client-id> \
  --events runs/<session-id>/events.jsonl \
  --timeout 10
```

Run the test suite:

```bash
python -m pytest -q
```

Assess one recorded M1 recovery run without performing any game action:

```bash
python -m src.antibot_cv.automation.controller_cli assess-m1-recovery \
  --events runs/<session-id>/events.jsonl
```

`offline_ready: true` means the recorded phases are internally complete. The
formal M1 gate requires a trailing streak of three natural recovery passes;
the current verified streak is `1/3`.

## Next Session Checklist

1. Read this file, then `docs/DEVELOPMENT_LOG.md`.
   Use `docs/DEVELOPMENT_PLAN.md` to confirm the current milestone and avoid
   expanding scope before its exit gate passes.
2. Manually Reload the unpacked Chrome extension, then confirm bridge
   `2026-07-17-npc-action-journal-v61`, extension `0.3.30`, one primary game tab, and
   `version_ok: true`.
3. Evaluate the exact quest-269 legacy recovery proof without `--apply`; apply
   it only if every identity and snapshot precondition matches.
4. Run one bounded live quest-intake slice with explicit `--live` and telemetry
   to prove journalled NPC action acknowledgement without any restart reissue.
5. Do not claim composite quest `263`, procurement, or M1 recovery PASS unless
   the corresponding live evidence is actually recorded.

## Safety Boundary

Dry-run remains the default. Live actions require explicit `--live` and must go
through the safety guard/action sink. Do not add stealth, CAPTCHA bypass,
process-memory access, credential interception, packet interception, or
anti-cheat bypass behavior.
