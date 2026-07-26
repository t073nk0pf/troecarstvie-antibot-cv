#!/usr/bin/env python3
"""Collect every NPC dialogue on the current location through the guarded API."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.antibot_cv.automation.npc_census_collector import NpcCensusCollector
from src.antibot_cv.automation.npc_census_http import LocalCensusHttpTransport
from src.antibot_cv.automation.npc_census_store import NpcCensusStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:17654")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--store", default="runs/npc_census.json")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("NPC census mutations require explicit --live")
    path = Path(args.store)
    if not path.parent.is_dir():
        parser.error("census store parent directory must exist")
    store = NpcCensusStore(path)
    store.load()
    collector = NpcCensusCollector(
        actor_key=args.client_id,
        transport=LocalCensusHttpTransport(
            server_url=args.server_url, client_id=args.client_id,
        ),
        store=store,
    )
    result = collector.collect_current_location()
    print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    return 0 if result.status.value == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
