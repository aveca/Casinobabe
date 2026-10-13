-- ============================================================================
-- CasinobabeFishing.lua — MODE PÊCHE — ASSISTANT (observation uniquement)
-- ============================================================================
-- Client cible : WoW Classic TBC Anniversary (Interface 20506), Lua 5.1.
--
-- RÈGLES STRICTES :
--   * Aucune simulation d'entrée clavier/souris. Aucune action protégée.
--   * Aucun CastSpell, UseAction, MoveForward, Click : le lancer et la
--     récupération restent sous le contrôle du joueur.
--   * Aucun état de réussite inventé : une prise n'est enregistrée QUE via
--     CHAT_MSG_LOOT ; LOOT_OPENED seul donne l'état "PRISE À RÉCUPÉRER".
--   * L'API des addons (2.5.x) ne fournit AUCUN événement de touche (bite).
--     Le module affiche donc une consigne d'observation manuelle du flotteur.
--   * Toute API optionnelle est accédée via rawget(_G, name) et échoue
--     proprement si elle manque.
--
-- Slash : /cbfish (ou /cbf) — on|off|pause|resume|status|reset|export|targets|spots
-- ============================================================================

local CB = Casinobabe or {}
Casinobabe = CB
local F = CB.Fishing or {}
CB.Fishing = F

-- ---------------------------------------------------------------------------
-- Helpers API (toute API optionnelle via rawget → pas d'accès strict interdit)
-- ---------------------------------------------------------------------------
local function G(name) -- fonction globale optionnelle
  local v = rawget(_G, name)
  return type(v) == "function" and v or nil
end

local function Gv(name) -- valeur globale optionnelle
  return rawget(_G, name)
end

local function Safe(fn, ...)
  if type(fn) ~= "function" then return nil end
  local ok, a, b, c = pcall(fn, ...)
  if ok then return a, b, c end
  return nil
end

local function Log(msg)
  local chat = Gv("DEFAULT_CHAT_FRAME")
  local prefix = "|cffFFD700Casinobabe|r [PÊCHE] "
  if chat and type(chat.AddMessage) == "function" then
    pcall(chat.AddMessage, chat, prefix .. tostring(msg))
  else
    pcall(print, prefix .. tostring(msg))
  end
end

-- ---------------------------------------------------------------------------
-- Persistance (CasinobabeDB.fishing)
-- ---------------------------------------------------------------------------
local function DB()
  local db = rawget(_G, "CasinobabeDB")
  if type(db) ~= "table" then
    db = {}
    CasinobabeDB = db
  end
  local fdb = db.fishing
  if type(fdb) ~= "table" then
    fdb = {}
    db.fishing = fdb
  end
  if type(fdb.totals) ~= "table" then
    fdb.totals = { catches = 0, targets = 0, lostCasts = 0, dups = 0,
                   unknownKind = 0, combats = 0, deaths = 0, castObserved = 0 }
  end
  if type(fdb.spots) ~= "table" then fdb.spots = {} end
  if type(fdb.history) ~= "table" then fdb.history = {} end
  if type(fdb.targets) ~= "table" then fdb.targets = {} end
  if type(fdb.targets.names) ~= "table" then
    -- ATTENTION : liste de départ non vérifiée en jeu. Variantes FR/EN
    -- plausibles des objets "Délices déviants". Ajustable via /cbfish target.
    fdb.targets.names = { "delice deviant", "delices deviants", "poisson-devie",
                          "deviate delight", "deviate fish" }
  end
  if type(fdb.targets.ids) ~= "table" then fdb.targets.ids = {} end
  if type(fdb.spotsDocumented) ~= "table" then
    -- Spots documentés publiquement (oasis des Tarides, données classiques
    -- publiques). Étiquetés : non vérifiés localement tant qu'aucun butin
    -- confirmé ne leur est associé.
    fdb.spotsDocumented = { ["Lushwater Oasis"] = true, ["Stagnant Oasis"] = true,
                            ["Forgotten Pools"] = true }
  end
  return fdb
end

-- ---------------------------------------------------------------------------
-- Normalisation de texte (casse + accents FR) pour le matching des cibles
-- ---------------------------------------------------------------------------
local ACCENTS = {
  ["à"]="a",["â"]="a",["ä"]="a",["á"]="a",["ã"]="a",
  ["é"]="e",["è"]="e",["ê"]="e",["ë"]="e",
  ["î"]="i",["ï"]="i",["í"]="i",
  ["ô"]="o",["ö"]="o",["ó"]="o",
  ["ù"]="u",["û"]="u",["ü"]="u",
  ["ç"]="c",["ñ"]="n",
}
local function norm(s)
  s = tostring(s or ""):lower()
  for acc, rep in pairs(ACCENTS) do s = s:gsub(acc, rep) end -- 1 gsub par caractère UTF-8
  s = s:gsub("[^%w]+", " ")
  s = s:gsub("^%s+", ""):gsub("%s+$", "")
  return s
end

local function targetSet()
  local set = {}
  local fdb = DB()
  for _, n in ipairs(fdb.targets.names or {}) do set[norm(n)] = true end
  return set
end

local function isTarget(name, itemId)
  local fdb = DB()
  if itemId and (fdb.targets.ids or {})[itemId] then return true end
  return targetSet()[norm(name)] or false
end

-- ---------------------------------------------------------------------------
-- États
-- ---------------------------------------------------------------------------
F.STATES = {
  INACTIF    = "INACTIF",
  PRET       = "PRÊT AU LANCER",
  EN_COURS   = "PÊCHE EN COURS",
  A_RECUPERER= "PRISE À RÉCUPÉRER",
  CAPTURED   = "PRISE ENREGISTRÉE",
  PAUSE      = "PAUSE / ERREUR",
  COMBAT     = "COMBAT DÉTECTÉ",
  MORT       = "PERSONNAGE MORT",
}

-- État initial défini AVANT toute construction UI : jamais nil, même si
-- l'init de la barre échoue partiellement côté client réel.
F.state = F.state or F.STATES.INACTIF

local function setState(s)
  F.state = s
  if F.UI then pcall(function() F.UI.Refresh() end) end
end

-- ---------------------------------------------------------------------------
-- Journal de diagnostic (aucune erreur avalée silencieusement)
-- ---------------------------------------------------------------------------
local function nowISO()
  return os.date("!%Y-%m-%dT%H:%M:%SZ")
end

local function pushDiag(event, note)
  local fdb = DB()
  fdb.diag = fdb.diag or {}
  table.insert(fdb.diag, { ts = nowISO(), ev = tostring(event or "?"),
                           from = tostring(F.state or F.STATES.INACTIF),
                           note = tostring(note or "") })
  while #fdb.diag > 20 do table.remove(fdb.diag, 1) end
end
F.pushDiag = pushDiag

local function zoneInfo()
  local z = Safe(G("GetZoneText")) or "INCONNU"
  local x, y = Safe(G("GetPlayerMapPosition"), "player")
  if x and y and x > 0 and y > 0 then
    return z, string.format("%.3f", x), string.format("%.3f", y)
  end
  return z, nil, nil -- coordonnées indisponibles → on ne les invente pas
end

-- ---------------------------------------------------------------------------
-- Journal d'événements persisté (200 max)
-- ---------------------------------------------------------------------------
local function pushHistory(entry)
  local fdb = DB()
  entry.ts = entry.ts or nowISO()
  table.insert(fdb.history, entry)
  while #fdb.history > 200 do table.remove(fdb.history, 1) end
end

-- ---------------------------------------------------------------------------
-- Déduplication des événements de butin (fenêtre 1.0 s)
-- ---------------------------------------------------------------------------
F.dedup = F.dedup or {}
local DEDUP_WINDOW = 1.0
local function isDup(key, t)
  local last = F.dedup[key]
  if last and (t - last) <= DEDUP_WINDOW then return true end
  F.dedup[key] = t
  -- taille bornée
  local n = 0
  for _ in pairs(F.dedup) do n = n + 1 end
  if n > 128 then
    for k, v in pairs(F.dedup) do
      if (t - v) > DEDUP_WINDOW then F.dedup[k] = nil end
    end
  end
  return false
end

-- ---------------------------------------------------------------------------
-- Extraction de liens d'objets : "|Hitem:<id>:...|h[Nom]|h"
-- ---------------------------------------------------------------------------
local function extractItems(msg)
  local out = {}
  for id, name in tostring(msg):gmatch("\124Hitem:(%d+):.-\124h%[(.-)%]\124h") do
    table.insert(out, { itemId = tonumber(id), name = name })
  end
  if #out == 0 then
    for name in tostring(msg):gmatch("%[(.-)%]") do
      table.insert(out, { itemId = nil, name = name })
    end
  end
  return out
end

local function classifyLoot(msg)
  local m = norm(msg)
  if m:find("attrap") or m:find("catch") then return "catch" end
  if m:find("recev") or m:find("receive loot") or m:find("butin") then return "receive" end
  return "unknown" -- langue inconnue → enregistré honnêtement comme inconnu
end

local function extractQty(msg)
  return tonumber(tostring(msg):match("x(%d+)")) or 1
end

-- ---------------------------------------------------------------------------
-- Comptage de butin
-- ---------------------------------------------------------------------------
local function updateSpot(zone, kind, isT, qty)
  local fdb = DB()
  local s = fdb.spots[zone]
  if not s then
    s = { zone = zone, catches = 0, targets = 0, firstTs = nowISO(),
          lastTs = nowISO(), targetTs = {}, lastX = nil, lastY = nil }
    fdb.spots[zone] = s
  end
  if kind == "catch" then s.catches = s.catches + 1 end
  if isT then
    s.targets = s.targets + qty
    table.insert(s.targetTs, os.time())
    while #s.targetTs > 20 do table.remove(s.targetTs, 1) end
  end
  local _, x, y = zoneInfo()
  s.lastX, s.lastY = x, y
  s.lastTs = nowISO()
end

local function recordLoot(kind, name, itemId, qty)
  local t = DB()
  qty = qty or 1
  local isT = isTarget(name, itemId)

  if kind == "catch" then
    t.totals.catches = t.totals.catches + 1
  elseif kind == "unknown" then
    t.totals.unknownKind = t.totals.unknownKind + 1
  end
  if isT then t.totals.targets = t.totals.targets + qty end

  local zone, x, y = zoneInfo()
  pushHistory({ ev = "LOOT", kind = kind, item = name, itemId = itemId,
                qty = qty, target = isT, zone = zone, x = x, y = y,
                conf = "confirmé (CHAT_MSG_LOOT)" })
  updateSpot(zone, kind, isT, qty)
  F.lastLoot = { ts = nowISO(), kind = kind, item = name, target = isT, itemId = itemId }

  if isT then
    Safe(G("PlaySound"), 51402) -- son "jackpot" du casino (id éprouvé)
    Log("|cff00ff00OBJET CIBLÉ OBTENU : " .. name .. " (x" .. qty .. ")|r")
  elseif kind == "catch" then
    Safe(G("PlaySound"), 43918) -- son "roll end" éprouvé côté casino
  end
  setState(F.STATES.CAPTURED)
  if F.UI then pcall(function() F.UI.Refresh() end) end
end

F.RecordLootMessage = function(msg)
  local kind = classifyLoot(msg)
  local qty = extractQty(msg)
  local items = extractItems(msg)
  local t = GetTime and GetTime() or os.time()
  if #items == 0 then
    -- message de butin sans objet identifiable : enregistré comme inconnu
    if not isDup("NOITEM|" .. kind, t) then
      local fdb = DB()
      fdb.totals.unknownKind = fdb.totals.unknownKind + 1
      pushHistory({ ev = "LOOT", kind = "unknown", item = tostring(msg):sub(1, 80),
                    qty = 0, target = false, zone = zoneInfo(), conf = "partiel (sans objet)" })
    end
    return false
  end
  for _, it in ipairs(items) do
    local key = tostring(it.name) .. "|" .. kind .. "|" .. qty
    if isDup(key, t) then
      DB().totals.dups = DB().totals.dups + 1
    else
      recordLoot(kind, it.name, it.itemId, qty)
    end
  end
  return true
end

-- ---------------------------------------------------------------------------
-- Événements de lancer / touche / incidents
-- ---------------------------------------------------------------------------
local FISH_SPELLS = { peche = true, fishing = true } -- FR / EN (comparaison normalisée)

local function isFishingSpell(name)
  return FISH_SPELLS[norm(name)] or false
end

-- Échecs de lancer / poisson échappé : mots-clés connus (FR/EN). Une erreur
-- inconnue est journalisée sans modifier l'état de réussite.
local FAILURE_KEYWORDS = { "echappe", "got away", "pas abouti", "didn t work",
                           "didn't work", "nothing to hook", "rien a mordre",
                           "ecorche", "s est echappe", "le poisson s est echappe" }

local function isFailureText(msg)
  local m = norm(msg)
  for _, k in ipairs(FAILURE_KEYWORDS) do
    if m:find(norm(k), 1, true) then return true end
  end
  return false
end

F.OnEvent = function(self, event, ...)
  if F.state == F.STATES.INACTIF or F.state == F.STATES.PAUSE then
    -- en PAUSE/INACTIF on n'analyse plus (l'arrêt est propre)
    if event == "PLAYER_DEAD" then F._deathWhileInactive = nowISO() end
    return
  end

  if event == "UNIT_SPELLCAST_SUCCEEDED" then
    local unit, spellName = ...
    if unit == "player" and isFishingSpell(spellName) then
      F.castStart = GetTime()
      DB().totals.castObserved = DB().totals.castObserved + 1
      setState(F.STATES.EN_COURS)
    end

  elseif event == "UNIT_SPELLCAST_FAILED" then
    local unit, spellName = ...
    if unit == "player" and isFishingSpell(spellName) and F.state == F.STATES.EN_COURS then
      DB().totals.lostCasts = DB().totals.lostCasts + 1
      pushHistory({ ev = "CAST_FAILED", item = spellName, zone = zoneInfo(),
                    conf = "confirmé (UNIT_SPELLCAST_FAILED)" })
      setState(F.STATES.PRET)
    end

  elseif event == "LOOT_OPENED" then
    -- signal fiable et autorisé : la fenêtre de butin s'ouvre (joueur a cliqué)
    setState(F.STATES.A_RECUPERER)

  elseif event == "CHAT_MSG_LOOT" then
    F.RecordLootMessage((...))

  elseif event == "UI_ERROR_MESSAGE" then
    local msg = ...
    if F.state == F.STATES.EN_COURS or F.state == F.STATES.A_RECUPERER then
      if isFailureText(msg) then
        DB().totals.lostCasts = DB().totals.lostCasts + 1
        pushDiag("LOST_CAST", tostring(msg))
        pushHistory({ ev = "LOST", item = tostring(msg), zone = zoneInfo(),
                      conf = "confirmé (UI_ERROR_MESSAGE)" })
        setState(F.STATES.PRET)
      else
        pushDiag("ERROR_UNCLASSIFIED", tostring(msg))
        pushHistory({ ev = "ERROR_UNKNOWN", item = tostring(msg), zone = zoneInfo(),
                      conf = "non classé" })
      end
    end

  elseif event == "PLAYER_REGEN_DISABLED" then
    F.prevState = F.state
    DB().totals.combats = DB().totals.combats + 1
    F.lastCombat = nowISO()
    pushHistory({ ev = "COMBAT", zone = zoneInfo(), conf = "confirmé (PLAYER_REGEN_DISABLED)" })
    setState(F.STATES.COMBAT)

  elseif event == "PLAYER_REGEN_ENABLED" then
    if F.state == F.STATES.COMBAT then setState(F.STATES.PRET) end

  elseif event == "PLAYER_DEAD" then
    DB().totals.deaths = DB().totals.deaths + 1
    F.lastDeath = nowISO()
    pushHistory({ ev = "DEATH", zone = zoneInfo(), conf = "confirmé (PLAYER_DEAD)" })
    setState(F.STATES.MORT)

  elseif event == "PLAYER_ALIVE" then
    if F.state == F.STATES.MORT then
      pushHistory({ ev = "REVIVE", zone = zoneInfo(), conf = "confirmé (PLAYER_ALIVE)" })
      setState(F.STATES.PRET)
    end
  end
end

-- ---------------------------------------------------------------------------
-- Contrôles du mode
-- ---------------------------------------------------------------------------
local INSTRUCTIONS = {
  "1) Placez le sort Pêche dans un emplacement cliquable de votre barre.",
  "2) Pêchez vers un bassin visible (cercles d'écume) si disponible : bonus de prise.",
  "3) Lancez manuellement. L'addon observe, il ne lance pas à votre place.",
  "4) L'API des addons ne peut PAS détecter la touche : surveillez le flotteur.",
  "5) Quand le flotteur plonge, cliquez vite sur le flotteur pour récupérer.",
  "6) L'addon enregistre la prise via le message de butin et sonne si c'est une cible.",
}

F.Activate = function()
  setState(F.STATES.PRET)
  F.sessionStart = GetTime()
  F.pausedAccum = 0
  local fdb = DB()
  fdb.enabled = true
  pushDiag("ACTIVATE", "activation manuelle (bouton ou /cbfish on)")
  Log("MODE PÊCHE — ASSISTANT ACTIVÉ. État : " .. F.state)
  for _, l in ipairs(INSTRUCTIONS) do Log(l) end
  if F.UI then pcall(function() F.UI.Refresh() end) end
  return F.state
end

F.Pause = function()
  if F.state == F.STATES.INACTIF then return F.state end
  F.pausedAt = GetTime()
  setState(F.STATES.PAUSE)
  DB().enabled = false
  pushDiag("PAUSE", "pause manuelle (bouton PAUSE ou /cbfish pause)")
  Log("PAUSE — analyse suspendue. Vos compteurs sont conservés.")
  return F.state
end

F.Resume = function()
  if F.state ~= F.STATES.PAUSE then return F.state end
  if F.pausedAt then F.pausedAccum = (F.pausedAccum or 0) + (GetTime() - F.pausedAt) end
  F.pausedAt = nil
  setState(F.STATES.PRET)
  DB().enabled = true
  pushDiag("RESUME", "reprise après pause")
  Log("REPRISE — analyse réactivée. État : " .. F.state)
  return F.state
end

F.Stop = function()
  setState(F.STATES.INACTIF)
  local fdb = DB()
  fdb.enabled = false
  pushDiag("STOP", "arrêt manuel (bouton ARRÊT ou /cbfish off)")
  Log("ARRÊT — mode pêche désactivé. Journal conservé.")
  if F.UI then pcall(function() F.UI.Refresh() end) end
  return F.state
end

F.Toggle = function()
  if F.state == F.STATES.INACTIF or F.state == F.STATES.PAUSE then
    if F.state == F.STATES.PAUSE then return F.Resume() end
    return F.Activate()
  end
  return F.Stop()
end

F.GetStatus = function()
  F.state = F.state or F.STATES.INACTIF -- garde anti-nil (client réel)
  local fdb = DB()
  local dur = 0
  if F.state ~= F.STATES.INACTIF then
    dur = (GetTime() - (F.sessionStart or GetTime())) - (F.pausedAccum or 0)
    if F.state == F.STATES.PAUSE and F.pausedAt then dur = dur - (GetTime() - F.pausedAt) end
  end
  return {
    state = F.state,
    durationSec = dur,
    catches = fdb.totals.catches,
    targets = fdb.totals.targets,
    lostCasts = fdb.totals.lostCasts,
    dups = fdb.totals.dups,
    unknownKind = fdb.totals.unknownKind,
    combats = fdb.totals.combats,
    deaths = fdb.totals.deaths,
    lastLoot = F.lastLoot,
    lastCombat = F.lastCombat,
    lastDeath = F.lastDeath,
    selectedSpot = F.selectedSpot,
    lastDiag = (function()
      local db2 = rawget(_G, "CasinobabeDB")
      local d = type(db2) == "table" and type(db2.fishing) == "table" and db2.fishing.diag or nil
      return d and d[#d] or nil
    end)(),
    cbErrors = (function()
      local db = rawget(_G, "CasinobabeDB")
      local j = type(db) == "table" and db.liveErrorJournal or nil
      if type(j) ~= "string" or j == "" then return 0 end
      local n = 1
      for _ in j:gmatch("\n") do n = n + 1 end
      return n
    end)(),
  }
end

F.Reset = function(confirmed)
  if confirmed ~= "confirm" and confirmed ~= true then
    local dlg = Gv("StaticPopupDialogs")
    local show = G("StaticPopup_Show")
    if dlg and show then
      if not dlg["CASINOBABE_FISH_RESET"] then
        dlg["CASINOBABE_FISH_RESET"] = {
          text = "Remettre les compteurs de pêche à zéro ?",
          button1 = "Oui", button2 = "Non",
          OnAccept = function() F.Reset(true) end,
          timeout = 30, whileDead = 1, hideOnEscape = 1,
        }
      end
      show("CASINOBABE_FISH_RESET")
      return "CONFIRM_ASKED"
    end
    return "CONFIRM_REQUIRED"
  end
  local fresh = DB()
  fresh.totals = { catches = 0, targets = 0, lostCasts = 0, dups = 0,
                   unknownKind = 0, combats = 0, deaths = 0, castObserved = 0 }
  fresh.history = {}
  fresh.spots = {}
  F.dedup = {}
  F.lastLoot = nil
  Log("Compteurs de pêche remis à zéro (session et historique).")
  if F.UI then pcall(function() F.UI.Refresh() end) end
  return "RESET_OK"
end

F.SetTarget = function(name, add)
  local fdb = DB()
  local n = norm(name)
  if n == "" then return "EMPTY" end
  fdb.targets.names = fdb.targets.names or {}
  for i, existing in ipairs(fdb.targets.names) do
    if norm(existing) == n then
      if add then return "ALREADY" end
      table.remove(fdb.targets.names, i)
      Log("Cible retirée : " .. name)
      return "REMOVED"
    end
  end
  if add then
    table.insert(fdb.targets.names, name)
    Log("Cible ajoutée : " .. name .. " (matching par nom, insensible à la casse/accents)")
    return "ADDED"
  end
  return "NOT_FOUND"
end

-- ---------------------------------------------------------------------------
-- Export JSON / CSV (affiché dans le chat, copier-coller — les addons ne
-- peuvent pas écrire de fichiers)
-- ---------------------------------------------------------------------------
local function q(s) return '"' .. tostring(s or ""):gsub('\\', '\\\\'):gsub('"', '\\"') .. '"' end

F.ExportJSON = function()
  local fdb = DB()
  local st = F.GetStatus()
  local parts = {}
  table.insert(parts, '"status":' .. q(st.state))
  table.insert(parts, '"totals":{' ..
    '"catches":' .. fdb.totals.catches ..
    ',"targets":' .. fdb.totals.targets ..
    ',"lostCasts":' .. fdb.totals.lostCasts ..
    ',"dups":' .. fdb.totals.dups ..
    ',"unknownKind":' .. fdb.totals.unknownKind ..
    ',"combats":' .. fdb.totals.combats ..
    ',"deaths":' .. fdb.totals.deaths .. '}')
  local spots = {}
  for _, s in pairs(fdb.spots) do
    table.insert(spots, '{"zone":' .. q(s.zone) ..
      ',"catches":' .. s.catches .. ',"targets":' .. s.targets ..
      ',"lastTs":' .. q(s.lastTs) .. ',"lastX":' .. q(s.lastX or "") .. ',"lastY":' .. q(s.lastY or "") .. '}')
  end
  table.insert(parts, '"spots":[' .. table.concat(spots, ",") .. ']')
  local hist = {}
  for _, h in ipairs(fdb.history) do
    table.insert(hist, '{"ts":' .. q(h.ts) .. ',"ev":' .. q(h.ev) ..
      ',"item":' .. q(h.item) .. ',"kind":' .. q(h.kind) ..
      ',"target":' .. tostring(h.target == true) ..
      ',"zone":' .. q(h.zone) .. ',"conf":' .. q(h.conf) .. '}')
  end
  table.insert(parts, '"history":[' .. table.concat(hist, ",") .. ']')
  local json = "{" .. table.concat(parts, ",") .. "}"
  Log("EXPORT JSON (copier depuis la fenêtre de chat) :")
  Log(json)
  return json
end

F.ExportCSV = function()
  local fdb = DB()
  local lines = { "ts,event,kind,item,target,zone,confidence" }
  for _, h in ipairs(fdb.history) do
    table.insert(lines, table.concat({
      q(h.ts), q(h.ev), q(h.kind), q(h.item),
      tostring(h.target == true), q(h.zone), q(h.conf) }, ","))
  end
  Log("EXPORT CSV :")
  Log(table.concat(lines, "\n"))
  return lines
end

-- ---------------------------------------------------------------------------
-- Spots : registre + recommandation (données observées uniquement)
-- ---------------------------------------------------------------------------
F.ListSpots = function()
  local fdb = DB()
  local zones, count = {}, 0
  for _, s in pairs(fdb.spots) do
    count = count + 1
    local interval = ""
    if #s.targetTs >= 2 then
      local sum = 0
      for i = 2, #s.targetTs do sum = sum + (s.targetTs[i] - s.targetTs[i - 1]) end
      interval = string.format(" | intervalle OBSERVÉ (confiance faible) : %.0f s", sum / (#s.targetTs - 1))
    end
    local pos = s.lastX and string.format(" (%s,%s)", s.lastX, s.lastY) or " (coords indisponibles)"
    Log(string.format("SPOT: %s%s | prises: %d | cibles: %d | dernière obs: %s%s",
      s.zone, pos, s.catches, s.targets, s.lastTs or "?", interval))
  end
  if count == 0 then Log("Aucun spot observé pour l'instant. Données insuffisantes.") end
  return count
end

F.RecommendNext = function()
  local fdb = DB()
  local best, bestTargets = nil, math.huge
  for _, s in pairs(fdb.spots) do
    if (s.targets or 0) < bestTargets then best, bestTargets = s, s.targets or 0 end
  end
  if not best then
    -- fallback : 1er spot du registre documenté non encore observé
    for name in pairs(fdb.spotsDocumented or {}) do
      Log("Recommandation (données publiques, non vérifiées localement) : " .. name)
      return name
    end
    Log("Recommandation indisponible : aucune donnée de spot observée.")
    return nil
  end
  Log("Prochain spot recommandé (le moins productif en cibles) : " ..
    best.zone .. " — à vérifier manuellement.")
  return best.zone
end

-- ---------------------------------------------------------------------------
-- Checklist de préparation (API gardées : connu / inconnu — jamais inventé)
-- ---------------------------------------------------------------------------
local ROD_PATTERNS = { "canne a peche", "fishing rod", "fishing pole", "strong fishing pole",
                       "big iron fishing pole", "blump family fishing pole" }
local BAITS = { "amorce brillante", "shiny bauble", "vers", "nightcrawlers",
                "ver de terre", "appat", "flesh eating worm", "aquadynamic fish attractor" }

F.ChecklistText = function()
  local lines = {}
  -- 1) Compétence Pêche connue ?
  local gsi = G("GetSpellInfo")
  local skill = "inconnu (API indisponible)"
  if gsi then
    local known = gsi("Pêche") or gsi("Fishing")
    skill = known and ("connue (" .. tostring(known) .. ")") or "NON connue"
  end
  table.insert(lines, "Compétence: " .. skill)
  -- 2) Canne équipée (slot à distance #18 en 2.5.x) ?
  local gil = G("GetInventoryItemLink")
  local rod = "inconnu (API indisponible)"
  if gil then
    local link = gil("player", 18)
    if not link then
      rod = "NON équipée (emplacement à distance vide)"
    else
      local name = tostring(link):match("\124h%[(.-)%]\124h") or ""
      local nm = norm(name)
      local ok = false
      for _, p in ipairs(ROD_PATTERNS) do
        if nm:find(p, 1, true) then ok = true break end
      end
      rod = ok and ("équipée (" .. name .. ")") or "autre arme: " .. name .. " (canne NON équipée)"
    end
  end
  table.insert(lines, "Canne: " .. rod)
  -- 3) Appât dans les sacs ?
  local gic = G("GetItemCount")
  local bait = "inconnu (API indisponible)"
  if gic then
    local total, found = 0, nil
    for _, b in ipairs(BAITS) do
      local n = gic(b) or 0
      if n > total then total, found = n, b end
    end
    bait = total > 0 and ("prêt: " .. found .. " x" .. total) or "AUCUN détecté (appliquez-le manuellement)"
  end
  table.insert(lines, "Appât: " .. bait)
  return lines
end

-- Sélecteur de spots : cycle parmi les spots enregistrés + documentés
F.spotIdx = 0
F.SelectNextSpot = function()
  local names = {}
  local fdb = DB()
  for _, s in pairs(fdb.spots) do table.insert(names, s.zone or "?") end
  for name in pairs(fdb.spotsDocumented or {}) do table.insert(names, name) end
  if #names == 0 then
    F.selectedSpot = nil
    Log("Aucun spot enregistré. Données insuffisantes.")
    return nil
  end
  table.sort(names)
  F.spotIdx = (F.spotIdx % #names) + 1
  F.selectedSpot = names[F.spotIdx]
  Log("SPOT sélectionné: " .. F.selectedSpot .. " (déplacement manuel)")
  if F.UI then pcall(function() F.UI.Refresh() end) end
  return F.selectedSpot
end

F.CurrentSpotName = function()
  local z = Safe(G("GetZoneText"))
  return z or nil
end

-- Recommandation eau libre (données publiques documentées — JAMAIS une action)
F.OpenWaterAdvice = function()
  local z = norm(Safe(G("GetZoneText")) or "")
  if z:find("barrens", 1, true) or z:find("tarides", 1, true) then
    return "Eau libre OK pour Poisson-dévie dans les oasis (données publiques) — recommandation, pas une action effectuée."
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- UI — barre autonome, draggable, sans vol de focus
-- ---------------------------------------------------------------------------
local function makeButton(parent, w, h, label, onClick)
  local b = CreateFrame("Button", nil, parent)
  b:SetSize(w, h)
  if type(b.SetHighlightTexture) == "function" then
    pcall(b.SetHighlightTexture, b, "Interface\\Buttons\\UI-Common-MouseHilight")
  end
  local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  fs:SetPoint("CENTER")
  fs:SetText(label)
  b.text = fs
  b:SetScript("OnClick", onClick)
  return b
end

local function buildUI()
  if F.UI then return F.UI end
  local BackdropTemplate = Gv("BackdropTemplateMixin")
  local fb = CreateFrame("Frame", "Casinobabe_FishingBar", UIParent,
    BackdropTemplate and "BackdropTemplate" or nil)
  fb:SetSize(300, 262)
  fb:SetPoint("BOTTOMRIGHT", UIParent, "BOTTOMRIGHT", -12, 180)
  fb:SetFrameStrata("MEDIUM")
  fb:SetMovable(true); fb:EnableMouse(true); fb:RegisterForDrag("LeftButton")
  fb:SetClampedToScreen(true)
  fb:SetScript("OnDragStart", fb.StartMoving)
  fb:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    local p, _, rp, x, y = self:GetPoint()
    local fdb = DB()
    fdb.barPos = { p = p, rp = rp, x = x, y = y }
  end)
  if fb.SetBackdrop then
    fb:SetBackdrop({ bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
      edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border", edgeSize = 12,
      insets = { left = 2, right = 2, top = 2, bottom = 2 } })
    fb:SetBackdropColor(0.03, 0.02, 0.06, 0.92)
  end

  local title = fb:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
  title:SetPoint("TOP", 0, -6)
  title:SetText("CASINOBABE — PÊCHE")

  local status = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  status:SetPoint("TOP", 0, -22)
  status:SetText("État : " .. (F.state or F.STATES.INACTIF))

  local counters = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  counters:SetPoint("TOP", 0, -40)
  counters:SetText("")

  local alert = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  alert:SetPoint("TOP", 0, -74)
  alert:SetText("")

  local toggle = makeButton(fb, 284, 30, "MODE PÊCHE — ASSISTANT", function() F.Toggle() end)
  toggle:SetPoint("TOP", 0, -90)

  local function rowBtn(label, x, fn)
    local b = makeButton(fb, 68, 22, label, fn)
    b:SetPoint("TOPLEFT", x, -124)
    return b
  end
  rowBtn("PAUSE",    8,  function() F.Pause() end)
  rowBtn("ARRÊT",    78, function() F.Stop() end)
  rowBtn("EXPORT",  148, function() F.ExportJSON() end)
  rowBtn("SPOTS",   218, function() F.SelectNextSpot() end)

  local resetBtn = makeButton(fb, 284, 20, "RESET (compteurs)", function()
    local r = F.Reset(nil)
    if r == "CONFIRM_REQUIRED" then Log("Reset : confirmation requise (/cbfish reset confirm).") end
  end)
  resetBtn:SetPoint("TOP", 0, -148)

  local info = fb:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  info:SetPoint("TOP", 0, -172)
  info:SetText("BITE NOT AUTOMATICALLY DETECTABLE — WATCH THE BOBBER / observez le flotteur, cliquez-le.")

  local checklist = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  checklist:SetPoint("TOP", 0, -188)
  checklist:SetText("")

  local spotLine = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  spotLine:SetPoint("TOP", 0, -236)
  spotLine:SetText("")

  F.UI = {
    frame = fb,
    Refresh = function()
      local st = F.GetStatus()
      status:SetText("État : " .. tostring(st.state))
      counters:SetText(string.format(
        "Session: %ds | Prises confirmées: %d | Cibles: %d\nÉchecs: %d | Doublons ignorés: %d | Combats: %d | Morts: %d",
        math.floor(st.durationSec or 0), st.catches or 0, st.targets or 0,
        st.lostCasts or 0, st.dups or 0, st.combats or 0, st.deaths or 0))
      local last = st.lastLoot
      alert:SetText(last and ("Dernier butin : " .. tostring(last.item) ..
        (last.target and " |cffffd700★ CIBLE|r" or "")) or "Aucun butin enregistré.")
      toggle.text:SetText((F.state == F.STATES.INACTIF or F.state == F.STATES.PAUSE)
        and "FISHING ASSISTANT — START" or "FISHING ASSISTANT — STOP")
      -- checklist (connu vs inconnu, jamais inventé)
      local cl = F.ChecklistText and F.ChecklistText() or nil
      if cl then checklist:SetText(table.concat(cl, "\n")) end
      -- ligne spot : sélection manuelle + recommandation eau libre
      local spotTxt = "SPOT: --"
      if F.selectedSpot then spotTxt = "SPOT sélectionné: " .. F.selectedSpot
      else
        local z = F.CurrentSpotName()
        if z then spotTxt = "SPOT actuel (zone): " .. z end
      end
      local ow = F.OpenWaterAdvice and F.OpenWaterAdvice()
      if ow then spotTxt = spotTxt .. "\n" .. ow end
      spotLine:SetText(spotTxt)
    end,
  }

  local fdb = DB()
  if fdb.barPos then
    pcall(fb.SetPoint, fb, fdb.barPos.p, UIParent, fdb.barPos.rp, fdb.barPos.x, fdb.barPos.y)
  end
  F.UI.Refresh()

  -- Rafraîchissement léger (1 s) via ticker — aucune entrée simulée
  local ticker = G("C_Timer") and rawget(_G, "C_Timer").NewTicker
  if type(ticker) == "function" then
    pcall(function()
      C_Timer.NewTicker(1, function()
        if fb.IsShown and fb:IsShown() and F.state ~= F.STATES.INACTIF then
          F.UI.Refresh()
        end
      end)
    end)
  end
  return F.UI
end

-- ---------------------------------------------------------------------------
-- Cadre d'événements
-- ---------------------------------------------------------------------------
local function installEvents()
  if F.eventFrame then return end
  local ef = CreateFrame("Frame", "Casinobabe_FishingEvents", UIParent)
  ef:SetScript("OnEvent", F.OnEvent)
  local events = {
    "LOOT_OPENED", "CHAT_MSG_LOOT",
    "UNIT_SPELLCAST_SUCCEEDED", "UNIT_SPELLCAST_FAILED",
    "UI_ERROR_MESSAGE",
    "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED",
    "PLAYER_DEAD", "PLAYER_ALIVE",
  }
  for _, e in ipairs(events) do
    local ok = pcall(ef.RegisterEvent, ef, e)
    if not ok then Log("Événement indisponible : " .. e .. " (ignoré)") end
  end
  F.eventFrame = ef
end

-- ---------------------------------------------------------------------------
-- Slash : /cbfish | /cbf
-- ---------------------------------------------------------------------------
SLASH_CASINOBABEFISH1 = "/cbfish"
SLASH_CASINOBABEFISH2 = "/cbf"
SlashCmdList["CASINOBABEFISH"] = function(msg)
  local cmd, arg = tostring(msg or ""):match("^(%S*)%s*(.-)$")
  cmd = (cmd or ""):lower()
  if cmd == "" or cmd == "status" then
    local st = F.GetStatus()
    Log(string.format(
      "État: %s | Prises: %d | Cibles: %d | Échecs: %d | Doublons: %d | Butin de type inconnu: %d | Combats: %d | Morts: %d",
      st.state, st.catches, st.targets, st.lostCasts, st.dups, st.unknownKind, st.combats, st.deaths))
  elseif cmd == "on" then F.Activate()
  elseif cmd == "off" then F.Stop()
  elseif cmd == "pause" then F.Pause()
  elseif cmd == "resume" then F.Resume()
  elseif cmd == "toggle" then F.Toggle()
  elseif cmd == "reset" then
    local r = F.Reset(arg)
    if r == "CONFIRM_REQUIRED" then Log("Reset refusé : tapez /cbfish reset confirm") end
  elseif cmd == "export" then
    if (arg or ""):lower() == "csv" then F.ExportCSV() else F.ExportJSON() end
  elseif cmd == "spots" then F.ListSpots(); F.RecommendNext()
  elseif cmd == "targets" then
    local names = DB().targets.names or {}
    Log("Cibles suivies (" .. #names .. ") :")
    for _, n in ipairs(names) do Log(" - " .. tostring(n)) end
  elseif cmd == "target" then
    local sub, name = (arg or ""):match("^(%S*)%s*(.-)$")
    if sub == "add" then F.SetTarget(name, true)
    elseif sub == "remove" then F.SetTarget(name, false)
    else Log("Usage : /cbfish target add <nom> | target remove <nom>") end
  elseif cmd == "test" then
    -- INJECTION DE TEST UNIQUEMENT (hors production) : simule un message de butin
    F.RecordLootMessage(arg or "")
  else
    Log("Commandes : /cbfish on|off|pause|resume|status|reset confirm|export [csv]|spots|targets|target add/remove <nom>")
  end
  if F.UI then pcall(function() F.UI.Refresh() end) end
end

-- ---------------------------------------------------------------------------
-- Init : UI + événements, ancrage sur le panneau Casinobabe si présent
-- ---------------------------------------------------------------------------
  local ok, err = pcall(function()
    buildUI()
    installEvents()
    local panel = Gv("Casinobabe_Panel")
    local fdb = DB()
    if panel and not fdb.barPos then
      pcall(function()
        F.UI.frame:ClearAllPoints()
        F.UI.frame:SetPoint("TOPLEFT", panel, "TOPRIGHT", 8, -8)
      end)
    end
  end)
F.state = F.state or F.STATES.INACTIF -- réaffirmé après init (aucune dépendance à la réussite UI)
if not ok then
  Log("Erreur d'initialisation du mode pêche (l'état et les compteurs restent fonctionnels) : " .. tostring(err))
  if seterrorhandler and geterrorhandler then
    pcall(function()
      local prev = geterrorhandler()
      seterrorhandler(function(m, s)
        -- le bus d'erreurs de Casinobabe.lua (déjà chargé) capturera aussi ceci
        if prev then return prev(m, s) end
      end)
    end)
  end
end
