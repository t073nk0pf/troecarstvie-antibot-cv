# Development Plan

Updated: 2026-07-13

## Product Result

Starting condition:

- the character already exists;
- the level 1 tutorial/test assignment is complete;
- the user sets a target level, for example level 9;
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

The project completed an architecture modularization gate before continuing
**Stage 1: survival and return after death**.

The former 5867-line Python controller is now a bounded orchestrator plus
screen, leveling/death, navigation, combat, resource, and CLI modules. The
former 4642-line editable page bridge is now generated from five bounded source
modules. Architecture tests prevent authored runtime files from growing beyond
1500 lines.

Foundation features already exist: per-tab browser control, state snapshots,
hunt scanning, target level filters, JS skill actions, resource thresholds,
inventory item actions, free resurrection, and compass route construction.

The modularization gate is enforced by automated architecture tests. Authored
runtime files remain below 1500 lines, while the Chrome bundle is reproducibly
generated from five ordered domain modules.

The main controller now executes every confirmed marked route transition,
preserves the destination across PvP or death, and waits for proven arrival
before hunt resumes. The route mechanics passed a manual live test and the
integrated controller loop is covered by automated tests. The remaining M1
gate is a full live death-to-return acceptance run, followed by three
consecutive natural recovery runs. The runtime now records a correlated
nine-phase recovery evidence chain, and a read-only offline validator
identifies missing or out-of-order phases from a run log. Offline readiness is
diagnostic only and does not satisfy the live exit gate.

The local v29 bridge also includes a bounded quest observation slice: active
and available cards, route labels, and explicit objective progress are parsed,
and a completed objective stops safely before turn-in. Quest acceptance and
turn-in remain outside the current milestone.

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

Exit gate: focused automated tests pass and one selected Chrome tab can be
controlled without affecting another tab.

### Stage 1 - Survival And Route Recovery

Status: implementation complete; live acceptance in progress. This remains the
current priority.

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

Status: partial observation/runtime slice exists; not the current blocker.

This module accelerates leveling but must use the stable travel, combat,
inventory, and death-recovery modules instead of duplicating them.

Responsibilities:

- load the list of available and active quests;
- parse eligibility, objective type, target, count, location, and reward;
- choose quests appropriate for the current level and configured policy;
- accept a quest through an exact page action;
- convert objectives into route/combat/inventory tasks;
- track progress and turn completed quests in;
- skip blocked, unsafe, unaffordable, or explicitly denied quests;
- re-plan when a quest target or location is unavailable.

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

1. Finish M1 route execution after death.
2. Revalidate the complete farm loop and sequential burdjuk recovery.
3. Stabilize adaptive skills and battle consumables.
4. Build the quest line module on top of those stable modules.
5. Add the level-goal planner and post-goal farm mode.
6. Run multi-window and long-duration hardening.

Do not begin broad quest automation while M1 is incomplete. A quest planner
cannot be reliable if the character cannot return to its objective after death.
