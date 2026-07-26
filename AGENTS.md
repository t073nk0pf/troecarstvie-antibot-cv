# Repository Instructions

This repository contains a local, visible, bounded CV automation harness for
generating controlled positive samples for anti-bot detector research.

Hard limits:
- Dry-run is the default.
- Live mode requires an explicit `--live` CLI flag.
- All actions go through the safety guard and action sink.
- The Chrome extension bridge is local-only and user-operated.
- Do not add stealth, anti-detection, CAPTCHA bypass, process-memory reading,
  packet interception, credential interception, or anti-cheat bypass logic.

Architecture limits:
- Do not add new behavior directly to a monolithic controller or bridge bundle.
- Every new domain capability must live in its own Python or JavaScript source
  module and be called by an orchestrator.
- Orchestrators may coordinate state and delegate work; they must not contain
  page parsing, combat rules, inventory rules, quest rules, or route algorithms.
- Domain source modules should normally contain 300-1500 lines. Small focused
  utilities may be shorter. Authored source files over 1500 lines must be split
  before the change is complete.
- Generated bundles, tests, fixtures, and migrations are exempt from the line
  limit, but their editable source modules are not.
- `browser_injector/page_bridge.js` is generated. Edit files under
  `browser_injector/page_bridge_modules/`, then run
  `python scripts/build_page_bridge.py`.
- Keep dependency direction one-way: controller/orchestrator -> domain runtime
  -> policy/helper. Domain modules must not import the controller or CLI.
- Add focused tests for a module's public behavior and keep the full regression
  suite passing after moves or boundary changes.

Context and verification efficiency:
- Start from `git status`, the current diff, and the smallest relevant source and
  test files. Do not recursively load `runs/`, `.venv/`, caches, generated
  `browser_injector/page_bridge.js`, or the full historical development log
  unless the task explicitly requires them.
- Treat `docs/DEVELOPMENT_LOG.md` as historical evidence, not startup context.
  Read targeted sections found with `rg`; prefer `docs/CURRENT_STATE.md` and the
  relevant architecture or plan section for current work.
- During implementation, run the exact affected test file or test node first.
  Run the relevant domain suite after a coherent change. Run the full regression
  suite only before handoff, after cross-domain/boundary changes, or when the
  focused suite is green. Do not repeatedly rerun an unchanged full suite.
- Reuse completed code exploration and critic findings when the working tree has
  not changed in that area. Re-audit only the changed paths and their consumers.
