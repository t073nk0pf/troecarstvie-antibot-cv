"""Durable bounded journal operations for exact quest-local blockers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.antibot_cv.automation.quest_chain_persistence import (
    valid_authority_id,
    valid_capability,
    valid_phase,
    valid_reason,
)
from src.antibot_cv.automation.quest_chain_state import QuestLocalBlock


class LocalBlockEnsureState(str, Enum):
    REFRESH_REQUIRED = "refresh_required"
    STAGED = "staged"
    QUARANTINE_REQUIRED = "quarantine_required"


@dataclass(frozen=True)
class LocalBlockEnsureResult:
    state: LocalBlockEnsureState
    attempts: int
    counted: bool


class QuestLocalBlockJournalMixin:
    """Public local-block API mixed into the quest-chain orchestrator."""

    @staticmethod
    def _local_block_key(
        quest_id: str, fingerprint: str, phase: str, capability_version: str,
    ) -> tuple[str, str, str, str]:
        return (quest_id, fingerprint, phase, capability_version)

    def ensure_local_block(
        self,
        quest_id: str,
        quest_title: str,
        fingerprint: str,
        *,
        phase: str,
        capability_version: str,
        reason: str,
        authority_id: str,
        max_attempts: int = 2,
    ) -> LocalBlockEnsureResult:
        """Ensure durable evidence, then derive the required crash-safe next action."""

        values = tuple(str(value or "").strip() for value in (
            quest_id, quest_title, fingerprint, phase, capability_version, reason, authority_id,
        ))
        quest_id, quest_title, fingerprint, phase, capability_version, reason, authority_id = values
        if (
            not quest_id.isdecimal() or int(quest_id) <= 0 or not quest_title
            or not fingerprint or len(fingerprint) > 512 or not valid_phase(phase)
            or not valid_capability(capability_version) or not valid_reason(reason)
            or not valid_authority_id(authority_id)
            or isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
            or max_attempts <= 0 or max_attempts > 100
        ):
            raise ValueError("invalid quest local block evidence")
        if self.lease is not None and (
            self.lease.quest_id != quest_id
            or self.lease.quest_title != quest_title
            or self.lease.current_fingerprint != fingerprint
        ):
            raise RuntimeError("quest local block does not match pinned chain")
        key = self._local_block_key(quest_id, fingerprint, phase, capability_version)
        index = next((i for i, item in enumerate(self.local_blocks) if self._local_block_key(
            item.quest_id, item.fingerprint, item.phase, item.capability_version,
        ) == key), None)
        existing = self.local_blocks[index] if index is not None else None
        if existing is not None and existing.quest_title != quest_title:
            raise RuntimeError("quest local block title changed")
        counted = bool(
            (existing is None or authority_id not in existing.authority_ids)
            and (existing is None or existing.attempts < max_attempts)
        )
        if counted:
            authority_ids = (*existing.authority_ids, authority_id) if existing else (authority_id,)
            updated = QuestLocalBlock(
                quest_id, quest_title, fingerprint, phase, capability_version, reason,
                authority_ids, len(authority_ids),
            )
            previous = self.local_blocks
            if index is None:
                self.local_blocks = (*self.local_blocks, updated)[-self.max_local_blocks :]
            else:
                self.local_blocks = (
                    *self.local_blocks[:index], updated, *self.local_blocks[index + 1:]
                )
            try:
                self._persist()
            except Exception:
                self.local_blocks = previous
                raise
            existing = updated
        assert existing is not None
        if existing.attempts >= max_attempts:
            state = LocalBlockEnsureState.QUARANTINE_REQUIRED
        elif self.pending_active_catalog_navigation is not None:
            state = LocalBlockEnsureState.STAGED
        else:
            state = LocalBlockEnsureState.REFRESH_REQUIRED
        return LocalBlockEnsureResult(state, existing.attempts, counted)

    def clear_local_block(
        self, quest_id: str, fingerprint: str, *, phase: str, capability_version: str,
    ) -> bool:
        key = self._local_block_key(
            str(quest_id or "").strip(), str(fingerprint or "").strip(),
            str(phase or "").strip(), str(capability_version or "").strip(),
        )
        retained = tuple(item for item in self.local_blocks if self._local_block_key(
            item.quest_id, item.fingerprint, item.phase, item.capability_version,
        ) != key)
        if retained == self.local_blocks:
            return False
        previous = self.local_blocks
        self.local_blocks = retained
        try:
            self._persist_or_unlink()
        except Exception:
            self.local_blocks = previous
            raise
        return True

__all__ = [
    "LocalBlockEnsureResult", "LocalBlockEnsureState", "QuestLocalBlockJournalMixin",
]
