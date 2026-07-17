from __future__ import annotations

import subprocess
from textwrap import dedent


def test_gathering_observer_captures_typed_evidence_without_invoking_method_or_getter() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        let invoked = 0;
        let getterRead = 0;
        const node = {
          nodeId: "herb-19",
          resourceName: "Вьюнок узколистный",
          locationName: "Зелёная опушка",
          kind: "resource herb",
          ready: true,
          cooldownRemaining: 0,
          requiredTool: "Серп",
          requiredProfession: "Травник",
          gather() { invoked += 1; },
        };
        Object.defineProperty(node, "dangerous", { enumerable: true, get() { getterRead += 1; throw new Error("read"); } });
        const model = {
          clientId: "client-main",
          characterId: "character-5",
          questId: "246",
          questFingerprint: "246:stage-2:abc",
          activities: [node],
        };
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const observe = factory(
          () => ({ model }),
          (value, limit) => String(value == null ? "" : value).slice(0, limit),
          "test-v1",
        );

        const result = observe({
          snapshotId: "gather-test", generatedAt: "2026-07-17T10:00:00.000Z",
          transportClientId: "client-main", expectedCharacterName: "character-5",
          observedCharacterName: "character-5", characterStatus: "available",
          questId: "246", questFingerprint: "246:stage-2:abc",
        });
        assert.strictEqual(result.ok, true);
        assert.strictEqual(result.actionable, false);
        assert.strictEqual(result.actionReason, "read_only_discovery");
        assert.match(result.snapshotId, /^gather-/);
        assert.strictEqual(result.binding.complete, true);
        assert.strictEqual(result.binding.transportClientId, "client-main");
        assert.strictEqual(result.binding.observedCharacterName, "character-5");
        assert.strictEqual(result.binding.requestScope.questId, "246");
        assert.strictEqual(result.binding.requestScope.questFingerprint, "246:stage-2:abc");
        assert.strictEqual(result.candidates.length, 1);
        assert.strictEqual(result.candidates[0].nodeId, "herb-19");
        assert.strictEqual(result.candidates[0].location, "Зелёная опушка");
        assert.strictEqual(result.candidates[0].nativeMethod.descriptorType, "function");
        assert.strictEqual(result.candidates[0].nativeMethod.functionName, "gather");
        assert.strictEqual(result.candidates[0].ready, true);
        assert.strictEqual(result.candidates[0].cooldownRemaining, 0);
        assert.strictEqual(result.candidates[0].evidence.complete, true);
        assert.strictEqual(invoked, 0);
        assert.strictEqual(getterRead, 0);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_gathering_observer_bounds_scan_and_marks_ambiguous_methods_incomplete() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        const nodes = Array.from({ length: 160 }, (_, index) => ({
          nodeId: `ore-${index}`,
          resourceName: `Руда ${index}`,
          locationName: "Шахта",
          kind: "resource ore",
          ready: true,
          cooldownRemaining: 0,
          gather() {},
          collect() {},
        }));
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const result = factory(
          () => ({ model: { nodes } }),
          (value, limit) => String(value == null ? "" : value).slice(0, limit),
          "test-v1",
        )();

        assert.strictEqual(result.candidates.length, 100);
        assert.strictEqual(result.scan.truncated, true);
        assert.strictEqual(result.candidates[0].nativeMethod, null);
        assert.strictEqual(result.candidates[0].methodCandidates.length, 2);
        assert.strictEqual(result.candidates[0].evidence.complete, false);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_every_structural_scan_cutoff_is_reported_as_truncated() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const safeString = (value, limit) => String(value == null ? "" : value).slice(0, limit);
        const observe = (model) => factory(() => ({ model }), safeString, "test-v1")();
        const target = () => ({
          nodeId: "target",
          resourceName: "Осинка",
          locationName: "Лес",
          kind: "resource wood",
          ready: true,
          cooldownRemaining: 0,
          gather() {},
        });

        const arrayPruned = Array.from({ length: 200 }, (_, index) => ({ index }));
        arrayPruned.push(target());
        const arrayResult = observe({ nodes: arrayPruned });
        assert.strictEqual(arrayResult.scan.truncated, true);
        assert.strictEqual(arrayResult.candidates.length, 0);

        let deep = target();
        for (let index = 0; index < 6; index += 1) deep = { data: deep };
        const depthResult = observe(deep);
        assert.strictEqual(depthResult.scan.truncated, true);
        assert.strictEqual(depthResult.candidates.length, 0);

        const groups = Array.from({ length: 5 }, (_, group) => ({
          items: Array.from({ length: 200 }, (_, index) => ({ group, index })),
        }));
        const budgetResult = observe({ list: groups });
        assert.strictEqual(budgetResult.scan.visited, 800);
        assert.strictEqual(budgetResult.scan.truncated, true);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_hunt_model_getter_is_never_read() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        let getterRead = 0;
        const hunt = {};
        Object.defineProperty(hunt, "model", { get() { getterRead += 1; throw new Error("must not read"); } });
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const result = factory(
          () => hunt,
          (value, limit) => String(value == null ? "" : value).slice(0, limit),
          "test-v1",
        )();

        assert.strictEqual(result.ok, false);
        assert.strictEqual(result.message, "hunt_model_missing");
        assert.strictEqual(getterRead, 0);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_array_accessor_is_not_read_and_forces_truncated_snapshot() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        let getterRead = 0;
        const nodes = [];
        Object.defineProperty(nodes, "0", {
          enumerable: true,
          get() { getterRead += 1; throw new Error("must not read"); },
        });
        nodes.length = 1;
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const result = factory(
          () => ({ model: { nodes } }),
          (value, limit) => String(value == null ? "" : value).slice(0, limit),
          "test-v1",
        )();

        assert.strictEqual(getterRead, 0);
        assert.strictEqual(result.scan.truncated, true);
        assert.strictEqual(result.candidates.length, 0);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_known_field_descriptor_errors_fail_closed_without_own_key_enumeration() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        let ownKeysCalls = 0;
        const huge = {};
        for (let index = 0; index < 100000; index += 1) huge[`unknown${index}`] = index;
        const proxied = new Proxy(huge, {
          ownKeys() { ownKeysCalls += 1; throw new Error("must not enumerate"); },
          getOwnPropertyDescriptor(target, property) {
            if (property === "nodes") throw new Error("descriptor unavailable");
            return Reflect.getOwnPropertyDescriptor(target, property);
          },
        });
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const result = factory(
          () => ({ model: proxied }),
          (value, limit) => String(value == null ? "" : value).slice(0, limit),
          "test-v1",
        )();

        assert.strictEqual(ownKeysCalls, 0);
        assert.strictEqual(result.scan.truncated, true);
        assert.strictEqual(result.candidates.length, 0);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")


def test_explicit_null_requirements_are_complete_but_missing_fields_are_not() -> None:
    script = dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const source = fs.readFileSync("browser_injector/page_bridge_modules/21_gathering_activity.js", "utf8");
        const base = {
          nodeId: "herb-19", resourceName: "Вьюнок", locationName: "Лес",
          kind: "resource herb", ready: true, cooldownRemaining: 0, gather() {},
        };
        const explicit = { ...base, requiredTool: null, requiredProfession: null };
        const missing = { ...base, nodeId: "herb-20" };
        const factory = new Function("findHuntApp", "safeString", "BRIDGE_VERSION", `${source}\nreturn gatheringNodeSnapshot;`);
        const result = factory(
          () => ({ model: { nodes: [explicit, missing] } }),
          (value, limit) => String(value == null ? "" : value).slice(0, limit),
          "test-v1",
        )();

        assert.strictEqual(result.candidates[0].evidence.complete, true);
        assert.strictEqual(result.candidates[0].evidence.toolRequirementObserved, true);
        assert.strictEqual(result.candidates[1].evidence.complete, false);
        assert.strictEqual(result.candidates[1].evidence.toolRequirementObserved, false);
        """
    )

    subprocess.run(["node", "-e", script], check=True, cwd=".")
