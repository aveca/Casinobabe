// Test du module peche (CasinobabeFishing.lua) sous le mock WoW.
//
// But : couvrir la resolution du NOM DU SORT dans UNIT_SPELLCAST_*, le
// bug corrige le 20/10 (le client transmet castGUID + spellID, jamais le
// nom -> castObserved restait a 0 -> l'etat EN_COURS n'etait jamais
// atteint -> les prises n'etaient jamais comptees).
//
// Usage : node tests/run-fishing-harness.mjs
"use strict";

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Lua } from "wasmoon-lua5.1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const MOCK = path.join(ROOT, "tests", "wow_mock.lua");
const FISHING = path.join(ROOT, "runtime-addon", "Casinobabe", "CasinobabeFishing.lua");

let failures = 0;
function check(name, ok, detail) {
  if (ok) {
    console.log(`  ok   ${name}`);
  } else {
    console.log(`  FAIL ${name} ${detail || ""}`);
    failures += 1;
  }
}

const lua = await Lua.create();
await lua.doString(fs.readFileSync(MOCK, "utf8"));

// Le mock doit exposer GetSpellInfo (API reelle) pour resoudre l'ID du sort.
await lua.doString([
  "local _realGetSpellInfo = GetSpellInfo",
  "local SPELLS = { [6223] = 'Fishing', [19689] = 'Cast Fishing Line' }",
  "function GetSpellInfo(id)",
  "  if type(id) == 'number' and SPELLS[id] then return SPELLS[id] end",
  "  if _realGetSpellInfo then return _realGetSpellInfo(id) end",
  "  return nil",
  "end",
].join("\n"));

// Le VM Lua sandboxe dofile : on lit le fichier et on l'execute comme
// texte (meme methode que l'autre harness).
let load = "OK";
try {
  await lua.doString(fs.readFileSync(FISHING, "utf8"));
} catch (error) {
  load = String(error);
}
check("module peche charge sans erreur", load === "OK", load);

const hasModule = await lua.doString(
  "return Casinobabe and Casinobabe.Fishing and 'HAS_MODULE' or 'NO_MODULE'"
);
check("module peche expose Casinobabe.Fishing", hasModule === "HAS_MODULE", hasModule);

// Payload REEL de WoW Classic : (unitTarget, castGUID, spellID).
const cast = await lua.doString([
  "CasinobabeDB = CasinobabeDB or {}",
  "local F = Casinobabe.Fishing",
  "F.Activate()",
  "local before = CasinobabeDB.fishing.totals.castObserved",
  "F.OnEvent(nil, 'UNIT_SPELLCAST_SUCCEEDED', 'player', '0x0000ABCD', 6223)",
  "local after = CasinobabeDB.fishing.totals.castObserved",
  "return string.format('%d|%d|%s', before, after, tostring(F.state))",
].join("\n"));
const [before, after, state] = String(cast).split("|");
check("castObserved incrementé (avant=" + before + ")",
  Number(after) === Number(before) + 1, "apres=" + after);
check("etat EN_COURS atteint", state === "PÊCHE EN COURS", state);

const other = await lua.doString([
  "local F = Casinobabe.Fishing",
  "local before = CasinobabeDB.fishing.totals.castObserved",
  "F.OnEvent(nil, 'UNIT_SPELLCAST_SUCCEEDED', 'player', '0x0000EF01', 133)",
  "return string.format('%d|%d', before, CasinobabeDB.fishing.totals.castObserved)",
].join("\n"));
const [b2, a2] = String(other).split("|");
check("un sort non-peche n'incrémente pas castObserved", b2 === a2, b2 + "->" + a2);

const otherUnit = await lua.doString([
  "local F = Casinobabe.Fishing",
  "local before = CasinobabeDB.fishing.totals.castObserved",
  "F.OnEvent(nil, 'UNIT_SPELLCAST_SUCCEEDED', 'target', '0x0000EF02', 6223)",
  "return string.format('%d|%d', before, CasinobabeDB.fishing.totals.castObserved)",
].join("\n"));
const [b3, a3] = String(otherUnit).split("|");
check("un sort peche sur 'target' n'incrémente pas", b3 === a3, b3 + "->" + a3);

console.log(`\n${failures} echec(s)`);
process.exit(failures ? 1 : 0);
