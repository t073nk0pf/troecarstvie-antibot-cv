# Runtime Architecture

Updated: 2026-07-13

## Design Rule

The system is a set of bounded domain modules coordinated by thin
orchestrators. New behavior belongs to the relevant domain module or to a new
module. It must not be appended to `controller.py`, `controller_cli.py`, or the
generated browser bridge bundle.

Authored Python and JavaScript files have a hard ceiling of 1500 lines. Domain
modules normally target 300-1500 lines; small policies and utilities may be
shorter. `tests/test_architecture_boundaries.py` enforces the ceiling and the
browser bundle reproducibility contract.

## Python Runtime

- `controller.py`: public compatibility facade, session construction, frame
  dispatch, and run lifecycle.
- `screen_runtime.py`: screen synchronization and generic page recovery.
- `leveling_runtime.py`: leveling observations, quest intent, death recovery,
  and checkpoint decisions.
- `quest_runtime.py`: quest snapshot handoff and guarded catalogue refresh
  orchestration.
- `quest_catalog.py`: fail-closed paginated catalogue validation and
  aggregation.
- `quest_director_policy.py` / `quest_director_runtime.py`: action-free quest
  scheduling policy and its observed state.
- `navigation_runtime.py`: hunt target selection, compass child-window flow,
  route execution, direction and scrollbar search.
- `combat_runtime.py`: battle synchronization, combat policy execution, skills,
  battle items, victory exit, and return to hunt.
- `resource_runtime.py`: health/prowess observations, resting, refresh, and
  between-battle recovery.
- `controller_cli.py`: CLI commands and argument parsing only.
- `runtime_helpers.py`: pure normalization and CV helper functions.
- `runtime_constants.py`: shared runtime constants without behavior.
- `telemetry/m1_recovery.py`: pure ordered recovery-evidence validation; it
  performs no game actions and cannot close the live acceptance gate.

The mixins preserve the existing `AutomationController` API while keeping each
domain independently inspectable. A later change may replace mixins with
composed services, but behavior should first migrate behind an explicit domain
interface with regression coverage.

## Browser Runtime

Editable page-bridge sources are ordered modules under
`browser_injector/page_bridge_modules/`:

- `00_core_combat.js`: bridge primitives, page inspection, battle model and
  battle actions.
- `10_hunt_inventory.js`: hunt/resource model and inventory recovery actions.
- `20_hunt_actions.js`: hunt candidates, movement, target attack, and page
  opening actions.
- `30_navigation_death.js`: page classification, compass routes, player state,
  death detection, and resurrection.
- `40_state_layout_dispatch.js`: aggregate state, optional layout controls, and
  command dispatch.

`scripts/build_page_bridge.py` wraps these ordered modules into
`browser_injector/page_bridge.js`. The bundle is committed because Chrome loads
it directly, but it must never be edited manually.

## Dependency Direction

```text
control_server / controller_cli
              |
              v
      controller orchestrator
              |
              v
 domain runtime modules (screen, leveling, navigation, combat, resources)
              |
              v
 policies, action sink, injector client, pure helpers
```

The browser follows the same rule: `content.js` transports commands, the
generated bridge dispatches them, and ordered domain source modules implement
page behavior.

## Adding A Capability

1. Choose the owning domain or create a focused new module.
2. Keep page facts separate from policy decisions and actions.
3. Route every live action through the existing guarded action sink.
4. Add focused tests for the new module.
5. Rebuild the browser bundle when a page module changes.
6. Run the architecture test and full regression suite.
