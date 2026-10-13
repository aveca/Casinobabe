// FISHING OFFLINE HARNESS — Lua 5.1 via wasmoon-lua5.1
// Valide les transitions d'état, le comptage de butin, la déduplication,
// la persistance et l'export du MODE PÊCHE — ASSISTANT.
// Résultats hors ligne uniquement : ne prouve PAS le fonctionnement client réel.
"use strict";

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Lua } from "wasmoon-lua5.1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const RUNTIME = path.join(ROOT, "runtime-addon", "Casinobabe", "CasinobabeFishing.lua");
const MOCK = path.join(ROOT, "tests", "wow_mock.lua");
const REPORT = path.join(ROOT, "reports", "fishing-harness-report.json");
const EXIT_REPORT = path.join(ROOT, "reports", "fishing-harness-exitcode.json");

const report = {
  schema: 1,
  vm: "wasmoon-lua5.1",
  source: path.relative(ROOT, RUNTIME),
  checks: [],
  runtimeErrors: [],
  infrastructureError: null,
  exitCode: 2
};

function record(name, ok, detail) {
  report.checks.push({ name, status: ok ? "PASS" : "FAIL", detail: detail || null });
  if (!ok) report.runtimeErrors.push(name + (detail ? ": " + detail : ""));
  return ok;
}

// Lien d'objet simulé (format 2.5.x) : |Hitem:<id>:...|h[Nom]|h
const DEV = "|Hitem:8365:0:0:0:0:0:0:0|h[Délice déviant]|h";   // cible par défaut (nom FR)
const FISH = "|Hitem:6291:0:0:0:0:0:0:0|h[Alose éclatante]|h"; // non-cible

async function main() {
  try {
    const lua = await Lua.create();
    await lua.doString(fs.readFileSync(MOCK, "utf8"));
    await lua.doString(fs.readFileSync(RUNTIME, "utf8"));
    record("load_fishing_runtime", true);

  async function luaEval(expr) {
    return await lua.doString(expr);
  }
  async function check(name, expr, expected) {
    try {
      const v = await luaEval(expr);
      return record(name, expected(v), "value=" + String(v));
    } catch (e) {
      return record(name, false, String(e));
    }
  }

  // 1. Namespace + état initial
  await check("fishing_namespace", "return type(Casinobabe.Fishing)", v => v === "table");
  await check("fishing_state_inactive", "return Casinobabe.Fishing.state", v => v === "INACTIF");
  await check("status_state_never_nil", "return type(Casinobabe.Fishing.GetStatus().state)", v => v === "string");
  await check("fishing_slash", "return type(SlashCmdList.CASINOBABEFISH)", v => v === "function");

  // 2. Activation via slash
  await check("slash_on", "SlashCmdList.CASINOBABEFISH('on') return Casinobabe.Fishing.state", v => v === "PRÊT AU LANCER");

  // 3. Lancer observé (UNIT_SPELLCAST_SUCCEEDED)
  await check("cast_observed", "WoWMock.fireEvent('UNIT_SPELLCAST_SUCCEEDED','player','Pêche') return Casinobabe.Fishing.state", v => v === "PÊCHE EN COURS");
  await check("cast_counted", "return CasinobabeDB.fishing.totals.castObserved", v => v === 1);

  // 4. LOOT_OPENED → signal fiable "à récupérer"
  await check("loot_opened", "WoWMock.fireEvent('LOOT_OPENED') return Casinobabe.Fishing.state", v => v === "PRISE À RÉCUPÉRER");

  // 5. Butin cible (FR) confirmé via CHAT_MSG_LOOT
  await check("target_caught", `WoWMock.fireEvent('CHAT_MSG_LOOT','Vous attrapez : ${DEV}') return Casinobabe.Fishing.state`, v => v === "PRISE ENREGISTRÉE");
  await check("catches_counted", "return CasinobabeDB.fishing.totals.catches", v => v === 1);
  await check("targets_counted", "return CasinobabeDB.fishing.totals.targets", v => v === 1);

  // 6. Déduplication : le même message dans la fenêtre ne compte pas deux fois
  await check("dup_ignored", `WoWMock.fireEvent('CHAT_MSG_LOOT','Vous attrapez : ${DEV}') return CasinobabeDB.fishing.totals.dups`, v => v === 1);
  await check("no_double_count", "return CasinobabeDB.fishing.totals.catches", v => v === 1);

  // 7. Capture non-cible : comptée mais pas dans les cibles
  await check("non_target_caught", `WoWMock.fireEvent('CHAT_MSG_LOOT','Vous attrapez : ${FISH}') return CasinobabeDB.fishing.totals.catches`, v => v === 2);
  await check("targets_unchanged", "return CasinobabeDB.fishing.totals.targets", v => v === 1);
  await check("itemid_resolved", "return CasinobabeDB.fishing.history[#CasinobabeDB.fishing.history].itemId", v => v === 6291);

  // 8. Langue inconnue : enregistrée honnêtement, pas inventée
  await check("unknown_kind", "WoWMock.fireEvent('CHAT_MSG_LOOT','Pescado: |Hitem:1234:0|h[Xyz]|h') return CasinobabeDB.fishing.totals.unknownKind", v => v === 1);

  // 9. Échec de lancer ("le poisson s'est échappé") → retour PRÊT, pas de fausse réussite
  await check("got_away", "WoWMock.fireEvent('UNIT_SPELLCAST_SUCCEEDED','player','Fishing') WoWMock.fireEvent('UI_ERROR_MESSAGE','Le poisson s\\'est échappé.') return Casinobabe.Fishing.state", v => v === "PRÊT AU LANCER");
  await check("lost_cast_counted", "return CasinobabeDB.fishing.totals.lostCasts", v => v === 1);

  // 10. Combat détecté / fin de combat
  await check("combat_detected", "WoWMock.fireEvent('PLAYER_REGEN_DISABLED') return Casinobabe.Fishing.state", v => v === "COMBAT DÉTECTÉ");
  await check("combat_counted", "return CasinobabeDB.fishing.totals.combats", v => v === 1);
  await check("combat_ended", "WoWMock.fireEvent('PLAYER_REGEN_ENABLED') return Casinobabe.Fishing.state", v => v === "PRÊT AU LANCER");

  // 11. Mort détectée
  await check("death_detected", "WoWMock.fireEvent('PLAYER_DEAD') return Casinobabe.Fishing.state", v => v === "PERSONNAGE MORT");
  await check("death_counted", "return CasinobabeDB.fishing.totals.deaths", v => v === 1);
  await check("revive_recovered", "WoWMock.fireEvent('PLAYER_ALIVE') return Casinobabe.Fishing.state", v => v === "PRÊT AU LANCER");

  // 12. Pause arrête l'analyse proprement
  await check("pause", "SlashCmdList.CASINOBABEFISH('pause') return Casinobabe.Fishing.state", v => v === "PAUSE / ERREUR");
  await check("paused_no_analysis", "WoWMock.fireEvent('PLAYER_DEAD') return CasinobabeDB.fishing.totals.deaths", v => v === 1);
  await check("resume", "SlashCmdList.CASINOBABEFISH('resume') return Casinobabe.Fishing.state", v => v === "PRÊT AU LANCER");

  // 13. Export JSON lisible et complet
  const json = await luaEval("return Casinobabe.Fishing.ExportJSON()");
  let parsed = null;
  try { parsed = JSON.parse(json); } catch { /* keep null */ }
  record("export_json_valid", !!parsed && typeof parsed === "object");
  record("export_json_totals", !!parsed && parsed.totals && parsed.totals.catches === 2 && parsed.totals.targets === 1,
    parsed ? "catches=" + parsed.totals.catches : "parse failed");
  record("export_json_history", !!parsed && Array.isArray(parsed.history) && parsed.history.length >= 4);

  // 14. Export CSV
  const csv = await luaEval("return table.concat(Casinobabe.Fishing.ExportCSV(),'\\n')");
  record("export_csv_header", csv.startsWith("ts,event,kind,item,target,zone,confidence"));

  // 15. Reset sans confirmation refusé, avec confirmation OK
  // (le mock possède StaticPopup → CONFIRM_ASKED ; sans popup → CONFIRM_REQUIRED ;
  //  dans les deux cas les compteurs ne sont PAS remis à zéro)
  const resetResult = await luaEval("return Casinobabe.Fishing.Reset('nope')");
  record("reset_needs_confirm", resetResult === "CONFIRM_ASKED" || resetResult === "CONFIRM_REQUIRED", "result=" + resetResult);
  await check("reset_not_applied", "return CasinobabeDB.fishing.totals.catches", v => v === 2);
  await check("reset_confirm", "return Casinobabe.Fishing.Reset('confirm')", v => v === "RESET_OK");
  await check("reset_cleared", "return CasinobabeDB.fishing.totals.catches", v => v === 0);

  // 16. Persistance : structure complète présente
  await check("persisted_spots", "return type(CasinobabeDB.fishing.spots)", v => v === "table");
  await check("persisted_targets", "return #(CasinobabeDB.fishing.targets.names)", v => v >= 3);

  // 17. Ajout/retrait de cible dynamique
  // ('Poisson-dévie' fait déjà partie des variantes par défaut → ALREADY est correct)
  await check("target_add_existing", "return Casinobabe.Fishing.SetTarget('Poisson-dévie', true)", v => v === "ALREADY");
  await check("target_add_new", "return Casinobabe.Fishing.SetTarget('Test custom cible', true)", v => v === "ADDED");
  await check("target_remove", "return Casinobabe.Fishing.SetTarget('Test custom cible', false)", v => v === "REMOVED");

  // 18. Arrêt propre
  await check("stop", "SlashCmdList.CASINOBABEFISH('off') return Casinobabe.Fishing.state", v => v === "INACTIF");

  // 19. Aucune simulation d'entrée : aucune API d'action dans le CODE (commentaires exclus)
  const src = fs.readFileSync(RUNTIME, "utf8").replace(/^\s*--.*$/gm, "");
  const forbidden = ["CastSpell", "CastSpellByName", "UseAction", "UseInventoryItem",
    "MoveForwardStart", "TurnLeftStart", "JumpOrAscendStart", "SitOrStand",
    "InteractUnit", "ClickSendMail", "CameraOrSelectOrMoveStart", "TargetUnit"];
  const foundForbidden = forbidden.filter(fn => new RegExp("\\b" + fn + "\\b").test(src));
  record("no_input_simulation", foundForbidden.length === 0, foundForbidden.join(",") || "none found");

  // 20. Aucune erreur runtime du mock
  const mockErrors = await luaEval("return #(WoWMock.errors or {})");
  record("mock_runtime_errors", Number(mockErrors) === 0, "count=" + String(mockErrors));

  report.exitCode = report.runtimeErrors.length === 0 ? 0 : 1;
  } catch (error) {
    report.infrastructureError = String(error && error.stack || error);
    report.exitCode = 2;
  }
}

function finalize() {
  fs.mkdirSync(path.dirname(REPORT), { recursive: true });
  fs.writeFileSync(REPORT, JSON.stringify(report, null, 2), "utf8");
  fs.writeFileSync(EXIT_REPORT, JSON.stringify({
    exitCode: report.exitCode,
    runtimeErrors: report.runtimeErrors,
    infrastructureError: report.infrastructureError
  }, null, 2), "utf8");

  console.log("VM=" + report.vm);
  console.log("CHECKS=" + report.checks.filter(x => x.status === "PASS").length + "/" + report.checks.length);
  console.log("ERRORS=" + report.runtimeErrors.length);
  console.log("INFRA_ERROR=" + (report.infrastructureError ? "YES" : "NO"));
  console.log("EXIT_CODE=" + report.exitCode);
  process.exit(report.exitCode);
}

main().then(finalize).catch(error => {
  report.infrastructureError = String(error && error.stack || error);
  report.exitCode = 2;
  finalize();
});
