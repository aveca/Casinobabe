const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn, spawnSync } = require("child_process");

const repo = path.resolve(process.env.CASINOBAE_REPO || path.join(__dirname, ".."));
const addonLive = process.env.CASINOBAE_WOW_ADDON || "";
const pollMs = Math.max(750, Number(process.env.CASINOBAE_POLL_MS || 1200));
const ollamaUrl = process.env.CASINOBAE_LLM_URL || "http://127.0.0.1:11434/api/generate";
const ollamaModel = process.env.CASINOBAE_LLM_MODEL || "nemotron";
const opencodeCmd = process.env.OPENCODE_CMD || "opencode";

const stateDir = path.join(repo, ".casino-errors");
const inboxDir = path.join(stateDir, "inbox");
const statePath = path.join(stateDir, "sentinel-state.json");
fs.mkdirSync(inboxDir, { recursive: true });

let state = {};
try { state = JSON.parse(fs.readFileSync(statePath, "utf8")); } catch (_) {}
if (!state.files) state.files = {};
let active = false;
let queued = null;

function saveState() {
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2), "utf8");
}

function sha(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

function readText(file) {
  try {
    const st = fs.statSync(file);
    return { text: fs.readFileSync(file, "utf8"), mtimeMs: st.mtimeMs };
  } catch (_) { return null; }
}

function relevant(file, text) {
  const base = path.basename(file).toLowerCase();
  return text.includes("CasinobabeErrorBus") ||
    text.includes("Casinobabe.lua") ||
    (base.includes("buggrabber") && text.includes("Casinobabe"));
}

function makeIncident(file) {
  const snap = readText(file);
  if (!snap || !relevant(file, snap.text)) return null;

  const key = path.resolve(file);
  const digest = sha(snap.text);
  if (state.files[key]?.hash === digest) return null;

  state.files[key] = { hash: digest, mtimeMs: snap.mtimeMs };
  saveState();

  return {
    id: `${Date.now()}-${digest.slice(0, 12)}`,
    capturedAt: new Date().toISOString(),
    sourceFile: key,
    sourceSha256: digest,
    text: snap.text.slice(-160000),
  };
}

async function localDiagnosis(incident) {
  const prompt = [
    "World of Warcraft Lua addon incident. Diagnose only from evidence.",
    "Return JSON only.",
    'Schema: {"root_cause":"string","confidence":0.0,"symbols":["string"],"severity":"LOW|MEDIUM|HIGH","next_action":"string"}',
    JSON.stringify(incident, null, 2),
  ].join("\n");

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const response = await fetch(ollamaUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: ollamaModel, prompt, stream: false, format: "json" }),
      signal: controller.signal,
    });
    clearTimeout(timeout);
    if (!response.ok) return null;
    const body = await response.json();
    const raw = body.response || body.message?.content || "";
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (_) { return null; }
}

function runOpenCode(incident) {
  const prompt = [
    "CASINOBAE SELF-HEALING INCIDENT.",
    "Read AGENTS.md first.",
    "Repair the real runtime incident; do not merely explain it.",
    "ROOT CAUSE -> SEARCH ALL REFERENCES -> FIX -> TEST -> SYNC LIVE -> SHA VERIFY -> COMMIT -> PUSH -> PR -> CONTINUE.",
    "Never hard-code dealer identity.",
    "Never let DEMO perform real actions.",
    "Verify any local-LLM diagnosis against source.",
    JSON.stringify(incident, null, 2),
  ].join("\n");

  return new Promise((resolve) => {
    const child = spawn(opencodeCmd, ["run", prompt], {
      cwd: repo,
      shell: true,
      stdio: "inherit",
      env: process.env,
    });
    child.on("error", () => resolve(1));
    child.on("exit", (code) => resolve(Number(code || 0)));
  });
}

function syncLive() {
  if (!addonLive || !fs.existsSync(addonLive)) {
    return { ok: false, reason: "WoW LIVE addon path unavailable" };
  }

  const src = path.join(repo, "runtime-addon", "Casinobabe");
  if (!fs.existsSync(src)) return { ok: false, reason: "runtime source unavailable" };

  const backup = path.join(addonLive, `.casino-backup-${Date.now()}`);
  try {
    fs.cpSync(addonLive, backup, { recursive: true });
  } catch (e) {
    return { ok: false, reason: "LIVE backup failed: " + e.message };
  }

  const copy = spawnSync("robocopy", [src, addonLive, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"], {
    shell: true,
    encoding: "utf8",
  });

  if (copy.status === null || copy.status > 7) {
    return { ok: false, reason: "robocopy failed with exit=" + copy.status };
  }

  const srcLua = fs.readFileSync(path.join(src, "Casinobabe.lua"));
  const liveLua = fs.readFileSync(path.join(addonLive, "Casinobabe.lua"));
  const srcHash = crypto.createHash("sha256").update(srcLua).digest("hex");
  const liveHash = crypto.createHash("sha256").update(liveLua).digest("hex");

  return { ok: srcHash === liveHash, srcHash, liveHash, backup };
}

function discoverSavedVariables() {
  const explicit = [process.env.CASINOBAE_SV_PATH, process.env.BUGGRABBER_SV_PATH].filter(Boolean);
  if (explicit.length) return [...new Set(explicit.filter(fs.existsSync))];

  const root = process.env.CASINOBAE_WOW_ROOT || "";
  const wtf = root ? path.join(root, "WTF", "Account") : "";
  const found = [];

  function walk(dir) {
    let names;
    try { names = fs.readdirSync(dir); } catch (_) { return; }
    for (const name of names) {
      const full = path.join(dir, name);
      let st;
      try { st = fs.statSync(full); } catch (_) { continue; }
      if (st.isDirectory()) walk(full);
      else if (name === "Casinobabe.lua" || name === "!BugGrabber.lua") found.push(full);
    }
  }

  if (wtf && fs.existsSync(wtf)) walk(wtf);
  return found;
}

async function processIncident(incident) {
  if (active) { queued = incident; return; }
  active = true;

  try {
    incident.localLLM = await localDiagnosis(incident);

    const incidentFile = path.join(inboxDir, incident.id + ".json");
    fs.writeFileSync(incidentFile, JSON.stringify(incident, null, 2), "utf8");

    incident.agentExitCode = await runOpenCode(incident);
    if (incident.agentExitCode === 0) incident.liveSync = syncLive();

    fs.writeFileSync(incidentFile, JSON.stringify(incident, null, 2), "utf8");
  } finally {
    active = false;
    if (queued) {
      const next = queued;
      queued = null;
      await processIncident(next);
    }
  }
}

async function poll() {
  for (const file of discoverSavedVariables()) {
    const incident = makeIncident(file);
    if (incident) await processIncident(incident);
  }
}

console.log("[CasinoBae] LIVE self-healing sentinel");
console.log("[CasinoBae] repo=" + repo);
console.log("[CasinoBae] WoW addon=" + (addonLive || "(discover via watch script)"));
console.log("[CasinoBae] Ollama=" + ollamaUrl + " model=" + ollamaModel);

poll().catch(() => {});
setInterval(() => poll().catch(() => {}), pollMs);
