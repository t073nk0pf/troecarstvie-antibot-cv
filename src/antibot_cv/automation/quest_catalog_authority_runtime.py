"""Runtime state for causal active-catalogue turn-in authority.

This mixin owns only the semantic authority accumulator and its transitions.
The quest orchestrator remains responsible for navigation, catalogue ingestion,
logging, and deciding how to handle an unsafe diagnostic.
"""

from __future__ import annotations

import time
from typing import Any

from src.antibot_cv.automation.quest_catalog_authority import (
    ActiveCatalogAuthorityAccumulator,
)
from src.antibot_cv.automation.quest_compiler import (
    QuestCompileStatus,
    compile_quest_plan,
)


class QuestCatalogAuthorityRuntimeMixin:
    """Build and seal causal authority for the semantic vertical slice."""

    def _init_quest_catalog_authority_runtime(self) -> None:
        self._quest_active_catalog_authority_accumulator = None
        self._quest_active_catalog_authority = None

    def _invalidate_quest_active_catalog_authority(self) -> None:
        self._quest_active_catalog_authority_accumulator = None
        self._quest_active_catalog_authority = None

    def _record_quest_active_catalog_authority_page(
        self,
        *,
        page: int,
        pending_navigation: Any,
        evidence: Any,
        quest_data: dict[str, object],
        now: float | None = None,
    ) -> str | None:
        """Record one settled active page or return a fail-closed diagnostic."""

        if self.config.leveling.quest_engine_mode == "legacy":
            return None
        if pending_navigation is None:
            return "quest_active_catalog_authority_navigation_missing"
        if page == 0:
            self._quest_active_catalog_authority = None
            previous_context = getattr(self, "_quest_semantic_evaluation_context", None)
            previous_baseline = str(
                getattr(previous_context, "causal_baseline", "") or ""
            ).strip()
            self._quest_active_catalog_authority_accumulator = (
                ActiveCatalogAuthorityAccumulator(
                    causal_baseline=pending_navigation.baseline_snapshot_id,
                    causal_ancestors=(previous_baseline,) if previous_baseline else (),
                    max_age_seconds=max(
                        1.0,
                        self.config.leveling.snapshot_stale_timeout_ms / 1000,
                    ),
                    max_pages=max(
                        1, int(self.config.leveling.quest_catalog_max_pages)
                    ),
                )
            )
        accumulator = self._quest_active_catalog_authority_accumulator
        if accumulator is None or evidence is None:
            return "quest_active_catalog_authority_sequence_missing"
        accumulator.ingest(
            pending_navigation,
            evidence,
            quest_data,
            now=time.time() if now is None else now,
        )
        return None

    def _seal_quest_active_catalog_authority(self) -> None:
        """Bind a complete causal catalogue to the exact semantic quest lease."""

        self._quest_active_catalog_authority = None
        if self.config.leveling.quest_engine_mode == "legacy":
            return
        director = self._quest_director
        accumulator = self._quest_active_catalog_authority_accumulator
        if director is None or accumulator is None or not accumulator.complete:
            raise ValueError("active catalogue authority is incomplete")
        lease = director.chain.lease
        if lease is None or lease.quest_id not in {"280", "304"}:
            return
        matches = tuple(
            entry for entry in accumulator.observation.entries
            if entry.id == lease.quest_id
        )
        if not matches:
            # Terminal-removal evidence cannot authorize a new turn-in.
            return
        if len(matches) != 1 or matches[0].title != lease.quest_title:
            raise ValueError("active catalogue authority quest identity mismatch")
        compiled = compile_quest_plan(matches[0])
        if compiled.status is not QuestCompileStatus.READY or compiled.plan is None:
            diagnostic = compiled.diagnostics[0] if compiled.diagnostics else "unknown"
            raise ValueError(f"active catalogue authority compile failed:{diagnostic}")
        if not lease.current_fingerprint:
            raise ValueError("active catalogue authority lease fingerprint missing")
        self._quest_active_catalog_authority = accumulator.seal(
            plan_fingerprint=compiled.plan.fingerprint,
            lease_fingerprint=lease.current_fingerprint,
            catalog_revision=director.active_catalog.revision,
        )
