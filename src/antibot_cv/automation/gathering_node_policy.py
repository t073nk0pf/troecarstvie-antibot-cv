"""Pure fail-closed planning from quest deficits to observed gather nodes.

This module consumes only read-only evidence. It never constructs an action
request and never invokes a browser/native gathering method.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Mapping, Sequence


class GatherExecutionStatus(str, Enum):
    OBSERVE = "observe"
    READY = "ready"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class AuthoritativeResourceDeficit:
    transport_client_id: str
    expected_character_name: str
    quest_id: str
    quest_fingerprint: str
    resource_name: str
    missing_count: int
    location: str
    resource_id: str | None = None
    normalized_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatheringCapabilitySnapshot:
    snapshot_id: str
    generated_at: str
    transport_client_id: str
    expected_character_name: str
    quest_id: str
    quest_fingerprint: str
    tools: frozenset[str]
    professions: frozenset[str]
    authoritative: bool = True
    requirements_observed: bool = False
    required_tool: str | None = None
    required_profession: str | None = None


@dataclass(frozen=True)
class GatherExecutionPlan:
    status: GatherExecutionStatus
    reason: str
    quest_id: str | None = None
    quest_fingerprint: str | None = None
    resource_name: str | None = None
    missing_count: int | None = None
    node_id: str | None = None
    node_name: str | None = None
    location: str | None = None
    native_method_name: str | None = None
    native_method_type: str | None = None
    ready: bool | None = None
    cooldown_remaining: float | None = None
    required_tool: str | None = None
    required_profession: str | None = None


def plan_gather_execution(
    deficit: AuthoritativeResourceDeficit,
    snapshot: Mapping[str, object],
    *,
    now: datetime,
    max_age_seconds: float = 15.0,
    capabilities: GatheringCapabilitySnapshot | None = None,
    max_capability_age_seconds: float = 30.0,
) -> GatherExecutionPlan:
    """Bind one exact authoritative deficit to one complete node observation."""

    invalid = _validate_deficit(deficit)
    if invalid:
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, invalid)
    base = {
        "quest_id": deficit.quest_id,
        "quest_fingerprint": deficit.quest_fingerprint,
        "resource_name": deficit.resource_name,
        "missing_count": deficit.missing_count,
        "location": deficit.location,
    }
    if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
        return GatherExecutionPlan(GatherExecutionStatus.OBSERVE, "node_snapshot_unavailable", **base)
    if snapshot.get("message") != "gathering_nodes_observed":
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "node_snapshot_kind_invalid", **base)
    if snapshot.get("actionable") is not False or snapshot.get("actionReason") != "read_only_discovery":
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "observer_actionability_invalid", **base)
    snapshot_id = snapshot.get("snapshotId")
    generated_at_raw = snapshot.get("generatedAt")
    generated_at = _timestamp(generated_at_raw)
    if not _valid_snapshot_identity(snapshot_id, generated_at_raw):
        return GatherExecutionPlan(GatherExecutionStatus.OBSERVE, "snapshot_identity_missing", **base)
    if generated_at is None or not isinstance(now, datetime) or now.tzinfo is None:
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "snapshot_timestamp_invalid", **base)
    age = (now.astimezone(timezone.utc) - generated_at).total_seconds()
    if age < -1.0:
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "snapshot_from_future", **base)
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds):
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "snapshot_max_age_invalid", **base)
    if age > max(0.0, float(max_age_seconds)):
        return GatherExecutionPlan(GatherExecutionStatus.OBSERVE, "node_snapshot_stale", **base)
    binding_reason = _snapshot_binding_reason(deficit, snapshot.get("binding"))
    if binding_reason:
        status = GatherExecutionStatus.OBSERVE if binding_reason == "snapshot_binding_missing" else GatherExecutionStatus.UNSAFE
        return GatherExecutionPlan(status, binding_reason, **base)
    scan = snapshot.get("scan")
    if not isinstance(scan, Mapping) or not isinstance(scan.get("truncated"), bool):
        return GatherExecutionPlan(GatherExecutionStatus.OBSERVE, "node_scan_incomplete", **base)
    if scan.get("truncated") is True:
        return GatherExecutionPlan(GatherExecutionStatus.OBSERVE, "node_scan_truncated", **base)
    raw_candidates = snapshot.get("candidates")
    if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "node_candidates_invalid", **base)
    if len(raw_candidates) > 100:
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "node_candidates_unbounded", **base)

    resource_matches: list[Mapping[str, object]] = []
    wrong_location = False
    for candidate in raw_candidates:
        if not isinstance(candidate, Mapping):
            return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "node_candidate_invalid", **base)
        name = candidate.get("nodeName")
        if not isinstance(name, str) or not _resource_identity_matches(deficit, candidate, name):
            continue
        location = candidate.get("location")
        if not isinstance(location, str) or _normalized(location) != _normalized(deficit.location):
            wrong_location = True
            continue
        resource_matches.append(candidate)

    if not resource_matches:
        reason = "matching_node_wrong_location" if wrong_location else "matching_node_not_observed"
        status = GatherExecutionStatus.UNSAFE if wrong_location else GatherExecutionStatus.OBSERVE
        return GatherExecutionPlan(status, reason, **base)
    if len(resource_matches) != 1:
        return GatherExecutionPlan(GatherExecutionStatus.UNSAFE, "matching_node_ambiguous", **base)

    candidate = resource_matches[0]
    parsed, invalid_reason = _parse_complete_candidate(candidate)
    if invalid_reason:
        status = GatherExecutionStatus.OBSERVE if invalid_reason == "node_evidence_incomplete" else GatherExecutionStatus.UNSAFE
        return GatherExecutionPlan(status, invalid_reason, **base)
    assert parsed is not None
    capability_reason = _capability_reason(
        parsed,
        capabilities,
        now=now,
        max_age_seconds=max_capability_age_seconds,
        snapshot_id=snapshot_id,
        generated_at=generated_at_raw,
        deficit=deficit,
    )
    if capability_reason:
        capability_status = (
            GatherExecutionStatus.UNSAFE
            if capability_reason in {
                "required_tool_unavailable",
                "required_profession_unavailable",
                "capability_snapshot_invalid",
                "capability_requirements_mismatch",
                "capability_binding_mismatch",
            }
            else GatherExecutionStatus.OBSERVE
        )
        return GatherExecutionPlan(capability_status, capability_reason, **base, **parsed)
    if parsed["ready"] is not True or parsed["cooldown_remaining"] != 0:
        return GatherExecutionPlan(
            GatherExecutionStatus.OBSERVE,
            "node_not_ready",
            **base,
            **parsed,
        )
    return GatherExecutionPlan(GatherExecutionStatus.READY, "unique_gather_node_ready", **base, **parsed)


def _validate_deficit(deficit: object) -> str | None:
    if not isinstance(deficit, AuthoritativeResourceDeficit):
        return "deficit_invalid"
    for value in (
        deficit.transport_client_id,
        deficit.expected_character_name,
        deficit.quest_id,
        deficit.quest_fingerprint,
        deficit.resource_name,
        deficit.location,
    ):
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 240:
            return "deficit_invalid"
    if isinstance(deficit.missing_count, bool) or not isinstance(deficit.missing_count, int) or deficit.missing_count <= 0:
        return "deficit_invalid"
    if deficit.resource_id is not None and (
        not isinstance(deficit.resource_id, str)
        or not deficit.resource_id
        or deficit.resource_id != deficit.resource_id.strip()
    ):
        return "deficit_invalid"
    if not isinstance(deficit.normalized_aliases, tuple):
        return "deficit_invalid"
    aliases = [_normalized(value) for value in deficit.normalized_aliases if isinstance(value, str)]
    if len(aliases) != len(deficit.normalized_aliases) or any(not value for value in aliases) or len(aliases) != len(set(aliases)):
        return "deficit_invalid"
    return None


def _parse_complete_candidate(candidate: Mapping[str, object]) -> tuple[dict[str, object] | None, str | None]:
    node_id = candidate.get("nodeId")
    node_name = candidate.get("nodeName")
    location = candidate.get("location")
    method = candidate.get("nativeMethod")
    method_candidates = candidate.get("methodCandidates")
    evidence = candidate.get("evidence")
    ready = candidate.get("ready")
    cooldown = candidate.get("cooldownRemaining")
    if (
        not isinstance(node_id, str) or not node_id.strip()
        or not isinstance(node_name, str) or not node_name.strip()
        or not isinstance(location, str) or not location.strip()
        or not isinstance(method, Mapping)
        or not isinstance(method_candidates, Sequence)
        or isinstance(method_candidates, (str, bytes))
        or len(method_candidates) != 1
        or not isinstance(evidence, Mapping)
        or evidence.get("complete") is not True
    ):
        return None, "node_evidence_incomplete"
    method_name = method.get("functionName")
    method_type = method.get("descriptorType")
    sole_method = method_candidates[0]
    if (
        not isinstance(method_name, str) or not method_name.strip()
        or method_type != "function"
        or not isinstance(sole_method, Mapping)
        or sole_method.get("functionName") != method_name
        or sole_method.get("descriptorType") != method_type
        or evidence.get("typedReady") is not True
        or evidence.get("typedCooldown") is not True
        or evidence.get("uniqueNativeMethod") is not True
        or evidence.get("toolRequirementObserved") is not True
        or evidence.get("professionRequirementObserved") is not True
        or not isinstance(ready, bool)
        or isinstance(cooldown, bool)
        or not isinstance(cooldown, (int, float))
        or not math.isfinite(float(cooldown))
        or cooldown < 0
    ):
        return None, "node_evidence_invalid"
    tool = candidate.get("requiredTool")
    profession = candidate.get("requiredProfession")
    if tool is not None and (not isinstance(tool, str) or not tool.strip()):
        return None, "node_tool_invalid"
    if profession is not None and (not isinstance(profession, str) or not profession.strip()):
        return None, "node_profession_invalid"
    return {
        "node_id": node_id,
        "node_name": node_name,
        "native_method_name": method_name,
        "native_method_type": method_type,
        "ready": ready,
        "cooldown_remaining": float(cooldown),
        "required_tool": tool,
        "required_profession": profession,
    }, None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_snapshot_identity(snapshot_id: object, generated_at: object) -> bool:
    if (
        not isinstance(snapshot_id, str)
        or not isinstance(generated_at, str)
        or not generated_at.strip()
        or len(snapshot_id) > 300
    ):
        return False
    prefix = f"gather-{generated_at}-"
    if not snapshot_id.startswith(prefix):
        return False
    sequence = snapshot_id[len(prefix):]
    return sequence.isascii() and sequence.isdigit() and 1 <= len(sequence) <= 6


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _resource_identity_matches(
    deficit: AuthoritativeResourceDeficit,
    candidate: Mapping[str, object],
    observed_name: str,
) -> bool:
    if deficit.resource_id is not None:
        candidate_id = candidate.get("resourceId")
        return isinstance(candidate_id, str) and candidate_id == deficit.resource_id
    aliases = {_normalized(deficit.resource_name), *(_normalized(value) for value in deficit.normalized_aliases)}
    return _normalized(observed_name) in aliases


def _capability_reason(
    candidate: Mapping[str, object],
    capabilities: GatheringCapabilitySnapshot | None,
    *,
    now: datetime,
    max_age_seconds: float,
    snapshot_id: str,
    generated_at: object,
    deficit: AuthoritativeResourceDeficit,
) -> str | None:
    required_tool = candidate.get("required_tool")
    required_profession = candidate.get("required_profession")
    if capabilities is None:
        return "capability_snapshot_required"
    if (
        not isinstance(capabilities, GatheringCapabilitySnapshot)
        or capabilities.authoritative is not True
        or capabilities.requirements_observed is not True
        or not isinstance(capabilities.snapshot_id, str)
        or not capabilities.snapshot_id.strip()
        or not isinstance(capabilities.generated_at, str)
        or _timestamp(capabilities.generated_at) is None
        or any(
            not isinstance(value, str) or not value.strip()
            for value in (
                capabilities.transport_client_id,
                capabilities.expected_character_name,
                capabilities.quest_id,
                capabilities.quest_fingerprint,
            )
        )
        or not isinstance(capabilities.tools, frozenset)
        or not isinstance(capabilities.professions, frozenset)
        or any(not isinstance(value, str) or not value.strip() for value in capabilities.tools | capabilities.professions)
        or isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or capabilities.required_tool is not None
        and (not isinstance(capabilities.required_tool, str) or not capabilities.required_tool.strip())
        or capabilities.required_profession is not None
        and (not isinstance(capabilities.required_profession, str) or not capabilities.required_profession.strip())
    ):
        return "capability_snapshot_invalid"
    if (
        capabilities.snapshot_id != snapshot_id
        or capabilities.generated_at != generated_at
        or capabilities.transport_client_id != deficit.transport_client_id
        or capabilities.expected_character_name != deficit.expected_character_name
        or capabilities.quest_id != deficit.quest_id
        or capabilities.quest_fingerprint != deficit.quest_fingerprint
    ):
        return "capability_binding_mismatch"
    capability_generated_at = _timestamp(capabilities.generated_at)
    assert capability_generated_at is not None
    age = (now.astimezone(timezone.utc) - capability_generated_at).total_seconds()
    if age < -1.0 or age > max(0.0, float(max_age_seconds)):
        return "capability_snapshot_stale"
    tools = {_normalized(value) for value in capabilities.tools}
    professions = {_normalized(value) for value in capabilities.professions}
    capability_tool = _normalized(capabilities.required_tool) if capabilities.required_tool is not None else None
    capability_profession = (
        _normalized(capabilities.required_profession)
        if capabilities.required_profession is not None
        else None
    )
    candidate_tool = _normalized(required_tool) if isinstance(required_tool, str) else None
    candidate_profession = _normalized(required_profession) if isinstance(required_profession, str) else None
    if capability_tool != candidate_tool or capability_profession != candidate_profession:
        return "capability_requirements_mismatch"
    if isinstance(required_tool, str) and _normalized(required_tool) not in tools:
        return "required_tool_unavailable"
    if isinstance(required_profession, str) and _normalized(required_profession) not in professions:
        return "required_profession_unavailable"
    return None


def _snapshot_binding_reason(deficit: AuthoritativeResourceDeficit, raw_binding: object) -> str | None:
    if not isinstance(raw_binding, Mapping) or raw_binding.get("complete") is not True:
        return "snapshot_binding_missing"
    request_scope = raw_binding.get("requestScope")
    expected = {
        "transportClientId": deficit.transport_client_id,
        "expectedCharacterName": deficit.expected_character_name,
        "observedCharacterName": deficit.expected_character_name,
        "characterStatus": "available",
    }
    for field, expected_value in expected.items():
        observed = raw_binding.get(field)
        if not isinstance(observed, str) or not observed.strip():
            return "snapshot_binding_missing"
        if observed != expected_value:
            return "snapshot_binding_mismatch"
    if not isinstance(request_scope, Mapping):
        return "snapshot_binding_missing"
    if request_scope.get("questId") != deficit.quest_id or request_scope.get("questFingerprint") != deficit.quest_fingerprint:
        return "snapshot_binding_mismatch"
    return None
