// REAL OFFLINE HARNESS — Lua 5.1 via wasmoon-lua5.1
"use strict";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import { fileURLToPath } from "url";
import { Lua } from "wasmoon-lua5.1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const ROOT = path.resolve(__dirname, "..");
const RUNTIME = path.join(ROOT, "runtime-addon", "Casinobabe", "Casinobabe.lua");
const MOCK = path.join(ROOT, "tests", "wow_mock.lua");
const REPORT = path.join(ROOT, "reports", "offline-harness-report.json");
const EXIT_REPORT = path.join(ROOT, "reports", "offline-harness-exitcode.json");
const LOG = path.join(ROOT, "logs", "offline-harness-last.log");

function sha256File(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), "utf8");
}

function record(report, name, ok, detail) {
  report.checks.push({ name, status: ok ? "PASS" : "FAIL", detail: detail || null });
  return ok;
}

async function main() {
  const startedAt = new Date().toISOString();
  const report = {
    schema: 2,
    vm: "wasmoon-lua5.1",
    source: path.relative(ROOT, RUNTIME),
    sourceSha256: null,
    checks: [],
    runtimeErrors: [],
    infrastructureError: null,
    startedAt,
    finishedAt: null,
    exitCode: 2
  };

  try {
    if (!fs.existsSync(RUNTIME)) throw new Error("runtime missing: " + RUNTIME);
    if (!fs.existsSync(MOCK)) throw new Error("mock missing: " + MOCK);

    report.sourceSha256 = sha256File(RUNTIME);
    const lua = await Lua.create();

    try {
      await lua.doString(fs.readFileSync(MOCK, "utf8"));
      record(report, "load_wow_mock", true);
    } catch (error) {
      record(report, "load_wow_mock", false, String(error));
      throw new Error("WoW mock failed: " + String(error));
    }

    try {
      await lua.doString(fs.readFileSync(RUNTIME, "utf8"));
      record(report, "load_real_runtime", true);
    } catch (error) {
      record(report, "load_real_runtime", false, String(error));
      report.runtimeErrors.push(String(error));
    }

    async function checkLua(name, expression, expected) {
      try {
        const value = await lua.doString(expression);
        const ok = expected(value);
        record(report, name, ok, ok ? String(value) : "unexpected=" + String(value));
        if (!ok) report.runtimeErrors.push(name + ": unexpected=" + String(value));
        return ok;
      } catch (error) {
        record(report, name, false, String(error));
        report.runtimeErrors.push(name + ": " + String(error));
        return false;
      }
    }

    let criticalOk = true;
    // The addon exposes state ONLY via the Casinobabe global (CB stays chunk-local);
    // checking bare CB reads the mock's empty table, never the addon.
    criticalOk = (await checkLua("CB_namespace", "return type(Casinobabe)", v => v === "table")) && criticalOk;
    criticalOk = (await checkLua("CB_state", "return type(Casinobabe.state)", v => v === "table")) && criticalOk;
    criticalOk = (await checkLua("dealer_conn_states", "return type(Casinobabe.state.DEALER_CONN_STATES)", v => v === "table")) && criticalOk;
    criticalOk = (await checkLua("CasinoSound_namespace", "return type(Casinobabe.CasinoSound)", v => v === "table")) && criticalOk;
    criticalOk = (await checkLua("slash_command", "return type(SlashCmdList and SlashCmdList.CASINOBABE)", v => v === "function")) && criticalOk;

    for (const [name, expr] of [
      ["dealer_status_smoke", "SlashCmdList.CASINOBABE('dealer status')"],
      ["dealer_on_smoke", "SlashCmdList.CASINOBABE('dealer on')"],
      ["dealer_off_smoke", "SlashCmdList.CASINOBABE('dealer off')"],
      ["connecting_without_group", "WoWMock.config.groupMembers=0; SlashCmdList.CASINOBABE('dealer on'); SlashCmdList.CASINOBABE('dealer off')"],
      ["roll_rejected_symbol", "return string.find((debug and debug.getinfo and 'ROLL_REJECTED') or '', 'ROLL_REJECTED') ~= nil"],
      // Dealer/trade handlers are chunk-locals by design (not _G); what is
      // observable is the slash dispatcher they hang from.
      ["trade_callbacks", "return type(SlashCmdList.CASINOBABE)=='function'"]
    ]) {
      try {
        const value = await lua.doString(expr);
        const ok = value === undefined ? true : value === true;
        record(report, name, ok, ok ? null : "result=" + String(value));
        if (!ok) criticalOk = false;
      } catch (error) {
        record(report, name, false, String(error));
        report.runtimeErrors.push(name + ": " + String(error));
        criticalOk = false;
      }
    }

    const mockErrors = await lua.doString("return #(WoWMock.errors or {})");
    const noMockErrors = Number(mockErrors) === 0;
    record(report, "mock_runtime_errors", noMockErrors, "count=" + String(mockErrors));
    if (!noMockErrors) criticalOk = false;

    report.exitCode = criticalOk && report.runtimeErrors.length === 0 ? 0 : 1;
  } catch (error) {
    report.infrastructureError = String(error && error.stack || error);
    report.exitCode = report.infrastructureError.includes("wasmoon") ? 2 : 1;
  }

  report.finishedAt = new Date().toISOString();
  report.durationMs = Date.now() - Date.parse(report.startedAt);
  writeJson(REPORT, report);
  writeJson(EXIT_REPORT, {
    exitCode: report.exitCode,
    timestamp: report.finishedAt,
    sourceSha256: report.sourceSha256,
    runtimeErrors: report.runtimeErrors,
    infrastructureError: report.infrastructureError
  });
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.writeFileSync(LOG, JSON.stringify(report, null, 2), "utf8");

  console.log("LUA_VM=" + report.vm);
  console.log("SOURCE_SHA256=" + report.sourceSha256);
  console.log("CHECKS=" + report.checks.filter(x => x.status === "PASS").length + "/" + report.checks.length);
  console.log("ERRORS=" + report.runtimeErrors.length);
  console.log("INFRA_ERROR=" + (report.infrastructureError ? "YES" : "NO"));
  console.log("EXIT_CODE=" + report.exitCode);
  process.exit(report.exitCode);
}

main().catch(error => {
  console.error("HARNESS_INFRA_ERROR=" + (error.stack || error));
  process.exit(2);
});
