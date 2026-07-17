/* JSONL harness: the generated bridge is read once, each case gets a VM. */
const fs = require("fs");
const vm = require("vm");
const bridgeSource = fs.readFileSync("browser_injector/page_bridge.js", "utf8");
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", async () => {
  for (const line of input.split("\n")) {
    if (!line.trim()) continue;
    const test = JSON.parse(line);
    try {
      const sandbox = { console, require, setTimeout, clearTimeout, BRIDGE_SOURCE: bridgeSource };
      vm.createContext(sandbox);
      const timeoutMs = Number(test.timeoutMs);
      if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
        throw new Error("case timeout must be a positive number of milliseconds");
      }
      const result = vm.runInContext(`(async () => { ${String(test.script)} })()`, sandbox, { timeout: timeoutMs });
      await new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error(`bridge case timed out after ${timeoutMs}ms`)), timeoutMs,
        );
        Promise.resolve(result).then(
          (value) => { clearTimeout(timer); resolve(value); },
          (error) => { clearTimeout(timer); reject(error); },
        );
      });
      process.stdout.write(JSON.stringify({ id: test.id, ok: true }) + "\n");
    } catch (error) {
      process.stdout.write(JSON.stringify({ id: test.id, ok: false, error: String(error && error.stack || error) }) + "\n");
    }
  }
});
