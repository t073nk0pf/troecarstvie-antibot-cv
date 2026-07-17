from datetime import datetime, timedelta, timezone
import json
import subprocess

from scripts.build_page_bridge import build

from src.antibot_cv.automation.gathering_node_policy import (
    AuthoritativeResourceDeficit,
    GatherExecutionStatus,
    GatheringCapabilitySnapshot,
    plan_gather_execution,
)


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)


def deficit(**updates: object) -> AuthoritativeResourceDeficit:
    values: dict[str, object] = {
        "transport_client_id": "client-main",
        "expected_character_name": "character-5",
        "quest_id": "246",
        "quest_fingerprint": "246:stage-2:abc",
        "resource_name": "Вьюнка узколистного",
        "missing_count": 7,
        "location": "Зелёная опушка",
        "normalized_aliases": ("Вьюнок узколистный",),
    }
    values.update(updates)
    return AuthoritativeResourceDeficit(**values)  # type: ignore[arg-type]


def node(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "nodeId": "herb-19",
        "nodeName": "Вьюнок узколистный",
        "location": "Зелёная опушка",
        "nativeMethod": {
            "property": "gather",
            "descriptorType": "function",
            "functionName": "gather",
            "owner": "instance",
        },
        "methodCandidates": [{
            "property": "gather",
            "descriptorType": "function",
            "functionName": "gather",
            "owner": "instance",
        }],
        "ready": True,
        "cooldownRemaining": 0,
        "requiredTool": "Серп",
        "requiredProfession": "Травник",
        "evidence": {
            "complete": True,
            "typedReady": True,
            "typedCooldown": True,
            "uniqueNativeMethod": True,
            "toolRequirementObserved": True,
            "professionRequirementObserved": True,
        },
    }
    value.update(updates)
    return value


def snapshot(*nodes: object, generated_at: datetime = NOW) -> dict[str, object]:
    return {
        "ok": True,
        "message": "gathering_nodes_observed",
        "snapshotId": f"gather-{generated_at.isoformat()}-1",
        "actionable": False,
        "actionReason": "read_only_discovery",
        "generatedAt": generated_at.isoformat(),
        "binding": {
            "complete": True,
            "transportClientId": "client-main",
            "expectedCharacterName": "character-5",
            "observedCharacterName": "character-5",
            "characterStatus": "available",
            "requestScope": {"questId": "246", "questFingerprint": "246:stage-2:abc"},
        },
        "scan": {"visited": 4, "candidateCount": len(nodes), "truncated": False},
        "candidates": list(nodes),
    }


def capabilities(**updates: object) -> GatheringCapabilitySnapshot:
    generated_at = updates.get("generated_at", NOW.isoformat())
    values: dict[str, object] = {
        "snapshot_id": f"gather-{generated_at}-1",
        "generated_at": generated_at,
        "transport_client_id": "client-main",
        "expected_character_name": "character-5",
        "quest_id": "246",
        "quest_fingerprint": "246:stage-2:abc",
        "tools": frozenset({"Серп"}),
        "professions": frozenset({"Травник"}),
        "authoritative": True,
        "requirements_observed": True,
        "required_tool": "Серп",
        "required_profession": "Травник",
    }
    values.update(updates)
    return GatheringCapabilitySnapshot(**values)  # type: ignore[arg-type]


def test_unique_case_inflected_node_with_complete_typed_evidence_is_ready() -> None:
    plan = plan_gather_execution(deficit(), snapshot(node()), now=NOW, capabilities=capabilities())

    assert plan.status is GatherExecutionStatus.READY
    assert plan.reason == "unique_gather_node_ready"
    assert plan.node_id == "herb-19"
    assert plan.node_name == "Вьюнок узколистный"
    assert plan.missing_count == 7
    assert plan.native_method_name == "gather"
    assert plan.required_tool == "Серп"


def test_actual_dispatch_snapshot_contract_is_accepted_end_to_end() -> None:
    source = build()
    script = f'''
const vm = require("vm");
const source = {json.dumps(source)};
const messages = [];
const listeners = {{}};
function element(tag, className, text) {{
  return {{
    tagName: tag, id: "", className, textContent: text, innerText: text, parentElement: null,
    getAttribute(name) {{ return name === "class" ? className : null; }},
    querySelector() {{ return null; }}, querySelectorAll() {{ return []; }},
  }};
}}
const hp = element("DIV", "b-control-lvl__hp", "Жизнь 100%");
const mp = element("DIV", "b-control-lvl__mp", "Удаль 100%");
const control = element("DIV", "b-control-lvl", "5 v3g45 Жизнь 100% Удаль 100% Опыт 12.5%");
hp.parentElement = control; mp.parentElement = control;
const mainDocument = {{
  title: "Охота", scripts: [], body: {{innerText: control.innerText, textContent: control.textContent}},
  documentElement: {{innerHTML: control.innerText}},
  querySelector(selector) {{
    if (selector.includes("control-lvl__hp")) return hp;
    if (selector.includes("control-lvl__mp")) return mp;
    if (selector.includes("control-lvl")) return control;
    return null;
  }},
  querySelectorAll(selector) {{ return selector === "*" ? [control, hp, mp] : []; }},
}};
const mainWin = {{name:"main", location:{{href:"https://3kingdoms.ru/hunt.php"}}, frames:[], document:mainDocument}};
const mainFrame = {{name:"main_frame", location:{{href:"https://3kingdoms.ru/main_frame.php"}}, frames:[mainWin], document:mainDocument}};
mainFrame.frames.main = mainWin;
const node = {{
  nodeId:"herb-19", resourceName:"Вьюнок узколистный", locationName:"Зелёная опушка",
  kind:"resource herb", ready:true, cooldownRemaining:0,
  requiredTool:null, requiredProfession:null, gather() {{}},
}};
const root = {{
  name:"top", location:{{href:"https://3kingdoms.ru/main.php"}}, frames:[mainFrame],
  document:mainDocument, setTimeout, clearTimeout,
  addEventListener(type, callback) {{ listeners[type] = callback; }},
  removeEventListener() {{}}, postMessage(message) {{ messages.push(message); }},
  getHuntApp() {{ return {{model:{{nodes:[node]}}}}; }},
}};
root.frames.main_frame = mainFrame; root.top = root; root.window = root;
vm.runInNewContext(source, {{window:root, console, setTimeout, clearTimeout, Date, Set, Map, Math}});
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
listeners.message({{source:root, data:{{
  source:`antibot-cv-content:${{version}}`, token:"contract",
  command:{{type:"gathering_node_snapshot", payload:{{
    transport:{{clientId:"client-main"}},
    metadata:{{expectedCharacterName:"v3g45", questId:"246", questFingerprint:"246:stage-2:abc"}},
  }}}},
}}}});
if (messages.length !== 1 || messages[0].ok !== true) process.exit(2);
process.stdout.write(messages[0].message);
'''
    completed = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=True)
    observed = json.loads(completed.stdout)
    generated_at = datetime.fromisoformat(observed["generatedAt"].replace("Z", "+00:00"))
    observed_node = observed["candidates"][0]
    plan = plan_gather_execution(
        deficit(expected_character_name="v3g45"),
        observed,
        now=generated_at,
        capabilities=capabilities(
            snapshot_id=observed["snapshotId"],
            generated_at=observed["generatedAt"],
            tools=frozenset(),
            professions=frozenset(),
            expected_character_name="v3g45",
            required_tool=observed_node["requiredTool"],
            required_profession=observed_node["requiredProfession"],
        ),
    )
    assert observed["snapshotId"] == f'gather-{observed["generatedAt"]}-1'
    assert (plan.status, plan.reason) == (GatherExecutionStatus.READY, "unique_gather_node_ready")


def test_stale_snapshot_requests_observation_without_reusing_node() -> None:
    plan = plan_gather_execution(
        deficit(),
        snapshot(node(), generated_at=NOW - timedelta(seconds=16)),
        now=NOW,
        max_age_seconds=15,
    )

    assert plan.status is GatherExecutionStatus.OBSERVE
    assert plan.reason == "node_snapshot_stale"
    assert plan.node_id is None


def test_matching_resource_in_wrong_location_is_unsafe() -> None:
    plan = plan_gather_execution(
        deficit(),
        snapshot(node(location="Туманная низина")),
        now=NOW,
    )

    assert plan.status is GatherExecutionStatus.UNSAFE
    assert plan.reason == "matching_node_wrong_location"


def test_multiple_matching_nodes_are_ambiguous_even_if_one_is_not_ready() -> None:
    plan = plan_gather_execution(
        deficit(),
        snapshot(node(), node(nodeId="herb-20", ready=False, cooldownRemaining=4)),
        now=NOW,
    )

    assert plan.status is GatherExecutionStatus.UNSAFE
    assert plan.reason == "matching_node_ambiguous"


def test_derived_item_is_not_accepted_as_resource_inflection() -> None:
    plan = plan_gather_execution(
        deficit(resource_name="Тисса", normalized_aliases=()),
        snapshot(node(nodeName="Тиссовый экстракт")),
        now=NOW,
    )

    assert plan.status is GatherExecutionStatus.OBSERVE
    assert plan.reason == "matching_node_not_observed"


def test_incomplete_method_or_readiness_evidence_only_observes() -> None:
    incomplete = node(nativeMethod=None, evidence={"complete": False})

    plan = plan_gather_execution(deficit(), snapshot(incomplete), now=NOW)

    assert plan.status is GatherExecutionStatus.OBSERVE
    assert plan.reason == "node_evidence_incomplete"


def test_explicit_cooldown_keeps_plan_non_actionable() -> None:
    plan = plan_gather_execution(
        deficit(),
        snapshot(node(ready=False, cooldownRemaining=2.5)),
        now=NOW,
        capabilities=capabilities(),
    )

    assert plan.status is GatherExecutionStatus.OBSERVE
    assert plan.reason == "node_not_ready"
    assert plan.cooldown_remaining == 2.5


def test_snapshot_claiming_actionability_is_rejected() -> None:
    observed = snapshot(node())
    observed["actionable"] = True

    plan = plan_gather_execution(deficit(), observed, now=NOW)

    assert plan.status is GatherExecutionStatus.UNSAFE
    assert plan.reason == "observer_actionability_invalid"


def test_truncated_scan_cannot_claim_a_unique_node() -> None:
    observed = snapshot(node())
    observed["scan"] = {"visited": 800, "candidateCount": 1, "truncated": True}

    plan = plan_gather_execution(deficit(), observed, now=NOW)

    assert plan.status is GatherExecutionStatus.OBSERVE
    assert plan.reason == "node_scan_truncated"


def test_prefix_similar_resource_names_do_not_match() -> None:
    plan = plan_gather_execution(
        deficit(resource_name="Осинка", normalized_aliases=()),
        snapshot(node(nodeName="Осинец", requiredTool=None, requiredProfession=None)),
        now=NOW,
    )

    assert plan.status is GatherExecutionStatus.OBSERVE
    assert plan.reason == "matching_node_not_observed"


def test_exact_resource_id_takes_priority_over_similar_name() -> None:
    observed = node(
        resourceId="item-42",
        nodeName="Каноническое имя узла",
        requiredTool=None,
        requiredProfession=None,
    )

    plan = plan_gather_execution(
        deficit(resource_id="item-42", normalized_aliases=()),
        snapshot(observed),
        now=NOW,
        capabilities=capabilities(required_tool=None, required_profession=None),
    )

    assert plan.status is GatherExecutionStatus.READY
    assert plan.node_id == "herb-19"


def test_declared_tool_and_profession_require_fresh_authoritative_capability() -> None:
    missing = plan_gather_execution(deficit(), snapshot(node()), now=NOW)
    stale_at = NOW - timedelta(seconds=31)
    stale = plan_gather_execution(
        deficit(),
        snapshot(node(), generated_at=stale_at),
        now=NOW,
        max_age_seconds=60,
        capabilities=capabilities(generated_at=stale_at.isoformat()),
    )
    no_tool = plan_gather_execution(
        deficit(),
        snapshot(node()),
        now=NOW,
        capabilities=capabilities(tools=frozenset()),
    )

    assert (missing.status, missing.reason) == (GatherExecutionStatus.OBSERVE, "capability_snapshot_required")
    assert (stale.status, stale.reason) == (GatherExecutionStatus.OBSERVE, "capability_snapshot_stale")
    assert (no_tool.status, no_tool.reason) == (GatherExecutionStatus.UNSAFE, "required_tool_unavailable")


def test_explicit_no_requirement_still_requires_fresh_capability_confirmation() -> None:
    observed = node(requiredTool=None, requiredProfession=None)
    missing = plan_gather_execution(deficit(), snapshot(observed), now=NOW)
    confirmed = plan_gather_execution(
        deficit(),
        snapshot(observed),
        now=NOW,
        capabilities=capabilities(required_tool=None, required_profession=None),
    )
    mismatched = plan_gather_execution(
        deficit(),
        snapshot(observed),
        now=NOW,
        capabilities=capabilities(required_tool="Серп", required_profession=None),
    )

    assert (missing.status, missing.reason) == (GatherExecutionStatus.OBSERVE, "capability_snapshot_required")
    assert confirmed.status is GatherExecutionStatus.READY
    assert (mismatched.status, mismatched.reason) == (
        GatherExecutionStatus.UNSAFE,
        "capability_requirements_mismatch",
    )


def test_missing_or_mismatched_actor_and_quest_binding_cannot_be_ready() -> None:
    missing_snapshot = snapshot(node())
    missing_snapshot.pop("binding")
    missing = plan_gather_execution(deficit(), missing_snapshot, now=NOW, capabilities=capabilities())

    for field, other in (
        ("transportClientId", "other-client"),
        ("observedCharacterName", "other-character"),
    ):
        observed = snapshot(node())
        binding = dict(observed["binding"])  # type: ignore[arg-type]
        binding[field] = other
        observed["binding"] = binding
        plan = plan_gather_execution(deficit(), observed, now=NOW, capabilities=capabilities())
        assert (plan.status, plan.reason) == (GatherExecutionStatus.UNSAFE, "snapshot_binding_mismatch")

    for scope in (
        {"questId": "999", "questFingerprint": "246:stage-2:abc"},
        {"questId": "246", "questFingerprint": "246:other-stage"},
    ):
        observed = snapshot(node())
        binding = dict(observed["binding"])  # type: ignore[arg-type]
        binding["requestScope"] = scope
        observed["binding"] = binding
        plan = plan_gather_execution(deficit(), observed, now=NOW, capabilities=capabilities())
        assert (plan.status, plan.reason) == (GatherExecutionStatus.UNSAFE, "snapshot_binding_mismatch")

    unavailable = snapshot(node())
    unavailable_binding = dict(unavailable["binding"])  # type: ignore[arg-type]
    unavailable_binding.update({"complete": False, "characterStatus": "partial", "observedCharacterName": None})
    unavailable["binding"] = unavailable_binding
    unavailable_plan = plan_gather_execution(deficit(), unavailable, now=NOW, capabilities=capabilities())
    assert (unavailable_plan.status, unavailable_plan.reason) == (
        GatherExecutionStatus.OBSERVE,
        "snapshot_binding_missing",
    )

    assert (missing.status, missing.reason) == (GatherExecutionStatus.OBSERVE, "snapshot_binding_missing")


def test_capability_binding_must_equal_same_snapshot_actor_and_quest() -> None:
    for update in (
        {"snapshot_id": "other-snapshot"},
        {"generated_at": "2026-07-17T08:00:00Z"},
        {"transport_client_id": "other-client"},
        {"expected_character_name": "other-character"},
        {"quest_id": "999"},
        {"quest_fingerprint": "246:other-stage"},
    ):
        plan = plan_gather_execution(
            deficit(),
            snapshot(node()),
            now=NOW,
            capabilities=capabilities(**update),
        )
        assert (plan.status, plan.reason) == (GatherExecutionStatus.UNSAFE, "capability_binding_mismatch")


def test_future_snapshot_and_missing_snapshot_id_cannot_be_ready() -> None:
    future = plan_gather_execution(
        deficit(),
        snapshot(node(), generated_at=NOW + timedelta(seconds=2)),
        now=NOW,
        capabilities=capabilities(generated_at=(NOW + timedelta(seconds=2)).isoformat()),
    )
    missing_id_snapshot = snapshot(node())
    missing_id_snapshot.pop("snapshotId")
    missing_id = plan_gather_execution(
        deficit(),
        missing_id_snapshot,
        now=NOW,
        capabilities=capabilities(),
    )

    assert (future.status, future.reason) == (GatherExecutionStatus.UNSAFE, "snapshot_from_future")
    assert (missing_id.status, missing_id.reason) == (GatherExecutionStatus.OBSERVE, "snapshot_identity_missing")


def test_malformed_timestamp_and_snapshot_id_timestamp_mismatch_fail_closed() -> None:
    malformed_snapshot = snapshot(node())
    malformed_snapshot["generatedAt"] = "not-a-timestamp"
    malformed_snapshot["snapshotId"] = "gather-not-a-timestamp-1"
    malformed = plan_gather_execution(
        deficit(),
        malformed_snapshot,
        now=NOW,
        capabilities=capabilities(),
    )
    mismatched_id_snapshot = snapshot(node())
    mismatched_id_snapshot["snapshotId"] = "gather-2026-07-17T07:59:59+00:00-1"
    mismatched_id = plan_gather_execution(
        deficit(),
        mismatched_id_snapshot,
        now=NOW,
        capabilities=capabilities(),
    )

    assert (malformed.status, malformed.reason) == (GatherExecutionStatus.UNSAFE, "snapshot_timestamp_invalid")
    assert (mismatched_id.status, mismatched_id.reason) == (
        GatherExecutionStatus.OBSERVE,
        "snapshot_identity_missing",
    )
