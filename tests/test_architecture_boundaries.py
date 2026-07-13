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


def test_domain_runtimes_do_not_import_orchestrator_or_cli() -> None:
    forbidden = (
        "src.antibot_cv.automation.controller import",
        "src.antibot_cv.automation.controller_cli import",
    )
    violations: list[str] = []
    for path in (ROOT / "src" / "antibot_cv" / "automation").glob("*_runtime.py"):
        source = path.read_text(encoding="utf-8")
        if any(value in source for value in forbidden):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == [], f"domain modules depend on orchestrator: {violations}"
