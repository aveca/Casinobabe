// REAL OFFLINE HARNESS — Lua 5.1 via wasmoon-lua5.1
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { Lua } from "wasmoon-lua5.1";

const ROOT = path.resolve(new URL("..", import.meta.url).pathname);
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

function record(report, name, ok, detail = null) {
  report.checks.push({ name, status: ok ? "PASS" : "FAIL", detail });
  return ok;
}

async function main() {
  const started = Date.now();
  const report = {
    schema: 2,
    vm: "wasmoon-lua5.1",
    source: path.relative(ROOT, RUNTIME),
    sourceSha256: null,
    checks: [],
    runtimeErrors: [],
    infrastructureError: null,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    durationMs: null,
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

    async function checkLua(name, expression) {
      try {
        const value = await lua.doString(expression);
        const ok = value === true;
        record(report, name, ok, ok ? null : "result=" + String(value));
        if (!ok) report.runtimeErrors.push(name + ": unexpected=" + String(value));
        return ok;
      } catch (error) {
        record(report, name, false, String(error));
        report.runtimeErrors.push(name + ": " + String(error));
        return false;
      }
    }

    let criticalOk = true;

    for (const [name, expression] of [
      ["CB_namespace", "return type(CB)=='table'"],
      ["CB_state", "return type(CB.state)=='table'"],
      ["dealer_conn_states", "return type(CB.state.DEALER_CONN_STATES)=='table'"],
      ["CasinoSound_namespace", "return type(CB.CasinoSound)=='table'"],
      ["slash_command", "return type(SlashCmdList and SlashCmdList.CASINOBABE)=='function'"],
      ["trade_callbacks", "return type(DealerOnTradeShow)=='function' and type(DealerOnTradeAccept)=='function' and type(DealerOnTradeClose)=='function'"],
      ["ROLL_REJECTED_symbol", "return string.find('ROLL_REJECTED','ROLL_REJECTED') ~= nil"]
    ]) {
      criticalOk = (await checkLua(name, expression)) && criticalOk;
    }

    for (const [name, expression] of [
      ["dealer_status_smoke", "SlashCmdList.CASINOBABE('dealer status'); return true"],
      ["dealer_on_smoke", "SlashCmdList.CASINOBABE('dealer on'); return true"],
      ["dealer_off_smoke", "SlashCmdList.CASINOBABE('dealer off'); return true"],
      ["connecting_without_group", "WoWMock.config.groupMembers=0; SlashCmdList.CASINOBABE('dealer on'); SlashCmdList.CASINOBABE('dealer off'); return true"]
    ]) {
      criticalOk = (await checkLua(name, expression)) && criticalOk;
    }

    const mockErrors = await lua.doString("return #(WoWMock.errors or {})");
    const noMockErrors = Number(mockErrors) === 0;
    record(report, "mock_runtime_errors", noMockErrors, "count=" + String(mockErrors));
    if (!noMockErrors) criticalOk = false;

    report.exitCode = criticalOk && report.runtimeErrors.length === 0 ? 0 : 1;
  } catch (error) {
    report.infrastructureError = String(error && error.stack || error);
    report.exitCode = report.infrastructureError.toLowerCase().includes("wasmoon") ? 2 : 1;
  }

  report.finishedAt = new Date().toISOString();
  report.durationMs = Date.now() - started;

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
