"""Contract tests for the read-only V2 action-ref bridge section."""
from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "browser_injector" / "page_bridge_modules" / "38_v2_action_refs.js"


def test_v2_action_ref_module_has_no_mutation_or_targeting_surface() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert "const v2CapabilityObservation" in source
    assert "V2_ACTION_REF_TTL_MS = 20 * 1000" in source
    assert "v2ActionRefs = new Map()" in source
    for forbidden in (".click(", "dispatch", "submit", "questId", "selector", "href", "routeRef"):
        assert forbidden not in source


def test_v2_capability_section_issues_opaque_snapshot_bound_visit_ref() -> None:
    module_names = [
        "00_core_combat.js", "10_hunt_inventory.js", "11_resource_capabilities.js",
        "12_resource_snapshot.js", "20_hunt_actions.js", "21_gathering_activity.js",
        "30_navigation_death.js", "31_area_objects.js", "32_instance_actions.js",
        "35_npc_quests.js", "36_quest_chat_progress.js", "37_shop_observer.js",
        "38_v2_action_refs.js", "40_state_layout_dispatch.js",
    ]
    source_expression = "+".join(
        f'fs.readFileSync("browser_injector/page_bridge_modules/{name}", "utf8") + "\\n"'
        for name in module_names
    )
    script = f"""
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const source="(() => {{\\n"+{source_expression}+"}})();\\n";
const version=source.match(/const BRIDGE_VERSION = \"([^\"]+)\"/)[1];
const messages=[],listeners={{}}; let mutations=0;
const main={{name:"main",location:{{href:"https://3kingdoms.ru/area.php"}},frames:[],
 document:{{title:"Лес",readyState:"complete",body:{{innerText:"Лес",textContent:"Лес"}},
 querySelector(){{return null}},querySelectorAll(){{return[]}},documentElement:{{innerHTML:""}}}},
 area:{{model:{{area:{{title:"Лес"}}}},controller:{{compass:{{data:{{location:102}}}}}}}}}};
const frame={{name:"main_frame",location:{{href:"https://3kingdoms.ru/main_frame.php"}},frames:[main],
 document:{{title:"",body:{{innerText:"",textContent:""}},querySelector(){{return null}},querySelectorAll(){{return[]}},documentElement:{{innerHTML:""}}}}}};
frame.frames.main=main;
const root={{name:"top",location:{{href:"https://3kingdoms.ru/main.php"}},frames:[frame],
 document:{{title:"",body:{{innerText:"",textContent:""}},querySelector(){{return null}},querySelectorAll(){{return[]}},documentElement:{{innerHTML:""}}}},
 addEventListener(type,callback){{listeners[type]=callback}},removeEventListener(){{}},postMessage(value){{messages.push(value)}},
 setTimeout, processMenu(){{mutations+=1}}}};
root.frames.main_frame=frame;root.top=root;root.window=root;
vm.runInNewContext(source,{{window:root,console,setTimeout,clearTimeout,URL,URLSearchParams,Math,Date}});
function observe(){{messages.length=0;listeners.message({{source:root,data:{{source:`antibot-cv-content:${{version}}`,token:"v2",command:{{type:"state_snapshot",payload:{{include:["v2Capabilities"]}}}}}}}});return JSON.parse(messages[0].message)}}
const first=observe(), section=first.sections.v2Capabilities;
assert.strictEqual(section.status,"available");
assert.strictEqual(section.data.snapshotId,first.snapshotId);
assert.strictEqual(section.data.revision,1);
assert.strictEqual(section.data.actionRefs.length,1);
const ref=section.data.actionRefs[0];
assert.deepStrictEqual(Object.keys(ref).sort(),["expiresAt","fingerprint","kind","ref","revision","snapshotId"]);
assert.strictEqual(ref.kind,"Visit");assert.match(ref.ref,/^ref:[a-z0-9-]+$/);
assert.strictEqual(ref.snapshotId,first.snapshotId);assert.strictEqual(ref.revision,1);assert.match(ref.fingerprint,/^v2-[a-z0-9]+$/);
assert.ok(ref.expiresAt>Date.now());
assert.strictEqual(JSON.stringify(ref).includes("Лес"),false);assert.strictEqual(JSON.stringify(ref).includes("102"),false);
const second=observe();assert.strictEqual(second.sections.v2Capabilities.data.revision,2);
assert.notStrictEqual(second.sections.v2Capabilities.data.actionRefs[0].ref,ref.ref);
assert.strictEqual(mutations,0);
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
