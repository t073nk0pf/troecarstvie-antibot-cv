const fields = [
  "configPath",
  "maxCycles",
  "targetLevels",
  "targetNames",
  "goalLevel",
  "maxDeathsPerSession",
  "targetLocationName",
  "autoNavigateQuestTargets",
  "startDelay",
  "healthMinPercent",
  "prowessMinPercent",
  "recoverToPercent",
  "recoveryHealthThreshold",
  "recoveryProwessThreshold",
  "maxUsesPerResource",
  "inventoryOpenDelayMs",
  "skillSlot1",
  "skillSlot2",
  "skillSlot3",
  "skillSlot4",
  "skillSlot5",
  "skillSlot6",
  "combatFallbackZeroEnabled",
  "combatFallbackProwessPercent",
  "combatClickIntervalMs",
  "combatPreClickDelayMs",
  "combatClickHoldMs",
  "battleItemRecoveryEnabled",
  "battleHealthPotionThreshold",
  "battleProwessPotionThreshold",
  "battleHealthPotionSlots",
  "battleProwessPotionSlots",
  "battleHealthPotionNames",
  "battleProwessPotionNames",
  "battleDamageBoostEnabled",
  "battleDamageBoostSlots",
  "battleDamageBoostNames",
  "battleDamageBoostChancePercent",
  "battleItemCooldownMs",
  "battleItemMaxUsesPerBattle",
  "live",
  "noActivateApp",
  "openHuntOnStart",
  "itemRecoveryEnabled",
];

const $ = (id) => document.getElementById(id);

const defaults = {
  configPath: "config/automation.local.json",
  maxCycles: 50,
  targetLevels: "",
  targetNames: "",
  startDelay: 1,
  healthMinPercent: 90,
  prowessMinPercent: 90,
  recoverToPercent: 90,
  recoveryHealthThreshold: 90,
  recoveryProwessThreshold: 90,
  maxUsesPerResource: 4,
  inventoryOpenDelayMs: 1500,
  skillSlot1: false,
  skillSlot2: true,
  skillSlot3: true,
  skillSlot4: false,
  skillSlot5: false,
  skillSlot6: false,
  combatFallbackZeroEnabled: true,
  combatFallbackProwessPercent: 1,
  combatClickIntervalMs: 900,
  combatPreClickDelayMs: 0,
  combatClickHoldMs: 0,
  battleItemRecoveryEnabled: false,
  battleHealthPotionThreshold: 35,
  battleProwessPotionThreshold: 15,
  battleHealthPotionSlots: "",
  battleProwessPotionSlots: "",
  battleHealthPotionNames: "",
  battleProwessPotionNames: "",
  battleDamageBoostEnabled: false,
  battleDamageBoostSlots: "",
  battleDamageBoostNames: "",
  battleDamageBoostChancePercent: 100,
  battleItemCooldownMs: 3000,
  battleItemMaxUsesPerBattle: 1,
  goalLevel: null,
  maxDeathsPerSession: 3,
  targetLocationName: "",
  autoNavigateQuestTargets: false,
  live: false,
  noActivateApp: true,
  openHuntOnStart: true,
  itemRecoveryEnabled: true,
};

async function api(path, options = {}) {
  const response = await localFetch(`/api${path}`, options);
  const data = response.data || {};
  if (!response.ok) {
    throw new Error(data.error || response.error || `HTTP ${response.status}`);
  }
  return data;
}

function localFetch(path, options = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      {
        type: "antibot-cv-local-fetch",
        request: {
          path,
          method: options.method || "GET",
          body: options.body,
        },
      },
      (response) => {
        const error = chrome.runtime.lastError;
        if (error) {
          reject(new Error(error.message));
          return;
        }
        resolve(response || { ok: false, status: 0, error: "local_fetch_failed" });
      }
    );
  });
}

function readSettings() {
  const settings = {};
  for (const id of fields) {
    const input = $(id);
    if (!input) {
      continue;
    }
    if (input.type === "checkbox") {
      settings[id] = input.checked;
    } else if (input.type === "number" || input.type === "range") {
      settings[id] = input.value === "" ? null : Number(input.value);
    } else {
      settings[id] = input.value.trim();
    }
  }
  settings.recoveryThreshold = settings.recoveryProwessThreshold;
  settings.targetLevels = String(settings.targetLevels || "")
    .replace(/,/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value > 0);
  settings.targetNames = parseNameList(settings.targetNames);
  settings.combatSlotSequence = [1, 2, 3, 4, 5, 6].filter((slot) => Boolean($(`skillSlot${slot}`)?.checked));
  settings.battleHealthPotionSlots = parseNumberList(settings.battleHealthPotionSlots);
  settings.battleProwessPotionSlots = parseNumberList(settings.battleProwessPotionSlots);
  settings.battleDamageBoostSlots = parseNumberList(settings.battleDamageBoostSlots);
  settings.battleHealthPotionNames = parseNameList(settings.battleHealthPotionNames);
  settings.battleProwessPotionNames = parseNameList(settings.battleProwessPotionNames);
  settings.battleDamageBoostNames = parseNameList(settings.battleDamageBoostNames);
  return settings;
}

function parseNumberList(value) {
  return String(value || "")
    .replace(/,/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((item) => Number(item))
    .filter((item, index, array) => Number.isInteger(item) && item >= 0 && array.indexOf(item) === index);
}

function parseNameList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function writeSettings(settings) {
  const merged = { ...defaults, ...settings };
  if (settings && Array.isArray(settings.combatSlotSequence)) {
    for (let slot = 1; slot <= 6; slot += 1) {
      merged[`skillSlot${slot}`] = settings.combatSlotSequence.includes(slot);
    }
  }
  if (settings && typeof settings.combatSlotSequence === "string") {
    const selected = settings.combatSlotSequence
      .replace(/,/g, " ")
      .split(/\s+/)
      .filter(Boolean)
      .map((value) => Number(value));
    for (let slot = 1; slot <= 6; slot += 1) {
      merged[`skillSlot${slot}`] = selected.includes(slot);
    }
  }
  if (settings && settings.recoveryThreshold != null) {
    merged.recoveryHealthThreshold = settings.recoveryHealthThreshold ?? settings.recoveryThreshold;
    merged.recoveryProwessThreshold = settings.recoveryProwessThreshold ?? settings.recoveryThreshold;
  }
  for (const id of fields) {
    const input = $(id);
    if (!input) {
      continue;
    }
    const value = merged[id];
    if (input.type === "checkbox") {
      input.checked = Boolean(value);
    } else if (id === "targetLevels" && Array.isArray(value)) {
      input.value = value.join(",");
    } else if ((id === "battleHealthPotionSlots" || id === "battleProwessPotionSlots" || id === "battleDamageBoostSlots") && Array.isArray(value)) {
      input.value = value.join(",");
    } else if ((id === "targetNames" || id === "battleHealthPotionNames" || id === "battleProwessPotionNames" || id === "battleDamageBoostNames") && Array.isArray(value)) {
      input.value = value.join(", ");
    } else {
      input.value = value ?? "";
    }
  }
}

async function saveSettings() {
  await chrome.storage.local.set({ antibotCvSettings: readSettings() });
}

async function loadSettings() {
  const stored = await chrome.storage.local.get("antibotCvSettings");
  writeSettings(stored.antibotCvSettings || defaults);
}

function renderDamageBoostChance() {
  const value = Math.max(0, Math.min(100, Number($("battleDamageBoostChancePercent")?.value || 0)));
  const output = $("battleDamageBoostChanceValue");
  if (output) {
    output.textContent = `${value}%`;
  }
}

function setStatusText(id, text) {
  $(id).textContent = text == null || text === "" ? "-" : String(text);
}

function selectedClientId() {
  return $("clientId")?.value || "";
}

function shortClientId(clientId) {
  const value = String(clientId || "");
  return value.length > 10 ? value.slice(-10) : value;
}

function clientLabel(client, statusClient) {
  const state = client.running
    ? "RUNNING"
    : statusClient && statusClient.client_seen
      ? "online"
      : client.ok
        ? "current"
        : "offline";
  const cycles =
    client.completed_cycles != null && client.requested_cycles != null
      ? ` ${client.completed_cycles}/${client.requested_cycles}`
      : "";
  const page = client.title || client.href || statusClient?.title || statusClient?.href || "";
  const suffix = page ? ` - ${page.replace(/^https?:\/\//, "").slice(0, 46)}` : "";
  return `${state}${cycles} ${shortClientId(client.client_id || client.clientId)}${suffix}`;
}

function sendMessageToTab(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(response || null);
    });
  });
}

async function loadCurrentClient() {
  const select = $("clientId");
  if (!select) {
    return null;
  }
  select.innerHTML = "";
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  } catch (_) {
    tabs = [];
  }
  const tab = tabs[0];
  if (!tab || !tab.id) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Активная вкладка не найдена";
    select.appendChild(option);
    select.disabled = true;
    return null;
  }
  let client = null;
  try {
    client = await sendMessageToTab(tab.id, { type: "antibot-cv-current-client" });
  } catch (_) {
    client = null;
  }
  const option = document.createElement("option");
  if (!client || !client.ok || !client.clientId) {
    option.value = "";
    option.textContent = "Открой поп-ап на вкладке игры";
    select.appendChild(option);
    select.disabled = true;
    return null;
  }
  option.value = String(client.clientId);
  option.textContent = clientLabel(client);
  select.appendChild(option);
  select.value = option.value;
  select.disabled = true;
  return client;
}

function renderCurrentClientOption(currentClient, status) {
  const select = $("clientId");
  if (!select || !currentClient || !currentClient.clientId) {
    return;
  }
  const option = select.options[0] || document.createElement("option");
  option.value = String(currentClient.clientId);
  option.textContent = clientLabel(
    {
      ...currentClient,
      client_id: currentClient.clientId,
      running: Boolean(status.running),
      completed_cycles: status.last_status?.completed_cycles,
      requested_cycles: status.last_status?.requested_cycles,
    },
    status.client || {}
  );
  if (!select.options.length) {
    select.appendChild(option);
  }
  select.value = option.value;
}

function renderStatus(status, currentClient) {
  const running = Boolean(status.running);
  const hasCurrentClient = Boolean(currentClient && currentClient.clientId && selectedClientId());
  $("runBadge").textContent = running ? "running" : "idle";
  $("runBadge").classList.toggle("running", running);
  $("startButton").disabled = running || !hasCurrentClient;
  $("questRunButton").disabled = running || !hasCurrentClient;
  $("stopButton").disabled = !running || !hasCurrentClient;
  $("updateExtensionButton").disabled = Boolean(status.any_running);

  const client = status.client || {};
  const clientText = !hasCurrentClient
    ? "открой поп-ап на вкладке игры"
    : client.client_seen
    ? `${client.version_ok ? "ok" : "version mismatch"} ${client.client_id || ""}`
    : "не виден";
  setStatusText("serverStatus", status.ok ? "Python control-server доступен" : "Нет ответа сервера");
  setStatusText("clientStatus", clientText);
  renderCurrentClientOption(currentClient, status);

  const last = status.last_status || {};
  setStatusText("botState", last.state || (running ? "STARTING" : "STOPPED"));
  setStatusText("cycleStatus", `${last.completed_cycles ?? 0}/${last.requested_cycles ?? "?"}`);
  const playerText = last.character_name
    ? `${last.character_name} [${last.current_level ?? "?"}] XP ${last.current_xp_percent ?? "?"}%`
    : "-";
  setStatusText("playerStatus", playerText);
  setStatusText("goalStatus", last.goal_level ? `уровень ${last.goal_level}` : "не задана");
  setStatusText(
    "planStatus",
    last.leveling_intent ? `${last.leveling_intent}: ${last.leveling_reason || ""}` : "-"
  );
  setStatusText("deathStatus", `${last.deaths_observed ?? 0}/${settingsMaxDeaths()}`);
  setStatusText("actionStatus", last.total_actions ?? 0);
  setStatusText("errorStatus", status.last_error || last.last_error || last.error_reason || last.errors || "-");
}

function settingsMaxDeaths() {
  const value = Number($("maxDeathsPerSession")?.value);
  return Number.isInteger(value) && value >= 0 ? value : "?";
}

async function refreshStatus() {
  try {
    const currentClient = await loadCurrentClient();
    const clientId = selectedClientId();
    if (!clientId) {
      renderStatus(
        {
          ok: true,
          running: false,
          last_status: {},
          client: { client_seen: false, version_ok: false, client_id: "" },
        },
        currentClient
      );
      return;
    }
    const status = await api(`/status?clientId=${encodeURIComponent(clientId)}`);
    renderStatus(status, currentClient);
  } catch (error) {
    $("runBadge").textContent = "offline";
    $("runBadge").classList.remove("running");
    $("startButton").disabled = true;
    $("questRunButton").disabled = true;
    $("stopButton").disabled = true;
    $("updateExtensionButton").disabled = true;
    setStatusText("serverStatus", "Сначала запусти control-server в терминале");
    setStatusText("clientStatus", "-");
    setStatusText("botState", "-");
    setStatusText("cycleStatus", "-");
    setStatusText("actionStatus", "-");
    setStatusText("errorStatus", error.message);
  }
}

async function startBot() {
  await saveSettings();
  const settings = readSettings();
  settings.clientId = selectedClientId();
  if (!settings.clientId) {
    throw new Error("Открой поп-ап на нужной вкладке игры, чтобы привязать текущий client_id.");
  }
  settings.autonomousQuestDirector = false;
  settings.autoNavigateQuestTargets = false;
  settings.openHuntOnStart = true;
  await api("/start", { method: "POST", body: settings });
  await refreshStatus();
}

async function startQuestBot() {
  await saveSettings();
  const settings = readSettings();
  settings.clientId = selectedClientId();
  if (!settings.clientId) {
    throw new Error("Открой поп-ап на нужной вкладке игры, чтобы привязать текущий client_id.");
  }
  if (settings.live !== true) {
    throw new Error("Для реального выполнения квестов включи live.");
  }
  settings.autonomousQuestDirector = true;
  settings.autoNavigateQuestTargets = true;
  settings.openHuntOnStart = false;
  settings.requiredCharacterName = "";
  settings.goalLevel = null;
  settings.targetLocationName = "";
  await api("/start", { method: "POST", body: settings });
  await refreshStatus();
}

async function stopBot() {
  const clientId = selectedClientId();
  if (!clientId) {
    throw new Error("Открой поп-ап на нужной вкладке игры, чтобы остановить ее экземпляр.");
  }
  await api("/stop", { method: "POST", body: { clientId } });
  await refreshStatus();
}

async function updateExtension() {
  const status = await api("/status");
  const currentClient = await loadCurrentClient();
  const currentVersion = String(
    currentClient?.version || status.client?.client_version || "unknown-pre-updater"
  ).trim();
  await globalThis.AntibotCvUpdateRuntime.requestExtensionUpdate({
    chromeApi: chrome,
    status,
    currentVersion,
  });
}

function renderSkillScan(data) {
  const abilities = Array.isArray(data.abilities) ? data.abilities : [];
  const bySlot = new Map();
  for (const ability of abilities) {
    const slot = Number(ability && ability.slot);
    if (Number.isInteger(slot) && slot >= 1 && slot <= 6 && !bySlot.has(slot)) {
      bySlot.set(slot, ability);
    }
  }
  for (let slot = 1; slot <= 6; slot += 1) {
    const label = document.querySelector(`[data-slot-label="${slot}"]`);
    const ability = bySlot.get(slot);
    if (label) {
      label.textContent = ability ? ability.name || `slot ${slot}` : "-";
      label.title = ability ? `${ability.name || ""} id=${ability.id ?? ""}` : "";
    }
  }
  const summary = abilities.length ? `Скиллы: ${abilities.length}` : data.message || "Скиллы не найдены";
  setStatusText("errorStatus", summary);
}

async function scanSkills() {
  const clientId = selectedClientId();
  if (!clientId) {
    throw new Error("Открой поп-ап на вкладке боя нужного окна.");
  }
  const data = await api(clientId ? `/battle-skills?clientId=${encodeURIComponent(clientId)}` : "/battle-skills");
  renderSkillScan(data);
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  renderDamageBoostChance();
  await loadCurrentClient().catch(() => {});
  for (const id of fields) {
    const input = $(id);
    if (input) {
      input.addEventListener("change", saveSettings);
    }
  }
  $("battleDamageBoostChancePercent")?.addEventListener("input", renderDamageBoostChance);
  $("startButton").addEventListener("click", () => startBot().catch((error) => setStatusText("errorStatus", error.message)));
  $("questRunButton").addEventListener("click", () => startQuestBot().catch((error) => setStatusText("errorStatus", error.message)));
  $("stopButton").addEventListener("click", () => stopBot().catch((error) => setStatusText("errorStatus", error.message)));
  $("refreshButton").addEventListener("click", refreshStatus);
  $("updateExtensionButton").addEventListener("click", () => {
    $("updateExtensionButton").disabled = true;
    updateExtension().catch((error) => {
      setStatusText("errorStatus", error.message);
      refreshStatus();
    });
  });
  $("scanSkillsButton").addEventListener("click", () => scanSkills().catch((error) => setStatusText("errorStatus", error.message)));
  await refreshStatus();
  setInterval(refreshStatus, 1500);
});
