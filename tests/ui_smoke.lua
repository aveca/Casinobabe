local out = {}
local fails = 0
local function log(s) out[#out+1] = s end

-- Counts both raised Lua errors and errors the mock itself records, so a
-- silently swallowed failure is still counted.
local function run(handler, arg, label)
  local before = #(WoWMock.errors or {})
  local ok, err = pcall(function() SlashCmdList[handler](arg) end)
  local after = #(WoWMock.errors or {})
  if not ok or after > before then
    fails = fails + 1
    log(string.format("  ECHEC  %-34s %s", label, ok and "mock a enregistre une erreur" or tostring(err)))
  else
    log(string.format("  ok     %s", label))
  end
end

local CB = {
  {"", "bare /cb (ouvre le panneau)"},
  {"help", "help"}, {"?", "alias ?"}, {"commands", "alias commands"},
  {"bogus", "commande inconnue"}, {"bogus a b", "inconnue + arguments"},
  {"autoshow", "autoshow etat"}, {"autoshow on", "autoshow on"},
  {"autoshow off", "autoshow off"},
  {"auto", "auto etat"}, {"auto stop", "auto stop"}, {"auto status", "auto status"},
  {"who", "who (auto)"}, {"who auto", "who auto"}, {"who reset", "who reset"},
  {"discord montag", "discord"}, {"fx", "fx"},
  {"resetstats", "resetstats"}, {"reset", "reset position"}, {"intro", "intro"},
  {"minimap", "minimap debug"},
  {"dealer status", "dealer status"}, {"dealer on", "dealer on"},
  {"dealer off", "dealer off"}, {"dealer zone", "dealer zone"},
  {"dealer log", "dealer log"}, {"dealer ad", "dealer ad"},
  {"dealer attract", "dealer attract"},
  {"dealer show", "dealer show"}, {"dealer quickad", "dealer quickad"},
  {"dealer stopshow", "dealer stopshow"}, {"dealer showstatus", "dealer showstatus"},
  {"dealer emote", "emote sans argument"},
  {"dealer emote NOPE", "emote jeton inconnu"},
  {"dealer emote WAVE", "emote jeton valide"},
  {"dealer invite", "dealer invite sans argument"},
  {"dealer game", "dealer game sans argument"},
  {"dealer stake", "dealer stake sans argument"},
  {"dealer roll", "dealer roll sans argument"},
  {"dealer record", "dealer record sans argument"},
  {"dealer resolve", "dealer resolve sans argument"},
  {"dealer payout", "dealer payout sans argument"},
  {"dealer close", "dealer close sans argument"},
  {"dealer reset", "dealer reset sans argument"},
}

log("=== /cb : " .. #CB .. " entrees ===")
-- Captured BEFORE any /cbfish ui runs, so it reflects the state at load.
local fishingBuiltAtLoad = (Casinobabe.Fishing and Casinobabe.Fishing.UI) ~= nil
for _, c in ipairs(CB) do run("CASINOBABE", c[1], c[2]) end

log("")
log("=== /cbs (moteur de show) ===")
for _, c in ipairs({ {"", "aide"}, {"MEGA", "MEGA"}, {"LOOP", "LOOP"},
                     {"STOP", "STOP"}, {"REFRESH", "REFRESH"}, {"INCONNU", "show inconnu"} }) do
  run("CASINOBAE_SHOW_ENGINE", c[1], c[2])
end

log("")
log("=== /cbqa (auto-test dealer) ===")
for _, c in ipairs({ {"status", "status"}, {"", "sans argument"}, {"bogus", "inconnu"} }) do
  run("CASINOBABE_QA", c[1], c[2])
end

log("")
log("=== /cbfish (module peche) ===")
for _, c in ipairs({ {"status", "status"}, {"", "sans argument"},
                     {"ui", "ui (construit le panneau)"}, {"ui", "ui (le masque)"},
                     {"spots", "spots"}, {"targets", "targets"},
                     {"target", "target sans argument"}, {"export", "export"},
                     {"reset", "reset"}, {"bogus", "inconnu"} }) do
  run("CASINOBABEFISH", c[1], c[2])
end

log("")
log("=== /cbd (diagnostic) ===")
for _, c in ipairs({ {"", "sans argument"} }) do
  run("CBD", c[1], c[2])
end

log("")
log("=== contraintes structurelles ===")
-- Aucune commande ne doit faire apparaitre le panneau peche tout seul.
log(string.format("  panneau peche absent au chargement  : %s", tostring(not fishingBuiltAtLoad)))
if fishingBuiltAtLoad then fails = fails + 1 end
-- Le panneau casino, lui, doit se construire sans erreur.
local beforeFrames = #(WoWMock.config.uiFrames or {})
local okPanel, errPanel = pcall(function() SlashCmdList.CASINOBABE("") end)
log(string.format("  construction du panneau casino      : %s", okPanel and "ok" or tostring(errPanel)))
if not okPanel then fails = fails + 1 end
-- Le show doit avoir emis des messages en anglais ASCII.
local asciiBad = 0
for _, m in ipairs(WoWMock.config.chatLog) do
  for i = 1, #m.text do
    if string.byte(m.text, i) > 126 then asciiBad = asciiBad + 1 break end
  end
end
log(string.format("  messages de chat non-ASCII          : %d (doit etre 0)", asciiBad))
if asciiBad > 0 then fails = fails + 1 end

log("")
log(string.format("=== TOTAL : %d echec(s) sur les commandes et contraintes ===", fails))
out[#out+1] = "UI_SMOKE=" .. (fails == 0 and "PASS" or "FAIL")
return table.concat(out, "\n")
