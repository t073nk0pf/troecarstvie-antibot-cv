"""Durable zero-reissue journal for NPC census endpoint inspection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile

from src.antibot_cv.automation.npc_census_live import CensusInspectContract


_MAX_JOURNAL_BYTES = 262_144
_MAX_RECORDS = 64
_MAX_PROFILE_ID = 256
_MAX_TAB_ID = 9_007_199_254_740_991


class CensusJournalCommitUncertain(OSError):
    """The rename was visible, but directory durability was not confirmed."""

    retain_mutation_lease = True


@dataclass(frozen=True, slots=True)
class PendingCensusInspection:
    profile_id: str
    tab_id: int
    contract: CensusInspectContract
    phase: str = "pending"
    dialogue_json: str | None = None

    def __post_init__(self) -> None:
        if self.phase not in {"pending", "reconciled"}:
            raise ValueError("NPC census inspect phase is invalid")
        if self.phase == "pending" and self.dialogue_json is not None:
            raise ValueError("pending NPC census inspection has dialogue")
        if self.phase == "reconciled":
            if not isinstance(self.dialogue_json, str) or not self.dialogue_json \
                    or len(self.dialogue_json.encode("utf-8")) > 131_072:
                raise ValueError("reconciled NPC census dialogue is invalid")

    @property
    def actor_key(self) -> tuple[str, int]:
        return self.profile_id, self.tab_id


class NpcCensusInspectJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: dict[tuple[str, int], PendingCensusInspection] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size > _MAX_JOURNAL_BYTES:
            raise ValueError("NPC census inspect journal exceeds hard cap")
        with self.path.open("rb") as handle:
            encoded = handle.read(_MAX_JOURNAL_BYTES + 1)
        if len(encoded) > _MAX_JOURNAL_BYTES:
            raise ValueError("NPC census inspect journal exceeds hard cap")
        raw = json.loads(encoded.decode("utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "records"}:
            raise ValueError("NPC census inspect journal schema is invalid")
        if type(raw["schemaVersion"]) is not int or raw["schemaVersion"] != 1 \
                or not isinstance(raw["records"], list) or len(raw["records"]) > _MAX_RECORDS:
            raise ValueError("NPC census inspect journal is invalid")
        records: dict[tuple[str, int], PendingCensusInspection] = {}
        for item in raw["records"]:
            if not isinstance(item, dict) or set(item) != {
                "profile_id", "tab_id", "contract", "phase", "dialogue_json",
            }:
                raise ValueError("NPC census inspect record is invalid")
            profile_id = item["profile_id"]
            tab_id = item["tab_id"]
            contract_raw = item["contract"]
            if not isinstance(profile_id, str) or not profile_id or len(profile_id) > _MAX_PROFILE_ID \
                    or profile_id != profile_id.strip() or type(tab_id) is not int \
                    or not 0 <= tab_id <= _MAX_TAB_ID:
                raise ValueError("NPC census inspect actor is invalid")
            if not isinstance(contract_raw, dict):
                raise ValueError("NPC census inspect contract is invalid")
            contract = CensusInspectContract(**contract_raw)
            record = PendingCensusInspection(
                profile_id, tab_id, contract, item["phase"], item["dialogue_json"],
            )
            if record.actor_key in records:
                raise ValueError("NPC census inspect actor is duplicated")
            records[record.actor_key] = record
        self._records = records

    def get(self, actor_key: tuple[str, int]) -> PendingCensusInspection | None:
        return self._records.get(actor_key)

    def records(self) -> tuple[PendingCensusInspection, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: item.actor_key))

    def stage(self, record: PendingCensusInspection) -> None:
        if (
            not isinstance(record, PendingCensusInspection)
            or not record.profile_id or len(record.profile_id) > _MAX_PROFILE_ID
            or record.profile_id != record.profile_id.strip()
            or type(record.tab_id) is not int or not 0 <= record.tab_id <= _MAX_TAB_ID
        ):
            raise ValueError("NPC census inspect record is invalid")
        existing = self._records.get(record.actor_key)
        if existing is not None and existing != record:
            raise ValueError("NPC census inspect journal CAS conflict")
        if existing is None and len(self._records) >= _MAX_RECORDS:
            raise ValueError("NPC census inspect journal record cap reached")
        candidate = dict(self._records)
        candidate[record.actor_key] = record
        try:
            self._save(candidate)
        except CensusJournalCommitUncertain:
            # Sink must not run, but the current process must retain a barrier.
            self._records = candidate
            raise
        self._records = candidate

    def mark_reconciled_exact(
        self, record: PendingCensusInspection, dialogue_snapshot: dict[str, object],
    ) -> PendingCensusInspection:
        if self._records.get(record.actor_key) != record or record.phase != "pending":
            raise ValueError("NPC census inspect reconcile CAS conflict")
        try:
            dialogue_json = json.dumps(
                dialogue_snapshot, ensure_ascii=False, sort_keys=True,
                allow_nan=False, separators=(",", ":"),
            )
            detached = json.loads(dialogue_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("NPC census inspect dialogue is invalid") from exc
        if not isinstance(detached, dict):
            raise ValueError("NPC census inspect dialogue is invalid")
        reconciled = PendingCensusInspection(
            record.profile_id, record.tab_id, record.contract,
            "reconciled", dialogue_json,
        )
        candidate = dict(self._records)
        candidate[record.actor_key] = reconciled
        self._save(candidate)
        self._records = candidate
        return reconciled

    def clear_exact(self, record: PendingCensusInspection) -> bool:
        if self._records.get(record.actor_key) != record:
            return False
        candidate = dict(self._records)
        candidate.pop(record.actor_key)
        self._save(candidate)
        self._records = candidate
        return True

    def _save(self, records: dict[tuple[str, int], PendingCensusInspection]) -> None:
        if not self.path.parent.is_dir():
            raise ValueError("NPC census inspect journal parent is missing")
        payload = {
            "schemaVersion": 1,
            "records": [
                {
                    "profile_id": record.profile_id,
                    "tab_id": record.tab_id,
                    "contract": asdict(record.contract),
                    "phase": record.phase,
                    "dialogue_json": record.dialogue_json,
                }
                for record in sorted(records.values(), key=lambda item: item.actor_key)
            ],
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_JOURNAL_BYTES:
            raise ValueError("NPC census inspect journal exceeds hard cap")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                try:
                    os.fsync(directory_fd)
                except OSError as exc:
                    raise CensusJournalCommitUncertain(
                        "NPC census journal commit durability is uncertain"
                    ) from exc
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
