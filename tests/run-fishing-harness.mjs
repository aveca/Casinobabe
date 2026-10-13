// FISHING OFFLINE HARNESS — Lua 5.1 via wasmoon-lua5.1
// NOTE ARCHITECTURE (2026-10-13) : wasmoon/liblua5.1.wasm trappe
// ("memory access out of bounds") quand on multiplie les allers-retours
// JS<->Lua doString sur un même état. Le scénario complet est donc exécuté
// en UN SEUL chunk Lua qui produit une table de résultats FISH_RESULTS,
// relue par Node en une seule passe. Limitation d'infrastructure de test,
// PAS un défaut de la logique de l'addon.
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
  schema: 2,
  vm: "wasmoon-lua5.1",
  mode: "batched-single-chunk",
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

function recordLua(row) {
  return record(row.name, row.ok === true || row.ok === "true", row.detail && row.detail !== "nil" ? row.detail : null);
}

const SCENARIO = `
local DEV = "|Hitem:8365:0:0:0:0:0:0:0|h[Délice déviant]|h"
local FISH = "|Hitem:6291:0:0:0:0:0:0:0|h[Alose éclatante]|h"
local F = Casinobabe.Fishing
local R = {}
local function ck(name, ok, detail)
  R[#R+1] = { name = name, ok = ok and true or false, detail = detail and tostring(detail) or nil }
  return ok
end
local function st() return F.state end

-- 1. Namespace + état initial
ck("fishing_namespace", type(F) == "table", "type=" .. type(F))
ck("fishing_state_inactive", st() == "INACTIF", st())
local gst = F.GetStatus()
ck("status_state_never_nil", type(gst.state) == "string", type(gst.state))
ck("fishing_slash", type(SlashCmdList.CASINOBABEFISH) == "function", type(SlashCmdList.CASINOBABEFISH))

-- 2. Activation
SlashCmdList.CASINOBABEFISH('on')
ck("slash_on", st() == "PRÊT AU LANCER", st())

-- 3. Lancer observé
WoWMock.fireEvent('UNIT_SPELLCAST_SUCCEEDED','player','Pêche')
ck("cast_observed", st() == "PÊCHE EN COURS", st())
ck("cast_counted", CasinobabeDB.fishing.totals.castObserved == 1, tostring(CasinobabeDB.fishing.totals.castObserved))

-- 4. LOOT_OPENED → signal fiable
WoWMock.fireEvent('LOOT_OPENED')
ck("loot_opened", st() == "PRISE À RÉCUPÉRER", st())

-- 5. Butin cible FR confirmé
WoWMock.fireEvent('CHAT_MSG_LOOT','Vous attrapez : '..DEV)
ck("target_caught", st() == "PRISE ENREGISTRÉE", st())
ck("catches_counted", CasinobabeDB.fishing.totals.catches == 1, tostring(CasinobabeDB.fishing.totals.catches))
ck("targets_counted", CasinobabeDB.fishing.totals.targets == 1, tostring(CasinobabeDB.fishing.totals.targets))

-- 6. Déduplication
WoWMock.fireEvent('CHAT_MSG_LOOT','Vous attrapez : '..DEV)
ck("dup_ignored", CasinobabeDB.fishing.totals.dups == 1, tostring(CasinobabeDB.fishing.totals.dups))
ck("no_double_count", CasinobabeDB.fishing.totals.catches == 1, tostring(CasinobabeDB.fishing.totals.catches))

-- 7. Capture non-cible
WoWMock.fireEvent('CHAT_MSG_LOOT','Vous attrapez : '..FISH)
ck("non_target_caught", CasinobabeDB.fishing.totals.catches == 2, tostring(CasinobabeDB.fishing.totals.catches))
ck("targets_unchanged", CasinobabeDB.fishing.totals.targets == 1, tostring(CasinobabeDB.fishing.totals.targets))
ck("itemid_resolved", CasinobabeDB.fishing.history[#CasinobabeDB.fishing.history].itemId == 6291,
   tostring(CasinobabeDB.fishing.history[#CasinobabeDB.fishing.history].itemId))

-- 8. Langue inconnue
WoWMock.fireEvent('CHAT_MSG_LOOT','Pescado: |Hitem:1234:0|h[Xyz]|h')
ck("unknown_kind", CasinobabeDB.fishing.totals.unknownKind == 1, tostring(CasinobabeDB.fishing.totals.unknownKind))

-- 8b. SÉPARATION STRICTE des compteurs (anti-confusion poisson/butin/plat) :
-- un plat cuisiné "Vous créez : [Délice déviant]" n'est NI une prise NI une cible
WoWMock.fireEvent('CHAT_MSG_LOOT','Vous créez : '..DEV)
ck("create_not_catch", CasinobabeDB.fishing.totals.catches == 2, tostring(CasinobabeDB.fishing.totals.catches))
ck("create_not_target", CasinobabeDB.fishing.totals.targets == 1, tostring(CasinobabeDB.fishing.totals.targets))
ck("create_counted_as_crafted", (CasinobabeDB.fishing.totals.crafted or 0) == 1, tostring(CasinobabeDB.fishing.totals.crafted or 0))
-- un butin en langue inconnue apparié à une cible reste "possible", jamais confirmé
WoWMock.fireEvent('CHAT_MSG_LOOT','Mystere incompris: '..DEV)
ck("unknown_target_possible_only", (CasinobabeDB.fishing.totals.possibleTargets or 0) == 1,
   tostring(CasinobabeDB.fishing.totals.possibleTargets or 0))
ck("unknown_target_not_confirmed", CasinobabeDB.fishing.totals.targets == 1, tostring(CasinobabeDB.fishing.totals.targets))
-- un butin "recevoir" apparié compte comme cible mais PAS comme prise de pêche
WoWMock.fireEvent('CHAT_MSG_LOOT','Vous recevez un butin : '..DEV)
ck("receive_target_counted", CasinobabeDB.fishing.totals.targets == 2, tostring(CasinobabeDB.fishing.totals.targets))
ck("receive_not_a_catch", CasinobabeDB.fishing.totals.catches == 2, tostring(CasinobabeDB.fishing.totals.catches))

-- 9. Échec de lancer
WoWMock.fireEvent('UNIT_SPELLCAST_SUCCEEDED','player','Fishing')
WoWMock.fireEvent('UI_ERROR_MESSAGE',"Le poisson s'est échappé.")
ck("got_away", st() == "PRÊT AU LANCER", st())
ck("lost_cast_counted", CasinobabeDB.fishing.totals.lostCasts == 1, tostring(CasinobabeDB.fishing.totals.lostCasts))

-- 10. Combat
WoWMock.fireEvent('PLAYER_REGEN_DISABLED')
ck("combat_detected", st() == "COMBAT DÉTECTÉ", st())
ck("combat_counted", CasinobabeDB.fishing.totals.combats == 1, tostring(CasinobabeDB.fishing.totals.combats))
WoWMock.fireEvent('PLAYER_REGEN_ENABLED')
ck("combat_ended", st() == "PRÊT AU LANCER", st())

-- 11. Mort + réanimation
WoWMock.fireEvent('PLAYER_DEAD')
ck("death_detected", st() == "PERSONNAGE MORT", st())
ck("death_counted", CasinobabeDB.fishing.totals.deaths == 1, tostring(CasinobabeDB.fishing.totals.deaths))
WoWMock.fireEvent('PLAYER_ALIVE')
ck("revive_recovered", st() == "PRÊT AU LANCER", st())

-- 12. Pause arrête l'analyse proprement
SlashCmdList.CASINOBABEFISH('pause')
ck("pause", st() == "PAUSE / ERREUR", st())
WoWMock.fireEvent('PLAYER_DEAD')
ck("paused_no_analysis", CasinobabeDB.fishing.totals.deaths == 1, tostring(CasinobabeDB.fishing.totals.deaths))
SlashCmdList.CASINOBABEFISH('resume')
ck("resume", st() == "PRÊT AU LANCER", st())

-- 13. Export JSON stocké pour lecture Node
FISH_EXPORT_JSON = F.ExportJSON()

-- 14. Reset : confirmation requise puis OK
local rr = F.Reset('nope')
ck("reset_needs_confirm", rr == "CONFIRM_ASKED" or rr == "CONFIRM_REQUIRED", tostring(rr))
ck("reset_not_applied", CasinobabeDB.fishing.totals.catches == 2, tostring(CasinobabeDB.fishing.totals.catches))
ck("reset_confirm", F.Reset('confirm') == "RESET_OK", "RESET_OK")
ck("reset_cleared", CasinobabeDB.fishing.totals.catches == 0, tostring(CasinobabeDB.fishing.totals.catches))

-- 15. Persistance
ck("persisted_spots", type(CasinobabeDB.fishing.spots) == "table", type(CasinobabeDB.fishing.spots))
ck("persisted_targets", #(CasinobabeDB.fishing.targets.names) >= 3, tostring(#(CasinobabeDB.fishing.targets.names)))

-- 16. Cibles dynamiques
ck("target_add_existing", F.SetTarget('Poisson-dévie', true) == "ALREADY", "ALREADY")
ck("target_add_new", F.SetTarget('Test custom cible', true) == "ADDED", "ADDED")
ck("target_remove", F.SetTarget('Test custom cible', false) == "REMOVED", "REMOVED")

-- 17. Arrêt propre
SlashCmdList.CASINOBABEFISH('off')
ck("stop", st() == "INACTIF", st())

-- 18. Checklist honnête (APIs absentes du mock -> inconnu, pas d'invention)
local cl = F.ChecklistText()
local nUnknown = 0
for _, l in ipairs(cl) do if tostring(l):find("inconnu") then nUnknown = nUnknown + 1 end end
ck("checklist_unknown_marked", #cl == 3 and nUnknown == 3, "lignes=" .. #cl .. " inconnues=" .. nUnknown)

-- 19. Journal de diagnostic
SlashCmdList.CASINOBABEFISH('on')
SlashCmdList.CASINOBABEFISH('pause')
ck("diag_pause_recorded", CasinobabeDB.fishing.diag[#CasinobabeDB.fishing.diag].ev == "PAUSE",
   tostring(CasinobabeDB.fishing.diag[#CasinobabeDB.fishing.diag].ev))
SlashCmdList.CASINOBABEFISH('resume')
ck("diag_resume_recorded", CasinobabeDB.fishing.diag[#CasinobabeDB.fishing.diag].ev == "RESUME",
   tostring(CasinobabeDB.fishing.diag[#CasinobabeDB.fishing.diag].ev))

-- 20. Sélecteur de spots
local s1 = F.SelectNextSpot()
local s2 = F.SelectNextSpot()
ck("spot_selector_cycles", type(s1) == "string" and type(s2) == "string" and s1 ~= s2, tostring(s1) .. " -> " .. tostring(s2))
ck("spot_selected_tracked", F.selectedSpot ~= nil, tostring(F.selectedSpot))

-- 21. Recommandation eau libre étiquetée (hors Tarides -> nil, correct)
local ow = F.OpenWaterAdvice()
ck("openwater_labeled_recommendation", ow == nil or tostring(ow):find("recommandation") ~= nil, tostring(ow))

-- 22. Export CSV (en-tête)
local csv = F.ExportCSV()
ck("export_csv_header", (csv[1] or "") == "ts,event,kind,item,target,zone,confidence", tostring(csv[1]))

FISH_RESULTS = R
return "SCENARIO_OK count=" .. #R
`;

const RESULTS_READER = `
local out = {}
for _, r in ipairs(FISH_RESULTS) do
  out[#out + 1] = r.name .. "\\t" .. tostring(r.ok) .. "\\t" .. tostring(r.detail or "")
end
return table.concat(out, "\\n")
`;

async function main() {
  try {
    const lua = await Lua.create();
    await lua.doString(fs.readFileSync(MOCK, "utf8"));
    await lua.doString(fs.readFileSync(RUNTIME, "utf8"));
    record("load_fishing_runtime", true);

    const scenarioStatus = await lua.doString(SCENARIO);
    record("scenario_executed", String(scenarioStatus).startsWith("SCENARIO_OK"), String(scenarioStatus));

    const rows = (await lua.doString(RESULTS_READER)).split("\n").filter(l => l.length > 0);
    for (const line of rows) {
      const [name, ok, detail] = line.split("\t");
      recordLua({ name, ok, detail });
    }

    // Export JSON : valide côté Node
    const json = await lua.doString("return FISH_EXPORT_JSON");
    let parsed = null;
    try { parsed = JSON.parse(json); } catch { /* parse failed */ }
    record("export_json_valid", !!parsed && typeof parsed === "object");
    record("export_json_totals", !!parsed && parsed.totals && parsed.totals.catches === 2 && parsed.totals.targets === 2
      && parsed.totals.crafted === 1 && parsed.totals.possibleTargets === 1,
      parsed ? "catches=" + parsed.totals.catches + " targets=" + parsed.totals.targets
        + " crafted=" + parsed.totals.crafted + " possible=" + parsed.totals.possibleTargets : "parse failed");
    record("export_json_history", !!parsed && Array.isArray(parsed.history) && parsed.history.length >= 4,
      parsed ? "history=" + (parsed.history || []).length : "parse failed");

    // Aucune simulation d'entrée : aucune API d'action dans le CODE (commentaires exclus)
    const src = fs.readFileSync(RUNTIME, "utf8").replace(/^\s*--.*$/gm, "");
    const forbidden = ["CastSpell", "CastSpellByName", "UseAction", "UseInventoryItem",
      "MoveForwardStart", "TurnLeftStart", "JumpOrAscendStart", "SitOrStand",
      "InteractUnit", "ClickSendMail", "CameraOrSelectOrMoveStart", "TargetUnit"];
    const foundForbidden = forbidden.filter(fn => new RegExp("\\b" + fn + "\\b").test(src));
    record("no_input_simulation", foundForbidden.length === 0, foundForbidden.join(",") || "none found");

    // Aucune erreur runtime du mock
    const mockErrors = await lua.doString("return #(WoWMock.errors or {})");
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
  console.log("MODE=" + report.mode);
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
