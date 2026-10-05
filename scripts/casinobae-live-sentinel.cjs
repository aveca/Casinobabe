#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const REPO = path.resolve(__dirname, "..");
const STATE_DIR = path.join(REPO, "state");
const LOG_DIR = path.join(REPO, "logs");
const REPORT_DIR = path.join(REPO, "reports");
const STATE_FILE = path.join(STATE_DIR, "live-sentinel-state.json");
const QUEUE_FILE = path.join(STATE_DIR, "live-queue.json");
const INCIDENTS_FILE = path.join(STATE_DIR, "live-incidents.json");
const JSONL_FILE = path.join(LOG_DIR, "live-incidents.jsonl");
const ERROR_LOG = path.join(LOG_DIR, "live-errors.log");
const REPORT_FILE = path.join(REPORT_DIR, "live-errors.json");
const STATUS_FILE = path.join(REPORT_DIR, "live-status.json");

const MAX_EVENTS = 1000;
const MAX_INCIDENTS = 500;
const DEFAULT_POLL_MS = 5000;

function ensureDirs() {
  for (const dir of [STATE_DIR, LOG_DIR, REPORT_DIR]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function atomicWrite(file, value) {
  ensureDirs();
  const tmp = file + ".tmp-" + process.pid;
  fs.writeFileSync(tmp, value, "utf8");
  fs.renameSync(tmp, file);
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function sha256(value) {
  return crypto.createHash("sha256").update(String(value), "utf8").digest("hex");
}

function normalizeMessage(value) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b/g, "<TS>")
    .replace(/\b(?:session|player|run|id)[=_:-][A-Za-z0-9._-]+/gi, "<DYNAMIC>")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function relevantStackFrame(stack) {
  return String(stack || "")
    .split(/\r?\n/)
    .map(function (s) { return s.trim(); })
    .find(function (s) { return /Casinobabe(?:\.lua|:)/i.test(s); }) || "";
}

function computeFingerprint(event) {
  return sha256([
    event.addon || "Casinobabe",
    event.file || "unknown",
    event.line || 0,
    normalizeMessage(event.message),
    relevantStackFrame(event.stack)
  ].join("|"));
}

function classifySeverity(event) {
  const text = [
    event.message,
    event.stack,
    event.phase,
    event.game,
    event.dealerState
  ].filter(Boolean).join(" ").toLowerCase();

  if (/(double[- ]?payout|duplicate gold|gold duplication|balance corruption|payout corruption|data loss|security boundary|protected action bypass)/i.test(text)) {
    return "P0";
  }

  if (/(roll_rejected|roll rejected|game[-_ ]?sync|stale session|win.*payout.*trade|payout.*trade|dealer|capital_show|lucky_night|attempt to (index|call)|nil value|onc?lick|onevent|show callback|show flow|trade flow)/i.test(text)) {
    return "P1";
  }

  if (/(cosmetic|visual)/i.test(text)) {
    return "P3";
  }

  if (/(timer|ticker|ui|frame|render|performance|update|warning|telemetry|non[- ]?blocking)/i.test(text)) {
    return "P2";
  }

  if (/(style|documentation|comment|todo)/i.test(text)) {
    return "P3";
  }

  return "P2";
}

function decodeLuaString(raw) {
  return String(raw || "")
    .replace(/\\n/g, "\n")
    .replace(/\\r/g, "\r")
    .replace(/\\t/g, "\t")
    .replace(/\\\\/g, "\\")
    .replace(/\"/g, '"');
}

function extractSavedVariableJournal(content) {
  const match = String(content).match(/(?:\[\s*["']?liveErrorJournal["']?\s*\]|\bliveErrorJournal\b)\s*=\s*"((?:\\.|[^"])*)"/s);
  return match ? decodeLuaString(match[1]) : "";
}

function parseEscapedFields(line) {
  const fields = [];
  let current = "";
  let escaped = false;

  for (const ch of String(line)) {
    if (escaped) {
      if (ch === "p") current += "|";
      else if (ch === "n") current += "\n";
      else if (ch === "r") current += "\r";
      else current += ch;
      escaped = false;
    } else if (ch === "\\") {
      escaped = true;
    } else if (ch === "|") {
      fields.push(current);
      current = "";
    } else {
      current += ch;
    }
  }

  if (escaped) current += "\\";
  fields.push(current);
  return fields;
}

function parseJournalLine(raw) {
  const parts = parseEscapedFields(raw);
  if (parts[0] !== "CBERR" || parts.length < 9) return null;

  return {
    raw: raw,
    timestamp: parts[1] || new Date().toISOString(),
    addon: parts[2] || "Casinobabe",
    file: parts[3] || "unknown",
    line: Number(parts[4]) || 0,
    message: parts[5] || "",
    stack: parts[6] || "",
    phase: parts[7] || "UNKNOWN",
    dealerState: parts[8] || null,
    game: parts[9] || null
  };
}

function findWowRoots() {
  const candidates = [
    process.env.CASINOBAE_WOW_ROOT,
    "C:\\Program Files (x86)\\World of Warcraft\\_anniversary_",
    "C:\\Program Files\\World of Warcraft\\_anniversary_",
    "C:\\Program Files (x86)\\World of Warcraft",
    "C:\\Program Files\\World of Warcraft"
  ].filter(Boolean);

  return Array.from(new Set(candidates)).filter(function (p) {
    return fs.existsSync(p);
  });
}

function findSavedVariableFiles(root) {
  const files = [];
  const direct = process.env.CASINOBAE_WOW_SAVED_VARIABLES;

  if (direct && fs.existsSync(direct)) files.push(direct);

  const start = root ? path.join(root, "WTF") : null;
  if (!start || !fs.existsSync(start)) return files;

  const stack = [start];
  let visited = 0;

  while (stack.length && visited < 50000) {
    const dir = stack.pop();
    visited++;

    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile() && entry.name.toLowerCase() === "casinobabe.lua") files.push(full);
    }
  }

  return Array.from(new Set(files));
}

function loadState() {
  return readJson(STATE_FILE, {
    schema: 1,
    seenEventHashes: [],
    incidents: {},
    lastScan: null,
    lastEvent: null
  });
}

function saveState(state) {
  state.seenEventHashes = state.seenEventHashes.slice(-MAX_EVENTS);
  const entries = Object.entries(state.incidents);
  state.incidents = Object.fromEntries(entries.slice(-MAX_INCIDENTS));
  atomicWrite(STATE_FILE, JSON.stringify(state, null, 2));
}

function loadQueue() {
  const q = readJson(QUEUE_FILE, []);
  return Array.isArray(q) ? q : [];
}

function saveQueue(queue) {
  atomicWrite(QUEUE_FILE, JSON.stringify(queue, null, 2));
}

function appendJsonl(object) {
  ensureDirs();
  fs.appendFileSync(JSONL_FILE, JSON.stringify(object) + "\n", "utf8");
}

function appendLog(line) {
  ensureDirs();
  fs.appendFileSync(ERROR_LOG, line + "\n", "utf8");
}

function rebuildReports(state, queue, health) {
  const incidents = Object.values(state.incidents);
  const summary = { total: incidents.length, P0: 0, P1: 0, P2: 0, P3: 0, open: 0 };

  for (const incident of incidents) {
    summary[incident.severity] = (summary[incident.severity] || 0) + 1;
    if (!["LIVE_VERIFIED"].includes(incident.status)) summary.open++;
  }

  atomicWrite(INCIDENTS_FILE, JSON.stringify(incidents, null, 2));
  atomicWrite(REPORT_FILE, JSON.stringify({
    generatedAt: new Date().toISOString(),
    summary: summary,
    incidents: incidents
  }, null, 2));

  atomicWrite(STATUS_FILE, JSON.stringify({
    generatedAt: new Date().toISOString(),
    sentinel: health,
    queueSize: queue.length,
    priorities: { P0: summary.P0, P1: summary.P1, P2: summary.P2, P3: summary.P3 },
    lastIncident: incidents.length ? incidents[incidents.length - 1].id : null,
    lastLiveObservation: state.lastEvent ? state.lastEvent.timestamp : null,
    liveObserved: false,
    liveVerified: false
  }, null, 2));
}

function processEvent(event, state, queue) {
  const eventHash = sha256(event.raw);
  if (state.seenEventHashes.includes(eventHash)) return false;
  state.seenEventHashes.push(eventHash);

  const fingerprint = computeFingerprint(event);
  const severity = classifySeverity(event);
  const existing = state.incidents[fingerprint];

  if (existing) {
    existing.count += 1;
    existing.lastSeen = event.timestamp;
    existing.occurrences.push({
      timestamp: event.timestamp,
      phase: event.phase,
      line: event.line
    });

    if (existing.status === "LIVE_VERIFIED") existing.status = "REGRESSION";

    if (!queue.some(function (x) { return x.fingerprint === fingerprint; })) {
      queue.push(existing);
    }

    appendJsonl({
      type: "occurrence",
      incidentId: existing.id,
      fingerprint: fingerprint,
      timestamp: event.timestamp,
      count: existing.count
    });

    appendLog("[" + event.timestamp + "] DUP " + existing.severity + " " + existing.id + " x" + existing.count + " " + event.message);
  } else {
    const id = "INC-" + new Date().toISOString().replace(/\D/g, "").slice(0, 14) + "-" + fingerprint.slice(0, 8);
    const incident = {
      id: id,
      source: "WOW_LIVE",
      addon: event.addon,
      fingerprint: fingerprint,
      severity: severity,
      message: event.message,
      stack: event.stack,
      file: event.file,
      line: event.line,
      phase: event.phase,
      dealerState: event.dealerState,
      game: event.game,
      count: 1,
      firstSeen: event.timestamp,
      lastSeen: event.timestamp,
      status: "NEW",
      validationState: "LIVE_PENDING",
      repairCycles: 0,
      occurrences: [{
        timestamp: event.timestamp,
        phase: event.phase,
        line: event.line
      }]
    };

    state.incidents[fingerprint] = incident;
    queue.push(incident);

    appendJsonl({ type: "incident", ...incident });
    appendLog("[" + event.timestamp + "] NEW " + severity + " " + id + " " + event.file + ":" + event.line + " " + event.message);
  }

  state.lastEvent = {
    timestamp: event.timestamp,
    fingerprint: fingerprint,
    file: event.file,
    line: event.line,
    message: event.message
  };

  return true;
}

function scanOnce() {
  ensureDirs();
  const state = loadState();
  const queue = loadQueue();
  const roots = findWowRoots();
  let processed = 0;

  for (const root of roots) {
    const files = findSavedVariableFiles(root);

    for (const file of files) {
      let content;
      try {
        content = fs.readFileSync(file, "utf8");
      } catch {
        continue;
      }

      const journal = extractSavedVariableJournal(content);
      if (!journal) continue;

      for (const raw of journal.split(/\r?\n/).filter(Boolean)) {
        const event = parseJournalLine(raw);
        if (event && processEvent(event, state, queue)) processed++;
      }
    }
  }

  state.lastScan = new Date().toISOString();
  saveState(state);
  saveQueue(queue);
  rebuildReports(state, queue, {
    status: "HEALTHY",
    mode: "once",
    roots: roots,
    processed: processed
  });

  return { processed: processed, queueSize: queue.length, roots: roots };
}

function mainOnce() {
  const result = scanOnce();
  console.log(JSON.stringify(result, null, 2));
  return 0;
}

function mainWatch() {
  let running = false;

  const tick = function () {
    if (running) return;
    running = true;

    try {
      mainOnce();
    } catch (error) {
      ensureDirs();
      appendLog("[" + new Date().toISOString() + "] SENTINEL_ERROR " + (error.stack || error));

      const status = readJson(STATUS_FILE, {});
      atomicWrite(STATUS_FILE, JSON.stringify({
        ...status,
        generatedAt: new Date().toISOString(),
        sentinel: { status: "DEGRADED", error: String(error) }
      }, null, 2));
    } finally {
      running = false;
    }
  };

  tick();
  setInterval(tick, Number(process.env.CASINOBAE_POLL_MS || DEFAULT_POLL_MS));
}

if (require.main === module) {
  ensureDirs();
  const mode = process.argv.includes("--watch") ? "watch" : "once";
  process.exitCode = mode === "watch" ? mainWatch() : mainOnce();
}

module.exports = {
  sha256,
  normalizeMessage,
  relevantStackFrame,
  computeFingerprint,
  classifySeverity,
  parseJournalLine,
  extractSavedVariableJournal,
  findWowRoots,
  findSavedVariableFiles,
  scanOnce,
  processEvent
};
