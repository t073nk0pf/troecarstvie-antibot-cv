from __future__ import annotations

from tests.node_bridge_harness import run_bridge_cases


def test_harness_loads_generated_bridge_once_for_multiple_isolated_cases() -> None:
    results = run_bridge_cases({
        "version": 'if (!BRIDGE_SOURCE.includes("BRIDGE_VERSION")) throw new Error("missing bridge");',
        "isolated": 'globalThis.marker = 1; if (!BRIDGE_SOURCE.includes("source:")) throw new Error("missing sources");',
    })
    assert all(item["ok"] is True for item in results.values())


def test_harness_reports_an_individual_case_timeout() -> None:
    results = run_bridge_cases({"hung": "await new Promise(() => {});"}, timeout=1.0, case_timeout=0.05)

    assert results["hung"]["ok"] is False
    assert "timed out after 50ms" in str(results["hung"]["error"])
