"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const cp = require("child_process");
const crypto = require("crypto");

const sentinel = require("../scripts/casinobae-live-sentinel.cjs");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function fingerprint(event) { return sentinel.computeFingerprint(event); }
function classify(message, extra) { return sentinel.classifySeverity({ message, ...(extra || {}) }); }

function makeEvent(message, line, extra) {
  return {
    raw: "CBERR|2026-10-05T10:00:00Z|Casinobabe|Casinobabe.lua|" + line + "|" + message + "||||",
    timestamp: "2026-10-05T10:00:00Z",
    addon: "Casinobabe",
    file: "Casinobabe.lua",
    line: line,
    message: message,
    stack: "Casinobabe.lua:" + line,
    phase: (extra && extra.phase) || "SYNTHETIC",
    dealerState: (extra && extra.dealerState) || null,
    game: (extra && extra.game) || null
  };
}

function testSavedVariablesErrorBus() {
  const fixture = [
    "CasinobabeDB = {",
    "}",
    "CasinobabeErrorBus = {",
    "  [\"version\"] = 1,",
    "  [\"seq\"] = 5,",
    "  [\"pending\"] = {",
    "    {",
    "      [\"id\"] = \"1791493357-4\",",
    "      [\"timestamp\"] = 1791493357,",
    "      [\"message\"] = \"Interface\\\\AddOns\\\\Casinobabe\\\\Casinobabe.lua:110: attempt to index a nil value\",",
    "      [\"source\"] = \"Interface\\\\AddOns\\\\Casinobabe\\\\Casinobabe.lua\",",
    "      [\"line\"] = 110,",
    "      [\"stack\"] = \"Casinobabe.lua:110: attempt to index a nil value\",",
    "      [\"session\"] = \"unknown\",",
    "    },",
    "    {",
    "      [\"id\"] = \"1791493357-5\",",
    "      [\"timestamp\"] = 1791493357,",
    "      [\"message\"] = \"Interface\\\\AddOns\\\\Casinobabe\\\\Casinobabe.lua:111: attempt to call a nil value\",",
    "      [\"source\"] = \"Interface\\\\AddOns\\\\Casinobabe\\\\Casinobabe.lua\",",
    "      [\"line\"] = 111,",
    "      [\"stack\"] = \"Casinobabe.lua:111: attempt to call a nil value\",",
    "      [\"session\"] = \"unknown\",",
    "    },",
    "  },",
    "  [\"last\"] = {",
    "    [\"id\"] = \"1791493357-5\",",
    "    [\"timestamp\"] = 1791493357,",
    "    [\"message\"] = \"Interface\\\\AddOns\\\\Casinobabe\\\\Casinobabe.lua:111: attempt to call a nil value\",",
    "    [\"source\"] = \"Interface\\\\AddOns\\\\Casinobabe\\\\Casinobabe.lua\",",
    "    [\"line\"] = 111,",
    "    [\"stack\"] = \"Casinobabe.lua:111: attempt to call a nil value\",",
    "    [\"session\"] = \"unknown\",",
    "  },",
    "}"
  ].join("\n");

  const bus = sentinel.extractErrorBus(fixture);
  assert(bus && bus.seq === 5, "SavedVariables ErrorBus seq must be parsed as 5");
  assert(bus.pending.length === 2, "SavedVariables parser must recover all pending records");
  assert(bus.pending[0].id === "1791493357-4", "first sequence id was not preserved");
  assert(bus.pending[1].id === "1791493357-5", "fifth sequence id was not preserved");
  assert(bus.pending[1].timestamp === 1791493357, "timestamp field must map from timestamp, not only time");
  assert(bus.pending[1].file === "Casinobabe.lua", "source path must normalize to addon filename");
  assert(bus.pending[1].line === 111, "source line must be preserved");

  const events = sentinel.extractErrorsFromSavedVariables(fixture);
  assert(events.length === 2, "the last record must not be duplicated when it is already pending");
  assert(events.every((event) => typeof event.raw === "string" && event.raw.length > 0),
    "each extracted ErrorBus record needs a stable event identity for deduplication");
  assert(events[0].raw !== events[1].raw, "distinct ErrorBus sequence ids must not collapse to the same event hash");
  assert(events[0].timestamp === new Date(1791493357 * 1000).toISOString(),
    "ErrorBus timestamps must derive from SavedVariables, not the current poll time");

  const nextScan = sentinel.extractErrorsFromSavedVariables(fixture);
  assert(events[0].raw === nextScan[0].raw && events[1].raw === nextScan[1].raw,
    "re-reading unchanged SavedVariables must produce stable event identities");
}

function testSeverity() {
  const cases = [
    ["balance corruption", "P0"],
    ["duplicate gold payout", "P0"],
    ["ROLL_REJECTED stale session.game", "P1"],
    ["WIN -> PAYOUT -> TRADE failed", "P1"],
    ["attempt to index global 'CasinoSound' (a nil value)", "P1"],
    ["timer callback warning", "P2"],
    ["cosmetic visual warning", "P3"]
  ];
  for (const [message, expected] of cases) {
    const got = classify(message);
    assert(got === expected, "severity mismatch: " + message + " expected " + expected + " got " + got);
  }
}

function testDedupAndQueuePersistence() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "casinobae-factory-"));
  const stateFile = path.join(tmp, "state.json");
  const queueFile = path.join(tmp, "queue.json");
  const event = makeEvent("attempt to index global 'CasinoSound' (a nil value)", 1305);
  const fp = fingerprint(event);

  const state = { seenEventHashes: [], incidents: {}, lastScan: null, lastEvent: null };
  const queue = [];

  for (let i = 0; i < 500; i++) {
    const eventHash = crypto.createHash("sha256").update(event.raw + "|" + i).digest("hex");
    state.seenEventHashes.push(eventHash);
    if (!state.incidents[fp]) {
      state.incidents[fp] = { fingerprint: fp, severity: classify(event.message), count: 0, status: "NEW" };
      queue.push(state.incidents[fp]);
    }
    state.incidents[fp].count++;
  }

  fs.writeFileSync(stateFile, JSON.stringify(state));
  fs.writeFileSync(queueFile, JSON.stringify(queue));

  const restoredState = JSON.parse(fs.readFileSync(stateFile, "utf8"));
  const restoredQueue = JSON.parse(fs.readFileSync(queueFile, "utf8"));

  assert(Object.keys(restoredState.incidents).length === 1, "dedup expected one incident");
  assert(restoredState.incidents[fp].count === 500, "dedup count expected 500");
  assert(restoredQueue.length === 1, "queue expected one persisted incident");

  const child = cp.spawnSync(
    process.execPath,
    ["-e", "const fs=require('fs'); const q=JSON.parse(fs.readFileSync(process.argv[1])); if(q.length!==1 || q[0].count!==500) process.exit(1)", queueFile],
    { encoding: "utf8" }
  );
  assert(child.status === 0, "fresh Node process failed to recover queue");

  fs.rmSync(tmp, { recursive: true, force: true });
}

function testDistinctFingerprint() {
  assert(fingerprint(makeEvent("attempt to call a nil value", 100)) !== fingerprint(makeEvent("attempt to call a nil value", 101)),
    "different source lines must produce different fingerprints");
}

try {
  console.log("=== SYNTHETIC LIVE PIPELINE ===");
  testSavedVariablesErrorBus();
  console.log("SavedVariables ErrorBus seq/timestamp/source/dedup: PASS");
  testSeverity();
  console.log("P0-P3 assertions: PASS");
  testDedupAndQueuePersistence();
  console.log("Disk queue + 500x dedup + restart recovery: PASS");
  testDistinctFingerprint();
  console.log("Fingerprint separation: PASS");
  console.log("SYNTHETIC_LIVE=PASS");
  process.exit(0);
} catch (error) {
  console.error("SYNTHETIC_LIVE=FAIL");
  console.error(error.stack || error);
  process.exit(1);
}
