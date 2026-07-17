import ast
from pathlib import Path

from scripts.build_page_bridge import MODULES, MODULE_DIR, OUTPUT, build


ROOT = Path(__file__).resolve().parents[1]
MAX_AUTHORED_SOURCE_LINES = 1500


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_authored_runtime_sources_stay_bounded() -> None:
    sources = list((ROOT / "src").rglob("*.py"))
    sources.extend(path for path in (ROOT / "browser_injector").glob("*.js") if path != OUTPUT)
    sources.extend(MODULE_DIR.glob("*.js"))

    oversized = {
        str(path.relative_to(ROOT)): line_count(path)
        for path in sources
        if line_count(path) > MAX_AUTHORED_SOURCE_LINES
    }
    assert oversized == {}, f"split oversized authored modules: {oversized}"


def test_page_bridge_bundle_matches_ordered_modules() -> None:
    assert tuple(path.name for path in sorted(MODULE_DIR.glob("*.js"))) == MODULES
    assert OUTPUT.read_text(encoding="utf-8") == build()


def _layer(module_name: str) -> str | None:
    stem = module_name.rsplit(".", 1)[-1]
    if stem == "quest_route_binding":
        return "helper"
    if stem.endswith("_runtime"):
        return "runtime"
    if stem.endswith("_policy"):
        return "policy"
    if stem.endswith("_helpers"):
        return "helper"
    if stem in {"controller", "controller_cli"}:
        return "orchestrator"
    return None


def test_domain_import_graph_points_away_from_orchestrators() -> None:
    automation = ROOT / "src" / "antibot_cv" / "automation"
    violations: list[str] = []
    for path in automation.glob("*.py"):
        source_layer = _layer(path.stem)
        if source_layer not in {"runtime", "policy", "helper"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.append(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
        for module_name in imported_modules:
            target_layer = _layer(module_name)
            forbidden = target_layer == "orchestrator" or (
                source_layer in {"policy", "helper"} and target_layer == "runtime"
            )
            if forbidden:
                violations.append(
                    f"{path.relative_to(ROOT)}:{source_layer}->{target_layer}:{module_name}"
                )
    assert violations == [], f"invalid domain dependency direction: {violations}"


READ_ONLY_INJECTOR_COMMANDS = {
    "area_npc_snapshot",
    "battle_snapshot",
    "hunt_bot_info",
    "hunt_candidates",
    "hunt_snapshot",
    "inspect_functions",
    "instance_entrance_snapshot",
    "inventory_snapshot",
    "layout",
    "layout_snapshot",
    "location_route_snapshot",
    "location_route_debug_snapshot",
    "navigator_snapshot",
    "npc_dialog_snapshot",
    "probe_page",
    "procurement_observation_snapshot",
    "resource_refresh",
    "resource_snapshot",
    "resource_model_capabilities",
    "gathering_node_snapshot",
    "state_snapshot",
    "visible_hunt_targets",
}


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in node.keywords if item.arg == name), None)


def _is_injector_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"injector", "server"}
    if isinstance(node, ast.Attribute) and node.attr == "injector":
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "global_browser_injector"
    )


def _injector_call_violations(
    source: str,
    filename: str,
    *,
    approved: bool = False,
    approved_handler: bool = False,
) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    parents: dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }

    def approved_sink_path(node: ast.AST) -> bool:
        if not approved and not approved_handler:
            return False
        current = parents.get(node)
        while current is not None:
            if approved and isinstance(current, ast.ClassDef) and current.name == "LiveMacActionSink":
                return True
            if (
                approved_handler
                and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef))
                and current.name == "handle_action"
            ):
                return True
            current = parents.get(current)
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if (
                not approved_sink_path(node)
                and isinstance(value, ast.Attribute)
                and value.attr == "execute"
                and _is_injector_receiver(value.value)
            ):
                violations.append(f"{filename}:{node.lineno}:aliased_injector_execute")
            continue
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        command_arg: ast.expr | None = None
        if node.func.attr == "execute" and _is_injector_receiver(node.func.value):
            command_arg = node.args[0] if node.args else _keyword(node, "command")
        elif node.func.attr == "_execute_injector":
            command_arg = node.args[1] if len(node.args) >= 2 else _keyword(node, "command")
        else:
            continue
        if approved_sink_path(node):
            continue
        if not isinstance(command_arg, ast.Constant) or not isinstance(command_arg.value, str):
            violations.append(f"{filename}:{node.lineno}:dynamic_injector_command")
        elif command_arg.value not in READ_ONLY_INJECTOR_COMMANDS:
            violations.append(f"{filename}:{node.lineno}:{command_arg.value}")
    return sorted(violations, key=lambda item: int(item.rsplit(":", 2)[-2]))


def test_mutating_injector_commands_stay_in_action_sink() -> None:
    # Only literal observation commands and UI-only layout may bypass the sink.
    automation = ROOT / "src" / "antibot_cv" / "automation"
    approved = automation / "action_live_sink.py"
    approved_handlers = {
        automation / "action_combat_handlers.py",
        automation / "action_navigation_handlers.py",
        automation / "action_quest_handlers.py",
        automation / "action_resource_handlers.py",
    }
    violations: list[str] = []
    for path in automation.glob("*.py"):
        violations.extend(
            _injector_call_violations(
                path.read_text(encoding="utf-8"),
                str(path.relative_to(ROOT)),
                approved=path == approved,
                approved_handler=path in approved_handlers,
            )
        )
    assert violations == [], f"injector call bypasses action sink policy: {violations}"


def test_injector_ast_policy_covers_keywords_dynamic_calls_and_noninjector_execute() -> None:
    source = """
server.execute(command="state_snapshot")
server.execute(command="attack_visible_bot")
server.execute(command_name)
self._execute_injector(injector, "open_area")
self._execute_injector(injector, command="open_hunt")
alias = self.injector.execute
action_executor.execute(request)
"""
    assert _injector_call_violations(source, "synthetic.py") == [
        "synthetic.py:3:attack_visible_bot",
        "synthetic.py:4:dynamic_injector_command",
        "synthetic.py:5:open_area",
        "synthetic.py:6:open_hunt",
        "synthetic.py:7:aliased_injector_execute",
    ]


def test_live_sink_file_approval_is_exact_class_path_not_blanket() -> None:
    source = """
class LiveMacActionSink:
    def mutate(self, injector):
        injector.execute("open_area")

def bypass(injector):
    injector.execute("open_area")
"""
    assert _injector_call_violations(source, "action_live_sink.py", approved=True) == [
        "action_live_sink.py:7:open_area",
    ]


def test_handler_approval_is_exact_function_not_blanket() -> None:
    source = """
def handle_action(sink, injector):
    sink._execute_injector(injector, "open_area")

def bypass(sink, injector):
    sink._execute_injector(injector, "open_area")
"""
    assert _injector_call_violations(
        source, "action_navigation_handlers.py", approved_handler=True
    ) == ["action_navigation_handlers.py:6:open_area"]


def test_controller_core_does_not_depend_on_cli_or_transport_facade() -> None:
    automation = ROOT / "src" / "antibot_cv" / "automation"
    controller_source = (automation / "controller.py").read_text(encoding="utf-8")
    service_source = (automation / "control_service.py").read_text(encoding="utf-8")

    assert "automation.controller_cli" not in controller_source
    assert "automation.control_server" not in controller_source
    assert "automation.controller_cli" not in service_source
    assert "automation.control_server" not in service_source
