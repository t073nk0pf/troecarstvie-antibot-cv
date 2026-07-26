from __future__ import annotations

import subprocess


def test_area_npc_snapshot_exposes_route_ref_without_secret_url() -> None:
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const names = ["00_core_combat.js", "10_hunt_inventory.js", "20_hunt_actions.js", "30_navigation_death.js", "35_npc_quests.js", "40_state_layout_dispatch.js"];
const source = `(() => {\n${names.map((name) => fs.readFileSync(`browser_injector/page_bridge_modules/${name}`, "utf8")).join("\n")}\n})();`;
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = [];
const listeners = {};
const shell = {};
const cards = [
  { title: "Торговец Богдан", id: "0", index: "0" },
  { title: "Богатырь Тур", id: "13", index: "1" },
  { title: "Воитель Герць", id: "3", index: "2" },
].map((item) => ({
  innerText: item.title, textContent: item.title, offsetWidth: 20, offsetHeight: 10,
  getClientRects() { return [{ width: 20, height: 10 }]; },
  getAttribute(name) {
    if (name === "title") return item.title;
    if (name === "data-id") return item.id;
    if (name === "data-index") return item.index;
    return null;
  },
}));
const config = String.raw`var area = new LocationApp({"area_conf":"<town><item id=\"0\" name=\"Bogdan\" type=\"npc\" href=\"\/npc.php?action=enter&amp;ref=398&amp;secret-token\" mode=\"npc\" /><item id=\"13\" name=\"Tur\" type=\"npc\" href=\"\/npc.php?action=enter&amp;ref=228&amp;secret-token\" mode=\"npc\" /><item id=\"3\" name=\"Gertz\" type=\"npc\" href=\"\/npc.php?action=enter&amp;ref=487&amp;secret-token\" mode=\"npc\" /></town>"});`;
const document = {
  title: "Городская площадь Арсы", readyState: "complete",
  body: { innerText: "Городская площадь Арсы", textContent: "Городская площадь Арсы" },
  scripts: [{ src: "", textContent: String.raw`const fake = 'area = new LocationApp({"area_conf":"<item id=\\"0\\" type=\\"npc\\" href=\\"/npc.php?action=enter&amp;ref=999\\" />"})';` }, { src: "", textContent: config }],
  querySelector(selector) { return selector === ".b-control-area__list,.b-control-area" ? shell : null; },
  querySelectorAll(selector) { return selector === ".b-control-area__list-item.npc" ? cards : []; },
};
const root = {
  name: "top", location: { href: "https://3kingdoms.ru/area.php" }, frames: [], document,
  area: { model: { area: { title: "Городская площадь Арсы" } }, controller: { compass: { data: { location: 102 } } } },
  setTimeout, addEventListener(type, callback) { listeners[type] = callback; }, removeEventListener() {},
  postMessage(message) { messages.push(message); },
};
root.top = root; root.window = root;
vm.runInNewContext(source, { window: root, console, setTimeout, URLSearchParams });
listeners.message({ source: root, data: { source: `antibot-cv-content:${version}`, token: "route-ref", command: { type: "area_npc_snapshot", payload: {} } } });
setTimeout(() => {
  assert.strictEqual(messages.length, 1);
  const snapshot = JSON.parse(messages[0].message);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(snapshot.items.map((item) => [item.dataId, item.routeRef]))), [["0", "398"], ["13", "228"], ["3", "487"]]);
  assert.strictEqual(JSON.stringify(snapshot).includes("secret-token"), false);
}, 0);
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_area_npc_route_ref_rejects_unbound_or_conflicting_config() -> None:
    script = r''' 
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const names = ["00_core_combat.js", "10_hunt_inventory.js", "20_hunt_actions.js", "30_navigation_death.js", "35_npc_quests.js", "40_state_layout_dispatch.js"];
const source = `(() => {\n${names.map((name) => fs.readFileSync(`browser_injector/page_bridge_modules/${name}`, "utf8")).join("\n")}\n})();`;
const version = source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages = []; const listeners = {}; const shell = {};
const cards = ["13", "14", "15", "16", "17", "18", "19", "20", "21", "22"].map((id) => ({ innerText: `Npc${id}`, textContent: `Npc${id}`, offsetWidth: 1, offsetHeight: 1,
  getClientRects() { return [{}]; }, getAttribute(name) { return name === "data-id" ? id : name === "data-index" ? id : name === "title" ? `Npc${id}` : null; } }));
const bad = String.raw`area = new LocationApp({"area_conf":"<town><item data-id=\"13\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=999\" /><item id=\"14\" type=\"npc\" href=\"/evil.php?action=enter&amp;ref=998\" /><item id=\"15\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=10\" /><item id=\"15\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=11\" /><item id=\"16\" type=\"npc\" href=\"/npc.php?action=enter&amp;action=evil&amp;ref=12\" /><item id=\"17\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=13&amp;ref=14\" /><item id=\"18\" id=\"118\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=18\" /><item id=\"19\" type=\"npc\" type=\"shop\" href=\"/npc.php?action=enter&amp;ref=19\" /><item id=\"20\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=20\" href=\"/npc.php?action=enter&amp;ref=120\" /><item id=\"21\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=21\" /><item id=\"22\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=21\" /></town>"});`;
const unrelated = String.raw`var note = "LocationApp <item id=\"13\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=997\" />";`;
const document = { title: "Area", readyState: "complete", body: { innerText: "Area", textContent: "Area" }, scripts: [{src:"",textContent:unrelated},{src:"",textContent:bad}],
 querySelector(s) { return s === ".b-control-area__list,.b-control-area" ? shell : null; }, querySelectorAll(s) { return s === ".b-control-area__list-item.npc" ? cards : []; } };
const root = { name:"top", location:{href:"https://3kingdoms.ru/area.php"}, frames:[], document, area:{model:{area:{title:"Area"}},controller:{compass:{data:{location:102}}}},
 setTimeout, addEventListener(t,c){listeners[t]=c;}, removeEventListener(){}, postMessage(m){messages.push(m);} }; root.top=root; root.window=root;
vm.runInNewContext(source, {window:root,console,setTimeout,URLSearchParams});
listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"negative",command:{type:"area_npc_snapshot",payload:{}}}});
setTimeout(() => { const snapshot=JSON.parse(messages[0].message); assert.deepStrictEqual(JSON.parse(JSON.stringify(snapshot.items.map((x)=>x.routeRef))), [null,null,null,null,null,null,null,null,null,null]); }, 0);
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_area_npc_route_ref_does_not_cross_constructor_object_boundary() -> None:
    script = r'''
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const names=["00_core_combat.js","10_hunt_inventory.js","20_hunt_actions.js","30_navigation_death.js","35_npc_quests.js","40_state_layout_dispatch.js"];
const source=`(() => {\n${names.map((n)=>fs.readFileSync(`browser_injector/page_bridge_modules/${n}`,"utf8")).join("\n")}\n})();`;
const version=source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1],messages=[],listeners={},shell={};
const card={innerText:"Npc",textContent:"Npc",offsetWidth:1,offsetHeight:1,getClientRects(){return[{}]},getAttribute(n){return n==="data-id"?"0":n==="data-index"?"0":n==="title"?"Npc":null}};
const config=String.raw`var area = new LocationApp({}); var unrelated = {"area_conf":"<town><item id=\"0\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=398\" /></town>"};`;
const document={title:"Area",readyState:"complete",body:{innerText:"Area",textContent:"Area"},scripts:[{src:"",textContent:config}],querySelector(s){return s===".b-control-area__list,.b-control-area"?shell:null},querySelectorAll(s){return s===".b-control-area__list-item.npc"?[card]:[]}};
const root={name:"top",location:{href:"https://3kingdoms.ru/area.php"},frames:[],document,area:{model:{area:{title:"Area"}},controller:{compass:{data:{location:102}}}},setTimeout,addEventListener(t,c){listeners[t]=c},removeEventListener(){},postMessage(m){messages.push(m)}};root.top=root;root.window=root;
vm.runInNewContext(source,{window:root,console,setTimeout,URLSearchParams}); listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"boundary",command:{type:"area_npc_snapshot",payload:{}}}});
setTimeout(()=>{const snapshot=JSON.parse(messages[0].message);assert.strictEqual(snapshot.items[0].routeRef,null)},0);
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_area_npc_route_ref_rejects_duplicate_top_level_area_conf() -> None:
    script = r'''
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const names=["00_core_combat.js","10_hunt_inventory.js","20_hunt_actions.js","30_navigation_death.js","35_npc_quests.js","40_state_layout_dispatch.js"];
const source=`(() => {\n${names.map((n)=>fs.readFileSync(`browser_injector/page_bridge_modules/${n}`,"utf8")).join("\n")}\n})();`,version=source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
async function probe(config) { const messages=[],listeners={},shell={},card={innerText:"Npc",textContent:"Npc",offsetWidth:1,offsetHeight:1,getClientRects(){return[{}]},getAttribute(n){return n==="data-id"?"0":n==="data-index"?"0":n==="title"?"Npc":null}};
 const document={title:"Area",readyState:"complete",body:{innerText:"Area",textContent:"Area"},scripts:[{src:"",textContent:config}],querySelector(s){return s===".b-control-area__list,.b-control-area"?shell:null},querySelectorAll(s){return s===".b-control-area__list-item.npc"?[card]:[]}};
 const root={name:"top",location:{href:"https://3kingdoms.ru/area.php"},frames:[],document,area:{model:{area:{title:"Area"}},controller:{compass:{data:{location:102}}}},setTimeout,addEventListener(t,c){listeners[t]=c},removeEventListener(){},postMessage(m){messages.push(m)}};root.top=root;root.window=root;
 vm.runInNewContext(source,{window:root,console,setTimeout,URLSearchParams});listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"dup",command:{type:"area_npc_snapshot",payload:{}}}});await new Promise((r)=>setTimeout(r,0));return JSON.parse(messages[0].message).items[0].routeRef; }
(async()=>{const item=String.raw`<town><item id=\"0\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=398\" /></town>`;
 assert.strictEqual(await probe(`area = new LocationApp({"area_conf":"<town></town>","area_conf":"${item}"});`),null);
 assert.strictEqual(await probe(`area = new LocationApp({"area_conf":"${item}","area_conf":"<town></town>"});`),null);})().catch((error)=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_open_exact_npc_requires_snapshot_bound_route_ref_before_navigation() -> None:
    script = r'''
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const names=["00_core_combat.js","10_hunt_inventory.js","20_hunt_actions.js","30_navigation_death.js","35_npc_quests.js","40_state_layout_dispatch.js"];
const source=`(() => {\n${names.map((n)=>fs.readFileSync(`browser_injector/page_bridge_modules/${n}`,"utf8")).join("\n")}\n})();`,version=source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1],messages=[],listeners={},shell={};
const card={innerText:"Торговец Богдан",textContent:"Торговец Богдан",offsetWidth:1,offsetHeight:1,getClientRects(){return[{width:1,height:1}]},click(){throw new Error("card click must not be used")},getAttribute(n){return n==="data-id"?"0":n==="data-index"?"0":n==="title"?"Торговец Богдан":null}};
const config=String.raw`var area = new LocationApp({"area_conf":"<town><item id=\"0\" type=\"npc\" href=\"/npc.php?action=enter&amp;ref=398&amp;secret\" /></town>"});`;
const document={title:"Area",readyState:"complete",body:{innerText:"Area",textContent:"Area"},scripts:[{src:"",textContent:config}],querySelector(s){return s===".b-control-area__list,.b-control-area"?shell:null},querySelectorAll(s){return s===".b-control-area__list-item.npc"?[card]:[]}};
let storedHref="https://3kingdoms.ru/area.php",assignCalls=0,throwLookup=false;const location={get href(){return storedHref},get assign(){if(throwLookup)throw new Error("pre-issue");return function(value){assignCalls+=1;storedHref=value;throw new Error(`must-not-leak:${value}`)}}};const root={name:"top",location,frames:[],document,area:{model:{area:{title:"Area"}},controller:{compass:{data:{location:102}}}},setTimeout,addEventListener(t,c){listeners[t]=c},removeEventListener(){},postMessage(m){messages.push(m)}};root.top=root;root.window=root;
vm.runInNewContext(source,{window:root,console,setTimeout,URL,URLSearchParams}); async function command(type,payload={}){messages.length=0;listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:type,command:{type,payload}}});while(!messages.length)await new Promise((r)=>setTimeout(r,5));return JSON.parse(messages[0].message)}
(async()=>{const observed=await command("area_npc_snapshot",{});assert.strictEqual(observed.items[0].routeRef,"398");assert.strictEqual(observed.items[0].actionable,true);const base={expectedSnapshotId:observed.snapshotId,expectedLocationId:"102",npcId:"0",expectedName:"Торговец Богдан",verifyTimeoutMs:100};
 const wrong=await command("open_exact_npc",{...base,expectedRouteRef:"999"});assert.strictEqual(wrong.outcome,"NOT_ISSUED");assert.strictEqual(location.href,"https://3kingdoms.ru/area.php");
 throwLookup=true;const unavailable=await command("open_exact_npc",{...base,expectedRouteRef:"398"});assert.strictEqual(unavailable.outcome,"NOT_ISSUED");assert.strictEqual(assignCalls,0);throwLookup=false;
 const observedAgain=await command("area_npc_snapshot",{});const opened=await command("open_exact_npc",{...base,expectedSnapshotId:observedAgain.snapshotId,expectedRouteRef:"398"});assert.strictEqual(opened.mutationIssued,true,JSON.stringify(opened));assert.strictEqual(assignCalls,1);assert.strictEqual(location.href,"/npc.php?action=enter&ref=398&secret");assert.strictEqual(JSON.stringify(opened).includes("secret"),false);assert.strictEqual(opened.destination,"/npc.php");assert.strictEqual(opened.message,"npc_route_navigation_exception");})().catch((e)=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_npc_dialog_snapshot_discovers_single_resulting_identity_without_quest_click() -> None:
    script = r'''
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const names=["00_core_combat.js","10_hunt_inventory.js","20_hunt_actions.js","30_navigation_death.js","35_npc_quests.js","40_state_layout_dispatch.js"];
const source=`(() => {\n${names.map((n)=>fs.readFileSync(`browser_injector/page_bridge_modules/${n}`,"utf8")).join("\n")}\n})();`,version=source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const messages=[],listeners={};
function node(text,href){return{innerText:text,textContent:text,tagName:"A",offsetWidth:1,offsetHeight:1,getClientRects(){return[{width:1,height:1}]},getAttribute(n){return n==="href"?href:null},closest(){return null}}}
const identity=node("КОЛДУНЬЯ ВИЛЕНА","/npc.php?f_id=4&npc_id=110&global_npc=0");
const service=node("вернуться к квестам","/npc.php?f_id=4&npc_id=110&global_npc=0");
const quest=node("Далее","/npc.php?f_id=4&npc_id=110&quest_id=304&point_id=1");
const actions=[identity,service,quest];
const header={innerText:"КОЛДУНЬЯ ВИЛЕНА",textContent:"КОЛДУНЬЯ ВИЛЕНА"};
const serviceHeader={innerText:"вернуться к квестам",textContent:"вернуться к квестам"};
const document={title:"NPC",readyState:"complete",body:{innerText:"КОЛДУНЬЯ ВИЛЕНА",textContent:"КОЛДУНЬЯ ВИЛЕНА"},querySelector(){return null},querySelectorAll(s){if(s==="h1")return[header,serviceHeader];if(s==="a[href],button,input[type='button'],input[type='submit'],[onclick]")return actions;return[]}};
const root={name:"top",location:{href:"https://3kingdoms.ru/npc.php"},frames:[],document,setTimeout,addEventListener(t,c){listeners[t]=c},removeEventListener(){},postMessage(m){messages.push(m)}};root.top=root;root.window=root;
vm.runInNewContext(source,{window:root,console,setTimeout,URL,URLSearchParams});listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"dialog",command:{type:"npc_dialog_snapshot",payload:{expectedNpcId:"4"}}}});
setTimeout(async()=>{let value=JSON.parse(messages[0].message);assert.strictEqual(value.identityMatches,true);assert.strictEqual(value.resultingName,"КОЛДУНЬЯ ВИЛЕНА");assert.strictEqual(value.npcInstanceId,"110");
 identity.innerText="Назад";identity.textContent="Назад";messages.length=0;listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"bad",command:{type:"npc_dialog_snapshot",payload:{expectedNpcId:"4"}}}});await new Promise((r)=>setTimeout(r,0));value=JSON.parse(messages[0].message);assert.strictEqual(value.resultingName,null);
 messages.length=0;listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"expected-header",command:{type:"npc_dialog_snapshot",payload:{expectedNpcId:"4",expectedName:"Колдунья Вилена"}}}});await new Promise((r)=>setTimeout(r,0));value=JSON.parse(messages[0].message);assert.strictEqual(value.matchingHeaders.length,1);assert.strictEqual(value.resultingName,"КОЛДУНЬЯ ВИЛЕНА");
 identity.innerText="КОЛДУНЬЯ ВИЛЕНА";identity.textContent="КОЛДУНЬЯ ВИЛЕНА";for(let i=0;i<149;i+=1)actions.push(node(`X${i}`,`/npc.php?f_id=4&npc_id=110&quest_id=${500+i}`));messages.length=0;listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"truncated",command:{type:"npc_dialog_snapshot",payload:{expectedNpcId:"4"}}}});await new Promise((r)=>setTimeout(r,0));value=JSON.parse(messages[0].message);assert.strictEqual(value.truncated,true);
},0);
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_route_authority_is_all_or_nothing_for_overflow_and_ambiguity() -> None:
    script = r'''
const assert=require("assert"),fs=require("fs"),vm=require("vm");
const names=["00_core_combat.js","10_hunt_inventory.js","20_hunt_actions.js","30_navigation_death.js","35_npc_quests.js","40_state_layout_dispatch.js"];
const source=`(() => {\n${names.map((n)=>fs.readFileSync(`browser_injector/page_bridge_modules/${n}`,"utf8")).join("\n")}\n})();`,version=source.match(/const BRIDGE_VERSION = "([^"]+)"/)[1];
const item=(id,ref,quote='"')=>`<item id=${quote}${id}${quote} type=${quote}npc${quote} href=${quote}/npc.php?action=enter&amp;ref=${ref}${quote} />`;
const config=(items)=>`area = new LocationApp(${JSON.stringify({area_conf:`<town>${items}</town>`})});`;
async function probe(scripts,ids){const messages=[],listeners={},shell={},cards=ids.map((id)=>({innerText:`N${id}`,textContent:`N${id}`,offsetWidth:1,offsetHeight:1,getClientRects(){return[{width:1}]},click(){},getAttribute(n){return n==="data-id"?id:n==="data-index"?id:n==="title"?`N${id}`:null}}));
 const document={title:"A",readyState:"complete",body:{innerText:"A",textContent:"A"},scripts:scripts.map((textContent)=>({src:"",textContent})),querySelector(s){return s===".b-control-area__list,.b-control-area"?shell:null},querySelectorAll(s){return s===".b-control-area__list-item.npc"?cards:[]}};
 const root={name:"top",location:{href:"https://3kingdoms.ru/area.php"},frames:[],document,area:{model:{area:{title:"A"}},controller:{compass:{data:{location:1}}}},setTimeout,addEventListener(t,c){listeners[t]=c},removeEventListener(){},postMessage(m){messages.push(m)}};root.top=root;root.window=root;vm.runInNewContext(source,{window:root,console,setTimeout,URL,URLSearchParams});listeners.message({source:root,data:{source:`antibot-cv-content:${version}`,token:"x",command:{type:"area_npc_snapshot",payload:{}}}});await new Promise((r)=>setTimeout(r,0));return JSON.parse(messages[0].message).items.map((x)=>x.routeRef)}
(async()=>{
 assert.deepStrictEqual(await probe([config(item("1","10")+item("2","10")+item("1","11"))],["1","2"]),[null,null]);
 assert.deepStrictEqual(await probe([config(item("1","10")+item("01","11"))],["1"]),[null]);
 assert.deepStrictEqual(await probe([config(item("1","10","'")+item("2","11"))],["1","2"]),[null,null]);
 assert.deepStrictEqual(await probe([config(item("1","10"))+`; area = new LocationApp(${JSON.stringify({area_conf:`<town>${item("2","11")}</town>`})});`],["1","2"]),[null,null]);
 assert.deepStrictEqual(await probe([config(Array.from({length:301},(_,i)=>item(String(i+1),String(i+1))).join(""))],["1"]),[null]);
 assert.deepStrictEqual(await probe(Array.from({length:101},(_,i)=>i===100?config(item("1","10")):""),["1"]),[null]);
})().catch((e)=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=".", text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
