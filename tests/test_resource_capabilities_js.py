from __future__ import annotations

import subprocess


def test_resource_capability_probe_is_bounded_and_never_invokes_getters() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
let getterCalls = 0;
const document = { title: "", body: null, querySelector() { throw new Error("DOM access"); }, querySelectorAll() { throw new Error("DOM scan"); } };
const root = {
  name: "top", frames: [], document, location: { href: "https://3kingdoms.ru/area.php" },
  setTimeout, addEventListener(type, callback) { listeners[type] = callback; },
  removeEventListener() {}, postMessage(message) { messages.push(message); },
  area: { model: { resources: { health: { current: 50, max: 100 } } } },
};
Object.defineProperty(root, "fight", { get() { getterCalls += 1; return {}; } });
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, clearTimeout });
listeners.message({ source: root, data: {
  source: `antibot-cv-content:${version}`, token: "capabilities",
  command: { type: "resource_model_capabilities", payload: {} },
} });
assert.strictEqual(messages.length, 1);
assert.strictEqual(messages[0].ok, true);
const result = JSON.parse(messages[0].message);
assert.strictEqual(getterCalls, 0);
assert.strictEqual(result.schemaVersion, 1);
assert.strictEqual(result.domNodesScanned, 0);
assert.ok(result.roots.length <= 6);
assert.ok(result.roots.every((entry) => entry.fields.length <= 24));
assert.strictEqual(result.roots.find((entry) => entry.root === "fight").status, "accessor");
const area = result.roots.find((entry) => entry.root === "area");
assert.strictEqual(area.fields.find((entry) => entry.path === "model.resources.health.current").type, "number");
'''
    result = subprocess.run(
        ["node", "-e", script], cwd=".", text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
