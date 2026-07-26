# Test lanes

The default development lane is deliberately bounded: it excludes the
transport and slow Node-backed cases.  No command enables xdist; browser ports,
run directories and checkpoints must remain isolated first.

| Lane | Command | Use |
| --- | --- | --- |
| exact | `make test-exact T=tests/test_quest_inventory_guard.py::test_inventory_completion_resolves_short_inflected_trophy_to_lynx_ear` | one affected assertion |
| fast | `make test-fast` | ordinary local feedback; excludes `transport` and `slow` |
| domain | `make test-domain` | policy/runtime changes |
| bridge | `make test-bridge` | generated bridge and source modules |
| transport | `make test-transport` | injector/control-server boundaries |
| full | `make test-full` | handoff/regression, reports 30 slowest tests |

Use `pytest --durations=30` whenever comparing a lane before and after an
optimization.  Transport and Node tests must set an explicit subprocess
timeout; the shared Node harness work owns the remaining migration.
