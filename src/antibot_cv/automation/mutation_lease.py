"""Process-local fencing for live mutations of one logical browser actor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading


class MutationLeaseMode(str, Enum):
    EXCLUSIVE_RUN = "exclusive_run"
    TRANSIENT = "transient"


@dataclass(frozen=True, slots=True)
class ActorKey:
    profile_id: str
    tab_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("mutation profile identity is missing")
        if isinstance(self.tab_id, bool) or not isinstance(self.tab_id, int) or self.tab_id < 0:
            raise ValueError("mutation tab identity is invalid")


@dataclass(frozen=True, slots=True)
class MutationLease:
    target: ActorKey
    owner_id: str
    mode: MutationLeaseMode
    actor_generation: int
    fencing_token: int

    def fence_data(self) -> dict[str, object]:
        return {
            "profile_id": self.target.profile_id,
            "tab_id": self.target.tab_id,
            "actor_generation": self.actor_generation,
            "fencing_token": self.fencing_token,
        }


# Compatibility name for existing controller-run consumers.  New mutation
# boundaries should say ActorKey explicitly.
MutationTarget = ActorKey


class MutationLeaseCoordinator:
    """Fence controller runs and control-API mutations on a logical tab."""

    def __init__(self, *, generation_seed: int = 1) -> None:
        if (
            isinstance(generation_seed, bool)
            or not isinstance(generation_seed, int)
            or generation_seed < 1
        ):
            raise ValueError("mutation generation seed is invalid")
        self._lock = threading.RLock()
        self._generation_seed = generation_seed
        self._leases: dict[ActorKey, MutationLease] = {}
        self._actor_generations: dict[ActorKey, tuple[str, int]] = {}
        self._next_fence: dict[ActorKey, int] = {}

    def bind(self, target: ActorKey, client_id: str) -> int:
        """Return a stable generation for one transport incarnation."""

        normalized = str(client_id or "").strip()
        if not normalized:
            raise ValueError("mutation client identity is missing")
        with self._lock:
            current = self._actor_generations.get(target)
            if current is not None and current[0] == normalized:
                return current[1]
            # A reconnect cannot advance the actor epoch while an earlier
            # incarnation still owns a mutation that may have reached sink.
            if current is not None and target in self._leases:
                return current[1]
            generation = self._generation_seed if current is None else current[1] + 1
            self._actor_generations[target] = (normalized, generation)
            return generation

    def try_acquire(
        self,
        target: MutationTarget,
        owner_id: str,
        mode: MutationLeaseMode,
        *,
        actor_generation: int = 0,
    ) -> MutationLease | None:
        if not isinstance(target, MutationTarget) or not isinstance(mode, MutationLeaseMode):
            raise TypeError("typed mutation lease inputs are required")
        normalized_owner = str(owner_id or "").strip()
        if not normalized_owner:
            raise ValueError("mutation owner identity is missing")
        with self._lock:
            current_generation = self._actor_generations.get(target)
            if actor_generation and (
                current_generation is None or current_generation[1] != actor_generation
            ):
                return None
            if target in self._leases:
                return None
            fence = self._next_fence.get(target, 0) + 1
            self._next_fence[target] = fence
            lease = MutationLease(target, normalized_owner, mode, actor_generation, fence)
            self._leases[target] = lease
            return lease

    def release(self, lease: MutationLease) -> bool:
        if not isinstance(lease, MutationLease):
            raise TypeError("typed mutation lease is required")
        with self._lock:
            if self._leases.get(lease.target) != lease:
                return False
            del self._leases[lease.target]
            return True

    def holder(self, target: MutationTarget) -> MutationLease | None:
        with self._lock:
            return self._leases.get(target)

    def is_current(self, lease: MutationLease) -> bool:
        """Validate the exact actor generation and fencing token."""

        if not isinstance(lease, MutationLease):
            return False
        with self._lock:
            current_generation = self._actor_generations.get(lease.target)
            return (
                self._leases.get(lease.target) == lease
                and (
                    lease.actor_generation == 0
                    or current_generation is not None
                    and current_generation[1] == lease.actor_generation
                )
            )

    def matches_fence(self, value: object) -> bool:
        if not isinstance(value, dict) or set(value) != {
            "profile_id", "tab_id", "actor_generation", "fencing_token",
        }:
            return False
        if isinstance(value["tab_id"], bool) or isinstance(value["actor_generation"], bool) \
                or isinstance(value["fencing_token"], bool):
            return False
        try:
            target = ActorKey(str(value["profile_id"]), int(value["tab_id"]))
            generation = int(value["actor_generation"])
            token = int(value["fencing_token"])
        except (TypeError, ValueError):
            return False
        with self._lock:
            lease = self._leases.get(target)
            return (
                lease is not None
                and lease.actor_generation == generation
                and lease.fencing_token == token
                and self.is_current(lease)
            )


__all__ = [
    "ActorKey", "MutationLease", "MutationLeaseCoordinator", "MutationLeaseMode", "MutationTarget",
]
