# Runtime Architecture

Updated: 2026-07-17

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
- `quest_catalog_navigation.py`: causal open/page acknowledgement state for
  available-catalogue traversal.
- `quest_catalog_recovery.py`: bounded action-free recovery decisions for
  expired catalogue stages.
- `quest_available_eligibility.py`: pure mixed-card eligibility filtering that
  keeps ineligible display cards out of authoritative intake references.
- `quest_active_catalog.py`: fail-closed paginated active-quest validation and
  aggregation.
- `quest_intake_runtime.py`: exact giver/dialogue/accept orchestration and
  active-list postcondition.
- `quest_acceptance_coordinator.py`: controller-facing intake phases, causal
  catalogue acknowledgement, and quarantine handoff.
- `quest_acceptance_settle.py`: pure bounded settle decisions for acknowledgement
  and post-accept catalogue evidence.
- `quest_intake_quarantine.py`: durable bounded, action-free quarantine records
  for exact intake references.
- `quest_npc_open_navigation.py`: durable causal state for one exact NPC-open
  mutation, acknowledgement, expiry, and compare-and-swap settlement.
- `quest_npc_legacy_recovery.py`: strict read-only proof construction for one
  recorded pre-v60 NPC-open false negative; checkpoint application remains an
  explicit CLI operation.
- `quest_npc_action_journal.py`: durable pre-dispatch journal for exact
  `OPEN`/`ANSWER`/`ACCEPT`, causal read-only settlement, restart zero-reissue,
  and strict expiry/identity/schema contracts.
- `quest_giver.py`: conservative catalogue-giver to area-NPC matching.
- `quest_director_policy.py` / `quest_director_runtime.py`: action-free quest
  scheduling policy and its observed state.
- `quest_objective_runtime.py`: pure fail-closed parsing, selection, and
  refresh comparison for typed active-quest objectives.
- `quest_objective_router.py`: typed objective classification, capability
  versioning, and fail-closed unsupported/composite decisions.
- `quest_route_binding.py`: exact objective fingerprint and route identity
  binding.
- `quest_chat_progress.py` / `quest_chat_progress_coordinator.py`: bounded chat
  evidence and causal active-catalogue refresh coordination.
- `quest_chain_runtime.py`: durable leases, fingerprints, completion recovery,
  and bounded objective quarantine.
- `quest_turnin_runtime.py` / `quest_turnin_coordinator.py`: pure terminal
  reconciliation plus controller-facing route/NPC handoff.
- `navigation_runtime.py`: hunt target selection, compass child-window flow,
  route execution, direction and scrollbar search.
- `combat_runtime.py`: battle synchronization, combat policy execution, skills,
  battle items, victory exit, and return to hunt.
- `spellbook_policy.py`: action-free typed spell semantics and fail-closed
  priority planning from explicit readiness/cooldown evidence.
- `spellbook_combat_adapter.py` / `combat_skill_mutation.py`: guarded policy
  adaptation and exact snapshot/token-bound skill mutation metadata.
- `gathering_activity_runtime.py` / `gathering_node_policy.py`: action-free
  gathering progress and bounded node selection policy.
- `procurement_policy.py`: action-free, exact-deficit shop/auction planning from
  bound observations. Purchase mutation is not implemented or claimed.
- `resource_runtime.py`: health/prowess observations, resting, refresh, and
  between-battle recovery.
- `resource_action_helpers.py`: isolated page-bridge resource polling helpers.
- `controller_cli.py`: CLI parsing and a shared guarded adapter for explicit
  live commands; it does not call mutating injector commands directly. Its
  `recover-legacy-npc-open` command is proof-only by default and applies the
  bounded checkpoint recovery only with explicit `--apply`.
- `runtime_helpers.py`: pure normalization and CV helper functions.
- `runtime_constants.py`: shared runtime constants without behavior.
- `telemetry/m1_recovery.py`: pure ordered recovery-evidence validation; it
  performs no game actions and cannot close the live acceptance gate.

The mixins preserve the existing `AutomationController` API while keeping each
domain independently inspectable. A later change may replace mixins with
composed services, but behavior should first migrate behind an explicit domain
interface with regression coverage.

NPC intake mutations use a two-phase durable rule: persist the exact action,
dispatch once, persist `ACK_PENDING`, then settle from newer causal evidence.
Unknown delivery is never retried. Only explicit `NOT_ISSUED` may clear a staged
mutation. All journal and related dialogue/lease updates use checkpoint
compare-and-swap semantics; write failure restores the full prior in-memory
state. This contract is offline-proven through v61 and still requires bounded
live evidence after the extension is reloaded.

## Browser Runtime

Editable page-bridge sources are ordered modules under
`browser_injector/page_bridge_modules/`:

- `00_core_combat.js`: bridge primitives, page inspection, battle model and
  battle actions.
- `10_hunt_inventory.js`: hunt/resource model and inventory recovery actions.
- `20_hunt_actions.js`: hunt candidates, movement, target attack, and page
  opening actions.
- `21_gathering_activity.js`: bounded, descriptor-only gathering observations;
  it exposes no gathering action.
- `30_navigation_death.js`: page classification, compass routes, player state,
  death detection, and resurrection.
- `32_instance_actions.js`: bounded instance discovery and entry actions.
- `35_npc_quests.js`: area NPC discovery and snapshot-bound quest dialogue
  actions.
- `36_quest_chat_progress.js`: bounded read-only observation of exact server
  collection-progress messages; it performs no quest actions.
- `37_shop_observer.js`: bounded read-only observations on the exact
  `/shop.php` and `/auction.php` paths. Client provenance is transport-attested,
  actor identity is independently observed from `playerSnapshot`, and quest
  identity remains an explicit requested scope; the module exposes no purchase
  action.
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

The browser follows the same rule: `content.js` transports commands and
overwrites caller-supplied client provenance with the registered tab client ID;
the generated bridge dispatches them, and ordered domain source modules
implement page behavior.

## Adding A Capability

1. Choose the owning domain or create a focused new module.
2. Keep page facts separate from policy decisions and actions.
3. Route every live action through the existing guarded action sink.
4. Add focused tests for the new module.
5. Rebuild the browser bundle when a page module changes.
6. Run the architecture test and full regression suite.
