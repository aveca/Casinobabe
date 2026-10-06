// Vérifie que LiveErrorCapture installe réellement un handler d'erreurs :
// sans seterrorhandler(), la capture est du code mort (CasinobabeErrorBus
// reste vide, CasinobabeDB.lastError jamais écrit) et on reste aveugle sur
// les plantages en live.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Lua } from "wasmoon-lua5.1";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LIVE_WOW_DIR =
  "C:\\Program Files (x86)\\World of Warcraft\\_anniversary_\\Interface\\AddOns\\Casinobabe";
const RUNTIME_DIR = path.join(ROOT, "runtime-addon", "Casinobabe");
const argDir = process.argv[2];
const ADDON_DIR = argDir
  ? path.resolve(argDir)
  : fs.existsSync(path.join(LIVE_WOW_DIR, "Casinobabe.lua"))
    ? LIVE_WOW_DIR
    : RUNTIME_DIR;

const failures = [];
const notes = [];
const ok = (step, message) => console.log(`  ok   [${step}] ${message}`);
const fail = (step, message) => {
  failures.push({ step, message });
  console.log(`  FAIL [${step}] ${message}`);
};

const lua = await Lua.create();
await lua.doString(fs.readFileSync(path.join(ROOT, "tests", "wow_mock.lua"), "utf-8"));

// Chargement identique au client : SavedVariables puis libs puis addon.
const sv =
  "C:\\Program Files (x86)\\World of Warcraft\\_anniversary_\\WTF\\Account\\YACOV972\\SavedVariables\\Casinobabe.lua";
if (fs.existsSync(sv)) await lua.doString(fs.readFileSync(sv, "utf-8"));

for (const file of [
  "Libs\\LibStub\\LibStub.lua",
  "Libs\\LibDBIcon-1.0\\LibDBIcon-1.0.lua",
  "LiveErrorCapture.lua",
  "Casinobabe.lua",
]) {
  const p = path.join(ADDON_DIR, file);
  if (fs.existsSync(p)) {
    try {
      await lua.doString(fs.readFileSync(p, "utf-8"));
      ok("load", file);
    } catch (e) {
      fail("load", `${file} -> ${typeof e === "string" ? e : e.message}`);
    }
  } else {
    fail("load", `${file} absent de ${ADDON_DIR}`);
  }
}

// Le handler doit être branché au ADDON_LOADED de Casinobabe.
try {
  await lua.doString('WoWMock.fireEvent("ADDON_LOADED", "Casinobabe")');
  const installed = await lua.doString("return WoWMock.config.errorHandler ~= nil");
  if (installed === true || installed === "true") ok("handler", "seterrorhandler installé");
  else fail("handler", "seterrorhandler NON installé (capture inopérante)");
} catch (e) {
  fail("handler", typeof e === "string" ? e : e.message);
}

// Une erreur non catchée dans un handler doit atterrir dans CasinobabeDB.lastError.
try {
  await lua.doString(`
local probe = CreateFrame("Frame", "CBErrorProbe")
probe:RegisterEvent("CHAT_MSG_SAY")
probe:SetScript("OnEvent", function() error("PROBE_BOOM: failure reproduce") end)
WoWMock.fireEvent("CHAT_MSG_SAY", "hello")`);
} catch (e) {
  fail("probe", typeof e === "string" ? e : e.message);
}

const persisted = String(
  (await lua.doString(`
if not CasinobabeDB or not CasinobabeDB.lastError then return "NONE" end
local e = CasinobabeDB.lastError
return table.concat({
  tostring(e.message or ""),
  tostring(e.file or ""),
  tostring(e.line or 0),
  tostring(CasinobabeDB.lastErrorCount or 0),
  tostring(CasinobabeDB.lastErrorIncident or ""),
}, "\\t")`)) || ""
);

if (!persisted || persisted === "NONE") {
  fail("persist", "CasinobabeDB.lastError absent : l'erreur live n'est jamais enregistrée");
} else {
  const [message, file, line, count, incident] = persisted.split("\t");
  if (message.includes("PROBE_BOOM")) ok("persist", `message capturé: ${message.slice(0, 80)}`);
  else fail("persist", `message inattendu: ${message}`);
  if (file && file !== "unknown") ok("persist", `fichier: ${file}:${line}`);
  else fail("persist", `emplacement non analysé (file=${file})`);
  if (Number(count) >= 1) ok("persist", `compteurs: count=${count} id=${incident}`);
  else fail("persist", `lastErrorCount=${count}`);
}

// Le handler précédent (UI d'erreur par défaut) doit rester chaîné.
const chained = await lua.doString(`
for _, e in ipairs(WoWMock.errors) do
  if e.phase == "ERROR_HANDLER" then return "yes" end
end
return "no"`);
if (String(chained) === "yes") ok("chaining", "handler précédent appelé (l'UI d'erreur par défaut reste active)");
else notes.push("handler précédent non observé (l'erreur n'a pas été retransmise)");

console.log("\n=== RAPPORT ERROR CAPTURE ===");
console.log(`Échecs: ${failures.length}`);
for (const n of notes) console.log(`note: ${n}`);
for (const f of failures) console.log(`FAIL: [${f.step}] ${f.message}`);
const exitCode = failures.length ? 1 : 0;
console.log(`EXIT CODE: ${exitCode}`);
process.exit(exitCode);
