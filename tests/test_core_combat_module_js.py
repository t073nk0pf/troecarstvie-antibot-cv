from __future__ import annotations

import json
import subprocess

from scripts.build_page_bridge import build


def test_adaptive_verify_honors_timeout_grace_before_stable_wrong_exit() -> None:
    source = build()
    assert "Math.floor(timeoutMs * 0.8)" in source
    assert "Date.now() - started >= stableWrongGraceMs" in source

    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/00_core_combat.js", "utf8");
const start = source.indexOf("  const adaptiveVerify");
const end = source.indexOf("  const interestingName", start);
let now = 0;
const context = {
  Date: { now: () => now },
  JSON,
  delayMs: async (delay) => { now += delay; },
};
vm.runInNewContext(`${source.slice(start, end)}; globalThis.runAdaptiveVerify = adaptiveVerify;`, context);
(async () => {
  const result = await context.runAdaptiveVerify(
    () => ({ state: "pending" }),
    () => false,
    { timeoutMs: 2000, maxStableWrong: 4 },
  );
  assert.strictEqual(result.state, "pending");
  assert.ok(now >= 1600, `stable-wrong exited before grace: ${now}`);
  assert.ok(now <= 2000, `stable-wrong exceeded timeout: ${now}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dom_readiness_requires_bound_exact_battle_ability_control() -> None:
    source = build()
    script = f"""
const assert = require("assert");
const vm = require("vm");
const source = {json.dumps(source)};

async function observe(mode) {{
  const messages = [];
  const listeners = {{}};
  const attrs = {{
    "data-slot": mode === "junk-slot" ? "2junk" : "2",
    "data-ability-id": "-4626",
    "data-ability-name": "skill",
    "data-ready": "true",
    "data-cooldown": "0",
  }};
  const control = {{
    offsetWidth: 20,
    offsetHeight: 20,
    disabled: false,
    getClientRects() {{ return [{{ width: 20, height: 20 }}]; }},
    getAttribute(name) {{ return Object.hasOwn(attrs, name) ? attrs[name] : null; }},
  }};
  const container = {{
    querySelectorAll() {{ return [control]; }},
  }};
  const fightDocument = {{
    title: "",
    body: {{ innerText: "" }},
    querySelectorAll(selector) {{
      if (selector.includes("data-battle-abilities")) return mode === "loose" ? [] : [container];
      if (selector.startsWith("[data-slot]")) return mode === "loose" ? [control] : [];
      return [];
    }},
  }};
  const fightWin = {{
    location: {{ href: "https://3kingdoms.ru/fight.php?1" }},
    frames: [],
    document: fightDocument,
    fight: {{ model: {{
      finished: false,
      fightState: 1,
      oppId: 7,
      myTurn: true,
      enabledControl: true,
      abilities: {{ all: [{{ id: -4626, slot: 2, name: "skill" }}] }},
    }} }},
    useSkill() {{}},
  }};
  const root = {{
    location: {{ href: "https://3kingdoms.ru/main.php" }},
    frames: [fightWin],
    document: {{ title: "", querySelectorAll() {{ return []; }} }},
    setTimeout,
    addEventListener(type, callback) {{ listeners[type] = callback; }},
    removeEventListener() {{}},
    postMessage(message) {{ messages.push(message); }},
  }};
  root.top = root;
  root.window = root;
  vm.runInNewContext(source, {{ window: root, console, setTimeout, clearTimeout }});
  const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
  listeners.message({{
    source: root,
    data: {{
      source: `antibot-cv-content:${{version}}`,
      token: "token",
      command: {{ type: "battle_snapshot", payload: {{}} }},
    }},
  }});
  const deadline = Date.now() + 1000;
  while (!messages.length && Date.now() < deadline) {{
    await new Promise((resolve) => setTimeout(resolve, 5));
  }}
  assert.strictEqual(messages.length, 1);
  return JSON.parse(messages[0].message).abilities[0].readinessEvidence;
}}

;(async () => {{
  const bound = await observe("bound");
  assert.strictEqual(bound.authoritative, true);
  assert.deepStrictEqual(bound.sources, ["visible_battle_ability_dom"]);

  const junk = await observe("junk-slot");
  assert.strictEqual(junk.authoritative, false);

  const loose = await observe("loose");
  assert.strictEqual(loose.authoritative, false);
}})().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
