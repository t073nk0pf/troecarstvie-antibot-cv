from __future__ import annotations

import json
import subprocess

from scripts.build_page_bridge import build


def test_shop_observer_is_read_only_bounded_and_typed() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/37_shop_observer.js", "utf8");
let clicks = 0;
function element(attrs, children = []) {
  return {
    attrs,
    children,
    click() { clicks += 1; },
  };
}
const lots = [
  element({"data-listing-id":"lot-1","data-item-id":"item-7","data-item-name":"Осиное крыло","data-unit-price":"4","data-available-quantity":"3","data-currency":"кругляши"}),
];
const balance = element({"data-balance":"19","data-currency":"round"});
const container = element({"data-auction-id":"auction-main"}, [balance, ...lots]);
const doc = {body: element({}, [container])};
const sandbox = {
  mainContentContext: () => ({doc, href:"https://3kingdoms.ru/auction.php", title:"Аукцион", pageKind:"shop"}),
  safeString: (value, limit) => String(value || "").slice(0, limit),
  attr: (node, name) => node.attrs[name] || "",
  Date,
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.observe = procurementObservationSnapshot;`, sandbox);
const result = sandbox.observe({
  snapshotId:"snap-1", generatedAt:"2026-07-17T10:00:00.000Z",
  transportClientId:"client-main", expectedCharacterName:"v3g45",
  observedCharacterName:"v3g45", characterStatus:"available",
  questId:"31", questFingerprint:"quest:31:step:2",
});
assert.strictEqual(result.status, "available");
assert.strictEqual(result.data.source, "auction");
assert.strictEqual(result.data.shopId, "auction-main");
assert.strictEqual(result.data.currency, "round");
assert.strictEqual(result.data.balance, 19);
assert.strictEqual(result.data.listings[0].totalPrice, 12);
assert.strictEqual(result.data.complete, true);
for (const generatedAt of [
  "not-a-time", "   ", "2026-02-30T10:00:00.000Z",
  "2026-04-31T10:00:00.000Z", "2026-02-29T10:00:00.000Z",
]) {
  const invalidTime = sandbox.observe({
    snapshotId:"snap-1", generatedAt,
    transportClientId:"client-main", expectedCharacterName:"v3g45",
    observedCharacterName:"v3g45", characterStatus:"available",
    questId:"31", questFingerprint:"quest:31:step:2",
  });
  assert.strictEqual(invalidTime.status, "partial");
  assert.strictEqual(invalidTime.data.complete, false);
}
const validLeapDay = sandbox.observe({
  snapshotId:"snap-1", generatedAt:"2028-02-29T10:00:00.000Z",
  transportClientId:"client-main", expectedCharacterName:"v3g45",
  observedCharacterName:"v3g45", characterStatus:"available",
  questId:"31", questFingerprint:"quest:31:step:2",
});
assert.strictEqual(validLeapDay.status, "available");
assert.strictEqual(validLeapDay.data.complete, true);
assert.strictEqual(clicks, 0);
assert.ok(!/\.click\s*\(/.test(source));
assert.ok(!/\.submit\s*\(/.test(source));
assert.ok(!/querySelectorAll/.test(source));
'''
    subprocess.run(["node", "-e", script], check=True)


def test_shop_observer_fails_closed_on_ambiguous_container_or_currency() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/37_shop_observer.js", "utf8");
function element(attrs, children = []) { return {attrs, children}; }
const container = element({id:"shop"});
const other = element({"data-shop-id":"other"});
const doc = {body: element({}, [container, other])};
const sandbox = {
  mainContentContext: () => ({doc, href:"https://3kingdoms.ru/shop.php", title:"Магазин", pageKind:"shop"}),
  safeString: (value, limit) => String(value || "").slice(0, limit),
  attr: (node, name) => node.attrs[name] || "",
  Date,
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.observe = procurementObservationSnapshot;`, sandbox);
assert.strictEqual(sandbox.observe({snapshotId:"snap"}).reason, "procurement_container_ambiguous");
'''
    subprocess.run(["node", "-e", script], check=True)


def test_shop_observer_rejects_title_only_or_unallowlisted_paths() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/37_shop_observer.js", "utf8");
const doc = {body:{attrs:{},children:[]}};
let href = "https://3kingdoms.ru/user.php?mode=personage";
const sandbox = {
  mainContentContext: () => ({doc, href, title:"Аукцион Магазин", pageKind:"shop"}),
  safeString: (value, limit) => String(value || "").slice(0, limit),
  attr: (node, name) => node.attrs[name] || "",
  Date,
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.observe = procurementObservationSnapshot;`, sandbox);
for (const unrelated of [
  "https://3kingdoms.ru/user.php?mode=personage",
  "https://3kingdoms.ru/auction.php.fake",
  "https://evil.example/auction.php",
  "http://3kingdoms.ru/auction.php",
  "https://3kingdoms.ru/AUCTION.php",
  "https://3kingdoms.ru/SHOP.php",
]) {
  href = unrelated;
  const result = sandbox.observe({});
  assert.strictEqual(result.status, "not_loaded");
  assert.strictEqual(result.reason, "procurement_page_not_loaded");
}
'''
    subprocess.run(["node", "-e", script], check=True)


def test_shop_observer_rejects_junk_decimals_overflow_and_binding_gaps() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/37_shop_observer.js", "utf8");
function element(attrs, children = []) { return {attrs, children}; }
function observe({balance="100", price="4", quantity="2", metadata=true} = {}) {
  const balanceNode = element({"data-balance":balance,"data-currency":"round"});
  const lot = element({
    "data-listing-id":"lot","data-item-id":"item","data-item-name":"Resource",
    "data-unit-price":price,"data-available-quantity":quantity,"data-currency":"round",
  });
  const container = element({"data-shop-id":"merchant"}, [balanceNode, lot]);
  const doc = {body: element({}, [container])};
  const sandbox = {
    mainContentContext: () => ({doc, href:"https://3kingdoms.ru/shop.php", title:"Shop", pageKind:"shop"}),
    safeString: (value, limit) => String(value || "").slice(0, limit),
    attr: (node, name) => node.attrs[name] || "",
    Date,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\nthis.observe = procurementObservationSnapshot;`, sandbox);
  return sandbox.observe(metadata ? {
    snapshotId:"snap", generatedAt:"2026-07-17T10:00:00.000Z", transportClientId:"client",
    expectedCharacterName:"character", observedCharacterName:"character", characterStatus:"available",
    questId:"quest", questFingerprint:"fingerprint",
  } : {snapshotId:"snap"});
}
for (const options of [
  {balance:"100junk"}, {price:"4oops"}, {quantity:"2units"},
  {price:"9007199254740991", quantity:"2"},
]) {
  const result = observe(options);
  assert.strictEqual(result.data.complete, false);
}
assert.strictEqual(observe({metadata:false}).data.complete, false);
'''
    subprocess.run(["node", "-e", script], check=True)


def test_shop_observer_marks_more_than_listing_limit_truncated() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/37_shop_observer.js", "utf8");
function element(attrs, children = []) { return {attrs, children}; }
const lots = Array.from({length: 101}, (_, index) => element({
  "data-listing-id":`lot-${index}`,"data-item-id":"item","data-item-name":"Resource",
  "data-unit-price":"1","data-available-quantity":"1","data-currency":"round",
}));
const balance = element({"data-balance":"1000","data-currency":"round"});
const container = element({"data-auction-id":"auction"}, [balance, ...lots]);
const doc = {body: element({}, [container])};
const sandbox = {
  mainContentContext: () => ({doc, href:"https://3kingdoms.ru/auction.php", title:"Auction", pageKind:"shop"}),
  safeString: (value, limit) => String(value || "").slice(0, limit),
  attr: (node, name) => node.attrs[name] || "",
  Date,
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.observe = procurementObservationSnapshot;`, sandbox);
const result = sandbox.observe({
  snapshotId:"snap", transportClientId:"client", expectedCharacterName:"character",
  observedCharacterName:"character", characterStatus:"available",
  questId:"quest", questFingerprint:"fingerprint",
});
assert.strictEqual(result.data.truncated, true);
assert.strictEqual(result.data.complete, false);
assert.strictEqual(result.data.listings.length, 100);
'''
    subprocess.run(["node", "-e", script], check=True)


def test_shop_observer_normalizes_bad_metadata_and_stops_before_child_overflow() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("browser_injector/page_bridge_modules/37_shop_observer.js", "utf8");
let indexedReads = 0;
const tooManyChildren = new Proxy({length: 601}, {
  get(target, property) {
    if (property !== "length") indexedReads += 1;
    return target[property];
  },
});
const body = {attrs:{}, children:tooManyChildren};
const doc = {body};
const sandbox = {
  mainContentContext: () => ({doc, href:"https://3kingdoms.ru/shop.php", title:"Shop", pageKind:"shop"}),
  safeString: (value, limit) => String(value || "").slice(0, limit),
  attr: (node, name) => node.attrs[name] || "",
  Date,
};
vm.createContext(sandbox);
vm.runInContext(`${source}\nthis.observe = procurementObservationSnapshot;`, sandbox);
const nullMetadata = sandbox.observe(null);
assert.strictEqual(nullMetadata.reason, "procurement_root_scan_truncated");
assert.strictEqual(nullMetadata.data.complete, false);
assert.strictEqual(indexedReads, 0);
assert.doesNotThrow(() => sandbox.observe("not-an-object"));
'''
    subprocess.run(["node", "-e", script], check=True)


def test_dispatch_mints_metadata_keeps_semantic_partial_transport_ok_and_never_mutates() -> None:
    source = build()
    script = f'''
const assert = require("assert");
const vm = require("vm");
const source = {json.dumps(source)};
const messages = [];
const listeners = {{}};
let mutations = 0;
function element(attrs, children = []) {{
  return {{
    children,
    getAttribute(name) {{ return Object.hasOwn(attrs, name) ? attrs[name] : null; }},
    click() {{ mutations += 1; }},
    submit() {{ mutations += 1; }},
  }};
}}
const listing = element({{
  "data-listing-id":"lot-1", "data-item-id":"item-7", "data-item-name":"Осиное крыло",
  "data-unit-price":"4", "data-available-quantity":"3", "data-currency":"round",
}});
const balance = element({{"data-balance":"19", "data-currency":"round"}});
const container = element({{"data-auction-id":"auction-main"}}, [balance, listing]);
const document = {{
  title: "Аукцион", body: element({{}}, [container]),
  querySelectorAll() {{ return []; }},
}};
const root = {{
  location: {{ href: "https://3kingdoms.ru/auction.php" }}, frames: [], document,
  setTimeout, clearTimeout,
  addEventListener(type, callback) {{ listeners[type] = callback; }},
  removeEventListener() {{}},
  postMessage(message) {{ messages.push(message); }},
  getHuntApp() {{
    return {{ model: {{
      clientId: "dom-client-must-not-win", characterId: "dom-character-must-not-win",
      questId: "dom-quest-must-not-win", questFingerprint: "dom-fingerprint-must-not-win",
      nodes: [],
    }} }};
  }},
}};
root.top = root;
root.window = root;
vm.runInNewContext(source, {{ window: root, console, setTimeout, clearTimeout, Date, Set, Map, Math, URL }});
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
function command(type, metadata, transportClientId = "client-tab-b") {{
  messages.length = 0;
  listeners.message({{
    source: root,
    data: {{
      source: `antibot-cv-content:${{version}}`, token: `token-${{type}}`,
      command: {{ type, payload: {{ metadata, transport: {{ clientId: transportClientId }} }} }},
    }},
  }});
  assert.strictEqual(messages.length, 1);
  return {{ transportOk: messages[0].ok, result: JSON.parse(messages[0].message) }};
}}
const metadata = {{
  snapshotId: "caller-must-not-mint", generatedAt: "2000-01-01T00:00:00.000Z",
  clientId: "spoofed-client-a", expectedCharacterName: "v3g45",
  questId: "31", questFingerprint: "quest:31:step:2",
}};
const procurement = command("procurement_observation_snapshot", metadata);
assert.strictEqual(procurement.transportOk, true);
assert.strictEqual(procurement.result.status, "partial");
assert.match(procurement.result.snapshotId, /^procurement-/);
assert.notStrictEqual(procurement.result.snapshotId, metadata.snapshotId);
assert.notStrictEqual(procurement.result.generatedAt, metadata.generatedAt);
assert.strictEqual(procurement.result.binding.transportClientId, "client-tab-b");
assert.strictEqual(procurement.result.binding.observedCharacterName, null);
assert.strictEqual(procurement.result.binding.complete, false);

const partial = command("procurement_observation_snapshot", {{ clientId: "spoofed-client-a" }});
assert.strictEqual(partial.transportOk, true);
assert.strictEqual(partial.result.status, "partial");
assert.strictEqual(partial.result.binding.complete, false);

const gathering = command("gathering_node_snapshot", metadata);
assert.strictEqual(gathering.transportOk, true);
assert.strictEqual(gathering.result.binding.transportClientId, "client-tab-b");
assert.strictEqual(gathering.result.binding.expectedCharacterName, "v3g45");
assert.strictEqual(gathering.result.binding.observedCharacterName, null);
assert.strictEqual(gathering.result.binding.requestScope.questId, "31");
assert.strictEqual(gathering.result.binding.requestScope.questFingerprint, "quest:31:step:2");
assert.strictEqual(gathering.result.binding.complete, false);
assert.strictEqual(gathering.result.actionable, false);
assert.strictEqual(mutations, 0);
'''
    subprocess.run(["node", "-e", script], check=True)
