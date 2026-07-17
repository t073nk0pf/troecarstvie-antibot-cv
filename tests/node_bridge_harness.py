"""One-process Node runner for independent bridge VM cases."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Mapping


def run_bridge_cases(
    cases: Mapping[str, str], *, timeout: float = 10.0, case_timeout: float = 5.0,
) -> dict[str, dict[str, object]]:
    """Run isolated VM cases with both process- and individual-case deadlines."""
    if timeout <= 0 or case_timeout <= 0:
        raise ValueError("Node harness timeouts must be positive")
    payload = "".join(
        json.dumps({"id": key, "script": value, "timeoutMs": round(case_timeout * 1000)}) + "\n"
        for key, value in cases.items()
    )
    result = subprocess.run(
        ["node", "tests/node_bridge_harness.js"], input=payload, text=True,
        capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or "node bridge harness failed")
    parsed = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    values = {str(item["id"]): item for item in parsed}
    if set(values) != set(cases):
        raise RuntimeError("node bridge harness returned incomplete cases")
    return values
