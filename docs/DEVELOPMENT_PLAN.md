# Development Plan

Updated: 2026-07-17

## Product Result

Starting condition:

- the character already exists;
- the level 1 tutorial/test assignment is complete;
- the user sets a target level, for example level 20;
- the user defines spending and safety limits.

Target behavior:

1. inspect the character, available quests, skills, inventory, location, and
   resources;
2. choose suitable quests and complete their objectives;
3. find required locations or monsters through the compass;
4. travel through every marked transition while respecting transition timers;
5. fight allowed targets, use available skills, and adapt the skill policy as
   stronger abilities unlock;
6. restore health and prowess between battles and use allowlisted battle
   consumables when required;
7. recover from death and return to the interrupted location and activity;
8. continue until the configured level is reached;
9. after reaching the target, switch to a simple farm loop:
   `hunt -> target -> battle -> exit -> recover -> hunt`.

## Current Stage

The project completed an architecture modularization gate and is advancing the
current bounded quest-autonomy slice while retaining the open Stage 1 live
recovery gate.

The former 5867-line Python controller is now a bounded orchestrator plus
screen, leveling/death, navigation, combat, resource, and CLI modules. The
former 4642-line editable page bridge is now generated from ten bounded source
modules. Architecture tests prevent authored runtime files from growing beyond
1500 lines.

Foundation features already exist: per-tab browser control, state snapshots,
hunt scanning, target level filters, JS skill actions, resource thresholds,
inventory item actions, free resurrection, and compass route construction.

The modularization gate is enforced by automated architecture tests. Authored
runtime files remain below 1500 lines, while the Chrome bundle is reproducibly
generated from ten ordered domain modules.

The main controller now executes every confirmed marked route transition,
preserves the destination across PvP or death, and waits for proven arrival
before hunt resumes. The route mechanics and one integrated natural
death-to-return run passed live on bridge v36, and the controller loop is
covered by automated tests. The remaining M1 gate is two more consecutive
natural recovery runs, bringing the verified trailing streak from `1/3` to
`3/3`. The runtime records a correlated
nine-phase recovery evidence chain, and a read-only offline validator
identifies missing or out-of-order phases from a run log. Offline readiness is
diagnostic only and does not satisfy the live exit gate.

The local v61 bridge includes bounded quest observation and exact NPC intake:
active and available cards are parsed across all pages; the bot can travel to a
unique giver, open the exact quest, click `Взять задание`, and acknowledge
success only after the quest ID appears in a fresh active catalogue. The first
typed objective executor selects and routes to an exact same-or-lower-level
monster from the complete active catalogue and refreshes progress after every
victory and resurrection. It stops after a bounded ten victories without
observable progress. A bounded dialogue executor now handles one exact NPC and
snapshot-bound progression actions, with fresh active-catalogue verification
and fail-closed handling for ambiguous or malformed reply alternatives.
Completed supported steps now enter a bounded turn-in coordinator. Intake
persists an authoritative reference before mutation, exact giver/dialogue
actions remain snapshot-bound, and a newer complete active catalogue either
releases a terminal quest or advances the same pinned multi-step chain. Legacy
leases without an authoritative reference remain fail-closed. The intake giver
reference is fingerprint-scoped and is not reused for a later chain step; that
step needs new authoritative giver/location evidence before another turn-in.
Pre-mutation completion evidence and the resulting terminal/continuation
reconciliation survive restart. Persisted quest leases, a bounded work
scheduler, world/instance routing, and deferred gathering groundwork are also
present offline. Gathering node discovery and a complete gathering mutation
loop are not yet implemented.

A bounded two-cycle v53 live run now proves the repeated monster-quest loop:
complete active-catalogue refresh, pinned objective selection, exact Navigator
handoff, target attack, battle, exit, and the same sequence again without
manual input. All configured combat slots 1-5 are selected. Slots 1, 3, 4, and
5 have current-character live confirmation; slot 2 (`Смена позиции I`) still
fails closed because the bridge has no reliable post-action signal for this
stance-like skill. Numeric quest-progress telemetry and live terminal turn-in
remain open acceptance evidence.

The spellbook-aware policy is connected through a guarded adapter. It
distinguishes instant setup, conditional defense, and turn-consuming attacks,
prefers the strongest explicitly ready attack, and remembers confirmed setup
within each battle. Complete identity and authoritative readiness/turn evidence
are mandatory; eight unknown observations stop unsafe.

The current status is **PARTIAL**. Exact dialogue selection and acceptance of
quest `263` are live-confirmed. Run `4e5b3e3a00ed4a7b95fbdf441545227d`
proved complete available/active catalogues, durable local-dialog quarantine for
quest `236`, bounded giver-not-observed quarantine for quest `267`, and
scheduler continuation plus routing for quest `269`. Its final exact NPC open
returned a false-negative postcondition even though the intended dialogue was
later observed open.

v61 journals exact `OPEN`, `ANSWER`, and final `ACCEPT` before dispatch. Restart
settles from read-only causal evidence with zero reissue; expiry, exact
client/profile/tab and quest/NPC/action identity, canonical schema, unique
successor evidence, complete active-catalogue proof, and CAS rollback are
covered by P0/P1/P2 tests. Durable NPC acknowledgement, legacy recovery,
mixed-card eligibility, and expired-stage recovery also pass offline. The
independent critic passed.

Registered extension tabs remain on v59, so manual extension Reload and exact
v61/0.3.30 registration are required before the next bounded live slice. The
exact quest-269 legacy recovery must be evaluated before a bounded journal live
proof. The composite three-NPC objective of quest `263` has not been executed.
Procurement remains observation and action-free exact-deficit planning only;
no purchase mutation is implemented or proven.

## Main Current Milestone

### M1 - Death Recovery And Return To Farm

Required sequence:

1. While alive, save the exact semantic location and current activity.
2. Detect death and stop inventory/hunt actions immediately.
3. Select only a confirmed free and allowlisted resurrection option.
4. Confirm that the character is alive again.
5. Close the resurrection notice.
6. Restore resources if the post-revive policy requires it.
7. If the character is not in the saved location, open the compass child
   window and select the saved location or a known monster from that location.
8. Build the route and return focus to `main.php`.
9. Activate exactly one compass-marked transition when its timer is zero.
10. Poll the area state, wait for the next transition timer, and repeat.
11. Confirm arrival by semantic location, not by elapsed time alone.
12. Resume the interrupted farm activity and open hunt.

Acceptance criteria:

- no inventory use while the character is dead;
- no hunt action before resurrection and arrival are confirmed;
- no coordinate clicks for route execution;
- route steps use the native page model/JS bridge;
- 3, 4, and 5 second transition timers are handled by polling;
- an incoming battle pauses travel and travel resumes afterward;
- a missing or ambiguous transition fails closed and writes one useful error;
- one complete live sequence ends in the original farming location with hunt
  active;
- three consecutive natural death recoveries complete without manual input.

## Delivery Stages

### Stage 0 - Runtime Foundation

Status: mostly complete.

- local Python control server;
- unpacked Chrome extension and version handshake;
- per-tab `client_id` routing;
- explicit dry-run/live boundary;
- guarded action sink and telemetry;
- popup configuration and start/stop controls.
- idle-only bridge self-update with one-shot reload and retry backoff
  (implemented; exact-tab automatic refresh still needs dedicated live proof).

Exit gate: focused automated tests pass and one selected Chrome tab can be
controlled without affecting another tab.

### Stage 1 - Survival And Route Recovery

Status: implementation complete; live acceptance is at `1/3` consecutive
natural recoveries. This remains an open formal safety gate alongside the
bounded quest-autonomy slice; it is not a live PASS.

- death detection;
- free resurrection;
- resurrection notice close;
- last alive semantic location checkpoint;
- compass route construction;
- marked transition discovery and execution (implemented and tested);
- timer polling (implemented and tested);
- arrival confirmation using saved semantic name/ID (implemented and tested);
- resume after battle or death interruption (implemented and tested).

Exit gate: all M1 acceptance criteria pass.

### Stage 2 - Stable Farm Loop

Status: partial implementation exists.

A bounded 2026-07-13 live acceptance run passed one full cycle with strict
target filtering and explicit victory evidence. The 100-cycle soak gate remains
open.

- scan current hunt candidates every cycle;
- select only allowed monster names and levels;
- avoid hostile/red or otherwise disallowed targets;
- enter battle and verify that it started;
- use selected skills through JS;
- detect victory, exit, and return to hunt;
- use health/prowess recovery items sequentially between battles;
- recover from stale pages without infinite reload loops.

Exit gate: 100 consecutive farm cycles or the configured time limit complete
without manual input, resource starvation, or an unintended target.

### Stage 3 - Adaptive Combat And Consumables

Status: basic skill and item controls exist; policy is incomplete.

The current rotation covers every configured skill slot, but stance-like
actions need a typed confirmation contract before an attempted slot can be
reported as successfully used. The live two-cycle evidence currently confirms
slots 1, 3, 4, and 5 and rejects unconfirmed slot 2.

- scan all currently available skills and their readiness;
- store skill cost, cooldown, and observed effectiveness when available;
- prefer stronger efficient skills as the character level increases;
- retain the zero-cost attack only as an explicit low-prowess fallback;
- use allowlisted health, prowess, and damage consumables during battle;
- enforce per-battle quantity, cooldown, and spending limits;
- prevent simultaneous or duplicate item consumption.

Exit gate: the bot completes battles at several character levels without a
hard-coded fixed skill set and never uses a non-allowlisted item.

### Stage 4 - Quest Line Module

Status: **PARTIAL**. Catalogue discovery, scheduling, travel-to-giver, exact
NPC acceptance, and quest-263 dialogue selection are implemented and
live-confirmed. Exact monster-objective
selection, route handoff, preferred-quest intake, the first dialogue executor,
and the first fingerprint-scoped completed-step turn-in are integrated offline.
Persisted pinned chains, bounded objective/intake quarantines, and deferred work
scheduling preserve ownership across refreshes. Causal catalogue ACK,
mixed-card eligibility, finite/client validation, and expired-stage recovery
are proven offline. Durable `OPEN`/`ANSWER`/`ACCEPT` journaling, restart
zero-reissue, systemic compare-and-swap rollback, and legacy false-negative
recovery are also offline PASS. Their combined live proof and other objective
types are next.

This module accelerates leveling but must use the stable travel, combat,
inventory, and death-recovery modules instead of duplicating them.

Responsibilities:

- load all pages of available quests and the active list; implemented;
- parse eligibility, objective type, target, count, location, and reward;
- choose a supported exact monster objective at or below the current level;
  implemented offline;
- accept a quest through an exact page action; implemented and live-confirmed;
- convert objectives into route/combat/inventory tasks; exact monster route and
  combat targeting implemented offline;
- track progress and turn completed steps in; full refresh after each monster
  victory and dialogue progression plus bounded terminal/next-step turn-in
  reconciliation implemented offline;
- skip blocked, unsafe, unaffordable, or explicitly denied quests;
- quarantine `stop_unsafe` work durably without cancelling the quest, then
  continue with another eligible quest when the catalogue remains authoritative;
- re-plan when a quest target or location is unavailable.

Current guarded boundary: discovery produces an ordered intake queue, resolves
one unique giver, performs one snapshot-bound acceptance action at a time, and
confirms acceptance from the complete active list. It can then select one exact
monster-hunt or dialogue step, route through the shared navigation runtime, and
re-read all active pages after each verified mutation. Dialogue alternatives
must have one conservative progression choice; malformed or ambiguous choices
fail closed. Completed supported steps may be submitted to the exact persisted
giver and must reconcile against a newer complete active list. Unknown or
unsupported steps stop before a new mutation and are preserved in a bounded
durable quarantine. The fallback farm intent is allowed only after a fresh
empty catalogue and active list.

Procurement is deliberately not an execution claim: the current policy can
compute an exact quest deficit from bounded, identity-bound shop/auction
observations, but there is no guarded purchase mutation or live purchase proof.

Exit gate: complete a configured level-range quest chain from a clean level 1
post-tutorial character without manual navigation.

### Stage 5 - Leveling Planner

Status: planned.

- take `target_level` as the primary goal;
- estimate whether quests or farming give better safe progress;
- alternate quest and farm tasks;
- re-evaluate after level-up, death, inventory changes, or unlocked skills;
- stop leveling actions exactly at the target level;
- switch to configured post-goal farm mode.

Exit gate: reach level 9 from the level 1 post-tutorial starting point under
the configured safety and spending limits.

### Stage 6 - Product Hardening

Status: planned.

- multi-window soak tests;
- persisted per-character plans and checkpoints;
- structured error reasons and recovery budgets;
- bounded log retention;
- automatic bridge/version diagnostics;
- Windows and macOS installation checks;
- operator-visible current objective, location, resources, and last action;
- explicit stop conditions for deaths, spending, time, and repeated failures.

Exit gate: an interrupted run can restart safely from its checkpoint and long
runs remain observable and bounded.

## Module Boundaries

- **State reader:** page facts only; no actions.
- **Planner:** chooses the next high-level objective.
- **Route executor:** compass, marked transitions, timers, arrival.
- **Combat executor:** target selection, battle entry, skills, exit.
- **Resource manager:** health/prowess checks and allowlisted consumables.
- **Death recovery:** revive, close notice, restore checkpoint.
- **Quest engine:** quest selection and objective decomposition.
- **Safety policy:** live permission, spending limits, action allowlists, stop
  conditions.

Page interaction should remain deterministic JS. An AI agent may help classify
unfamiliar quest text, compare leveling strategies, or analyze failure logs,
but it must not bypass the guarded action interface or invent page actions.

## Priority Order

1. Manually Reload the extension and confirm v61/0.3.30 on exactly one primary
   game tab. Evaluate the exact quest-269 legacy recovery proof, apply only on
   exact identity, then prove one bounded journal acknowledgement with zero
   repeated mutation.
2. Add typed executors for the composite quest-263 NPC/location objective and
   prove them one bounded mutation at a time.
3. Complete the remaining two consecutive M1 natural recovery validations.
4. Revalidate the complete farm loop, adaptive skills, and sequential resource
   recovery.
5. Add guarded exact-deficit procurement only behind explicit spending limits,
   then obtain a separate bounded live proof.
6. Add the level-goal planner and multi-window/long-duration hardening.

Quest work must continue to reuse recovery, route, combat, and resource
services. Neither offline readiness nor a partial live slice closes the M1 or
full autonomous-chain exit gates.
