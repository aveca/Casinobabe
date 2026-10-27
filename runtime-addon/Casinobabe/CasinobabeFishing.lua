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
                   unknownKind = 0, combats = 0, deaths = 0, castObserved = 0,
                   crafted = 0, possibleTargets = 0,
                   -- DIAGNOSTIC (2026-10-20) : pourquoi castObserved reste a 0
                   -- malgre 73 prises comptees ? On note ce que l'evenement
                   -- UNIT_SPELLCAST_SUCCEEDED apporte REELLEMENT (un seul
                   -- canal disponible : les SavedVariables, flush a la
                   -- deconnexion du client). Lecture : CasinobabeDB.fishing.
                   castEvents = 0, castNameResolved = 0,
                   castLastUnit = "", castLastArgs = "", castLastName = "",
                   castLastState = "" }
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
F.state = F.state or F.STATES.INACTIF -- FIX 2026-10-20 : publie l'etat
-- canne/appat des le depart, pour que le bot desktop sache s'il doit
-- tenter l'appat.
if F.PublishBaitState then pcall(F.PublishBaitState) end -- 

local function setState(s)
  F.state = s
  if F.UI then pcall(function() F.UI.Refresh() end) end
end

-- ---------------------------------------------------------------------------
-- Journal de diagnostic (aucune erreur avalée silencieusement)
-- ---------------------------------------------------------------------------
local function nowISO()
  return date("!%Y-%m-%dT%H:%M:%SZ")
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
  -- Transformation/craft (ex. cuisiner un Délice déviant) : JAMAIS compté
  -- comme une prise ni comme un butin de pêche (règle anti-confusion).
  if m:find("creez") or m:find("cree") or m:find("create") or m:find("fabriq") then return "create" end
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
    table.insert(s.targetTs, time())
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

  -- Compteurs STRICTEMENT séparés (jamais confondus) :
  --   catch   = prise de pêche confirmée (CHAT_MSG_LOOT "Vous attrapez")
  --   receive = butin ramassé (mob/coffre) — compté comme cible SI apparié,
  --             mais jamais comme une prise de pêche
  --   create  = objet fabriqué/transformé (ex. plat cuisiné) — jamais cible
  --   unknown = langue non reconnue — possible cible, en attente de confirmation
  if kind == "catch" then
    t.totals.catches = t.totals.catches + 1
    if isT then t.totals.targets = t.totals.targets + qty end
  elseif kind == "receive" then
    if isT then t.totals.targets = t.totals.targets + qty end
  elseif kind == "create" then
    t.totals.crafted = (t.totals.crafted or 0) + qty
    isT = false -- un plat transformé n'est jamais compté comme cible
  elseif kind == "unknown" then
    t.totals.unknownKind = t.totals.unknownKind + 1
    if isT then
      isT = false
      t.totals.possibleTargets = (t.totals.possibleTargets or 0) + qty
    end
  end

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
  local t = GetTime and GetTime() or time()
  -- Contexte de pêche (correctif 2026-10-13, preuve capture 22:52:10) : sur ce
  -- client TBC, TOUT le butin de pêche arrive comme "You receive loot: [x]".
  -- Si un canal de pêche est EN COURS (lancer observé, voire fenêtre de butin
  -- ouverte pour CE lancer et aucun butin encore enregistré), ce "receive"
  -- est une prise de pêche CONFIRMÉE par le contexte, pas un butin de monstre.
  -- Une fois la prise enregistrée (CAPTURED), les "receive" suivants redeviennent
  -- ambigus → classification par le texte uniquement. La cuisine ("create")
  -- reste toujours exclue.
  if kind ~= "create" and (F.state == F.STATES.EN_COURS
      or F.state == F.STATES.A_RECUPERER) then
    kind = "catch"
  end
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

-- Resout le NOM d'un sort a partir de son ID (les evenements
-- UNIT_SPELLCAST_* de WoW Classic ne transmettent que le castGUID et
-- l'ID du sort, jamais le nom). Retourne nil si l'API est absente ou
-- l'ID inconnu : l'appelant decidera alors (aucune erreur inventee).
local function spellNameFromCast(spellID, castGUID)
  -- Certains clients transmettent le nom en 2e argument : on l'accepte
  -- s'il ressemble a un nom de sort (pas un GUID, pas un nombre).
  if type(castGUID) == "string" and castGUID ~= "" then
    if not castGUID:match("^0[xX]") and not castGUID:match("^%-?%d+$") then
      return castGUID
    end
  end
  if type(spellID) == "number" and spellID > 0 then
    local gsi = G("GetSpellInfo")
    if gsi then
      local ok, name = pcall(gsi, spellID)
      if ok and type(name) == "string" and name ~= "" then return name end
    end
    local cs = Gv("C_Spell")
    if type(cs) == "table" and type(cs.GetSpellInfo) == "function" then
      local ok, info = pcall(cs.GetSpellInfo, spellID)
      if ok and type(info) == "table" and type(info.name) == "string"
         and info.name ~= "" then
        return info.name
      end
    end
  end
  return nil
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
  -- MORT (extension 2026-10-13) : aucun butin compté fantôme ; mais depuis
  -- MORT, PLAYER_ALIVE/PLAYER_DEAD passent toujours (reprise + anti-doublon).
  -- Depuis PAUSE/INACTIF, l'analyse reste totalement suspendue.
  local suspended = (F.state == F.STATES.INACTIF or F.state == F.STATES.PAUSE
      or F.state == F.STATES.MORT)
  local vital = (event == "PLAYER_ALIVE" or event == "PLAYER_DEAD")
  if suspended and not (F.state == F.STATES.MORT and vital) then
    -- en PAUSE/INACTIF/MORT on n'analyse plus (l'arrêt est propre) ;
    -- seule l'heure de mort est notée si elle survient hors analyse.
    if event == "PLAYER_DEAD" then F._deathWhileInactive = nowISO() end
    return
  end

  if event == "UNIT_SPELLCAST_SUCCEEDED" then
    -- FIX 2026-10-20 : en WoW Classic (2.5.x), UNIT_SPELLCAST_SUCCEEDED
    -- transmet (unitTarget, castGUID, spellID) -- le NOM DU SORT n'est
    -- JAMAIS transmis. L'ancien code lisait le 2e argument comme un nom de
    -- sort : c'etait en realite le castGUID, donc isFishingSpell() ne
    -- pouvait jamais etre vrai et castObserved restait a 0 (constate dans
    -- les SavedVariables). Consequence : l'etat EN_COURS n'etait jamais
    -- atteint, donc un butin "You receive loot: [X]" (anglais, client
    -- TBC) n'etait JAMAIS reclassifie en "catch" -> les prises de
    -- peche n'etaient comptees que par le mot-cle texte, jamais par le
    -- contexte de peche.
    -- On resout donc le nom via GetSpellInfo(spellID).
    local unit, castGUID, spellID = ...
    local spellName = spellNameFromCast(spellID, castGUID)
    -- DIAGNOSTIC (2026-10-20) : trace brute de l'evenement. castObserved est
    -- a 0 alors que 73 prises ont ete comptees : on doit savoir ce que le
    -- client envoie reellement (ordre des arguments, identifiant du sort) et
    -- dans quel etat etait le module. Ecrit dans les SavedVariables (flush a
    -- la deconnexion) — seul canal autorise (io.open est bloque par le client).
    local T = DB().totals
    T.castEvents = (T.castEvents or 0) + 1
    T.castLastUnit = tostring(unit)
    T.castLastArgs = "guid=" .. tostring(castGUID) .. " id=" .. tostring(spellID)
    T.castLastName = tostring(spellName)
    T.castLastState = tostring(F.state)
    if spellName then T.castNameResolved = (T.castNameResolved or 0) + 1 end
    if unit == "player" and isFishingSpell(spellName) then
      F.castStart = GetTime()
      DB().totals.castObserved = DB().totals.castObserved + 1
      setState(F.STATES.EN_COURS)
    end

  elseif event == "UNIT_SPELLCAST_FAILED" then
    local unit, castGUID, spellID = ...
    local spellName = spellNameFromCast(spellID, castGUID)
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
    if F.state == F.STATES.MORT then return end -- doublon : pas de double comptage
    DB().totals.deaths = DB().totals.deaths + 1
    F.lastDeath = nowISO()
    -- Contexte mort (extension 2026-10-13) : dernière position CONNUE (jamais
    -- inventée), spot, historique conservé tel quel. Le retour au corps et la
    -- résurrection restent 100 % manuels.
    local dz, dx, dy = zoneInfo()
    local fdb = DB()
    fdb.lastDeath = { ts = F.lastDeath, zone = dz, x = dx, y = dy,
                      spot = F.selectedSpot,
                      note = "coords corps inconnues (API indisponible) — dernière position connue ci-dessus" }
    F.RecordSpotRisk(dz, "death", nil)
    pushHistory({ ev = "DEATH", zone = dz, conf = "confirmé (PLAYER_DEAD)" })
    Log("MORT — pêche suspendue. Dernier état conservé (" .. tostring(dz) ..
      "). Récupérez votre corps manuellement ; la reprise sera vérifiée.")
    setState(F.STATES.MORT)

  elseif event == "PLAYER_ALIVE" then
    if F.state == F.STATES.MORT then
      pushHistory({ ev = "REVIVE", zone = zoneInfo(), conf = "confirmé (PLAYER_ALIVE)" })
      local fdb = DB()
      local dz = (fdb.lastDeath and fdb.lastDeath.zone) or nil
      local now, _, _ = zoneInfo()
      if dz and now ~= dz then
        Log("Résurrection vérifiée en " .. tostring(now) .. " (mort en " ..
          tostring(dz) .. ") — vérifiez le spot avant de relancer.")
      else
        Log("Résurrection vérifiée — état cohérent, vous pouvez relancer (START).")
      end
      setState(F.STATES.PRET)
    end

  elseif event == "COMBAT_LOG_EVENT_UNFILTERED" then
    -- Agression (extension 2026-10-13) : dégâts subis par le joueur, lus via
    -- l'API officielle du journal de combat. Attaquant connu ou "inconnu".
    -- NOTE : pcall direct (Safe ne remonte que 3 valeurs, il en faut 9+).
    local cl = G("CombatLogGetCurrentEventInfo")
    local un = G("UnitName")
    if cl and un then
      local okc, _, subev, _, srcGUID, srcName, _, _, destGUID, destName = pcall(cl)
      if okc and destName then
        local okp, pn = pcall(un, "player")
        if okp and pn and destName == pn and subev and tostring(subev):find("DAMAGE") then
          local z = zoneInfo()
          F.RecordSpotRisk(z, "aggro", (srcName and tostring(srcName)) or nil)
          pushHistory({ ev = "AGGRO", item = (srcName and tostring(srcName)) or "inconnu",
                        zone = z, conf = "confirmé (COMBAT_LOG)" })
        end
      end
    end

  elseif event == "BAG_UPDATE" or event == "PLAYER_LOGIN" then
    if F.ObserveBaitInventory then
      pcall(F.ObserveBaitInventory, event)
    end
    if F.UI then pcall(function() F.UI.Refresh() end) end

  elseif event == "UNIT_INVENTORY_CHANGED" then
    local unit = ...
    if unit == "player" and F.CheckEnchantTransition then
      pcall(F.CheckEnchantTransition)
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
  -- Session persistée (mémoire inter-redémarrages : début ; fin à l'arrêt)
  fdb.sessions = fdb.sessions or {}
  fdb.sessionSeq = (fdb.sessionSeq or 0) + 1
  local gam = G("GetAddOnMetadata")
  fdb.sessions[#fdb.sessions + 1] = {
    id = nowISO() .. "-" .. fdb.sessionSeq, startTs = nowISO(),
    endTs = nil, spot = F.selectedSpot,
    configVersion = (gam and Safe(gam, "Casinobabe", "Version")) or "?",
  }
  while #fdb.sessions > 100 do table.remove(fdb.sessions, 1) end
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
  -- Clôture de la session persistée (durée + compteurs, jamais inventés)
  if type(fdb.sessions) == "table" then
    for i = #fdb.sessions, 1, -1 do
      local s = fdb.sessions[i]
      if type(s) == "table" and not s.endTs then
        s.endTs = nowISO()
        s.durationSec = math.floor(((GetTime() or 0) - (F.sessionStart or GetTime() or 0)) - (F.pausedAccum or 0))
        if s.durationSec < 0 then s.durationSec = 0 end
        s.counters = {
          catches = fdb.totals.catches, targets = fdb.totals.targets,
          lostCasts = fdb.totals.lostCasts, dups = fdb.totals.dups,
          unknownKind = fdb.totals.unknownKind, combats = fdb.totals.combats,
          deaths = fdb.totals.deaths, crafted = fdb.totals.crafted or 0,
          baitApplied = fdb.totals.baitApplied or 0,
        }
        break
      end
    end
  end
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
                   unknownKind = 0, combats = 0, deaths = 0, castObserved = 0,
                   crafted = 0, possibleTargets = 0 }
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
    ',"crafted":' .. (fdb.totals.crafted or 0) ..
    ',"possibleTargets":' .. (fdb.totals.possibleTargets or 0) ..
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
    local risk = ""
    if s.risk and ((s.risk.deaths or 0) + (s.risk.aggro or 0) > 0) then
      risk = string.format(" | risque OBSERVÉ: %d morts, %d aggro",
        s.risk.deaths or 0, s.risk.aggro or 0)
    end
    Log(string.format("SPOT: %s%s | prises: %d | cibles: %d | dernière obs: %s%s%s",
      s.zone, pos, s.catches, s.targets, s.lastTs or "?", interval, risk))
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
  -- 1) Compétence Pêche : valeur réelle quand l'API Professions la donne
  local skillTxt = "inconnu (API indisponible)"
  local sk, mx = F.FishingSkill()
  if sk then
    skillTxt = "connue (Pêche " .. tostring(sk) .. (mx and ("/" .. tostring(mx)) or "") .. ")"
  else
    local gsi = G("GetSpellInfo")
    if gsi then
      local known = gsi("Pêche") or gsi("Fishing")
      skillTxt = known and ("connue (" .. tostring(known) .. ")") or "NON connue"
    end
  end
  table.insert(lines, "Compétence: " .. skillTxt)
  -- 2) Canne équipée (main droite, slot 16 : la canne est une arme 2M.
  -- Correctif 2026-10-13 : l'ancien code lisait le slot 18 (baguette) et
  -- affichait à tort "canne NON équipée" pendant une vraie pêche.)
  local rodTxt = "inconnu (API indisponible)"
  local ok, label = F.RodEquipped()
  if ok ~= nil then rodTxt = label end
  table.insert(lines, "Canne: " .. rodTxt)
  -- 3) Appât : statut READY / BAIT REQUIRED / OUT OF STOCK / UNKNOWN
  local bst, bdetail = F.BaitStatus()
  table.insert(lines, "Appât [" .. tostring(bst) .. "]: " .. tostring(bdetail))
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
-- Appâts : inventaire (IDs vérifiés TBC), statut, bouton sécurisé, vérification
-- RÈGLE : "commande envoyée" != "appât actif". Seule la transition
-- false→true de GetWeaponEnchantInfo() prouve l'application.
-- Le bouton APPLIQUER APPÂT utilise un SecureActionButtonTemplate (clic
-- matériel du joueur) : il amorce l'objet, le joueur clique ensuite sa canne.
-- Aucune entrée simulée, aucun auto-équipement, aucun bind modifié.
-- ---------------------------------------------------------------------------
local MAINHAND_SLOT = 16 -- la canne est une arme 2M en main droite (pas le slot 18)
local LURES = {
  { id = 6529, names = { "shiny bauble", "amorce brillante" }, bonus = 25 },
  { id = 6530, names = { "nightcrawlers", "vers" }, bonus = 50 },
  { id = 6532, names = { "bright baubles", "appats brillants" }, bonus = 75 },
  { id = 6533, names = { "aquadynamic fish attractor", "attracteur" }, bonus = 100 },
  { id = 7307, names = { "flesh eating worm", "ver mangeur de chair" }, bonus = 75 },
}
local POLE_SUBTYPES = { "fishing poles", "cannes a peche", "angelruten" }

F.LureInventory = function()
  local gic = G("GetItemCount")
  if not gic then return nil end -- API indisponible → inconnu, jamais inventé
  local best = nil
  for _, lure in ipairs(LURES) do
    local n = Safe(gic, lure.id) or Safe(gic, lure.names[1]) or 0
    n = tonumber(n) or 0
    if n > 0 and (not best or n > best.count) then
      best = { id = lure.id, name = lure.names[1], count = n, bonus = lure.bonus }
    end
  end
  return best -- nil = aucun détecté (ou API muette : compté comme 0 partout)
end

F.RodEquipped = function()
  local gil = G("GetInventoryItemLink")
  if not gil then return nil, "inconnu (API indisponible)" end
  local link = Safe(gil, "player", MAINHAND_SLOT)
  if not link then return false, "NON équipée (main droite vide)" end
  local name = tostring(link):match("\124h%[(.-)%]\124h") or ""
  local nm = norm(name)
  -- sous-type d'objet quand l'API le donne (localisé ou non).
  -- NOTE : Safe() ne remonte que 3 valeurs → pcall direct pour le 7e champ.
  local gii = G("GetItemInfo")
  if gii then
    local ok7, _, _, _, _, _, _, subtype = pcall(gii, link)
    if ok7 and subtype then
      local sm = norm(tostring(subtype))
      for _, ps in ipairs(POLE_SUBTYPES) do
        if sm == ps then return true, "équipée (" .. name .. ")" end
      end
    end
  end
  for _, p in ipairs(ROD_PATTERNS) do
    if nm:find(p, 1, true) then return true, "équipée (" .. name .. ")" end
  end
  return false, "autre arme: " .. name .. " (canne NON équipée)"
end

F.WeaponEnchantActive = function()
  local gwei = G("GetWeaponEnchantInfo")
  if not gwei then return nil end
  local has = Safe(gwei)
  if has == nil then return nil end
  return has and true or false -- main droite = la canne
end

F.FishingSkill = function()
  local gp, gpi = G("GetProfessions"), G("GetProfessionInfo")
  if not (gp and gpi) then return nil end
  local idx = Safe(gp)
  if type(idx) ~= "table" then return nil end
  for _, i in ipairs(idx) do
    -- pcall direct (Safe ne remonte que 3 valeurs ; le max est le 4e champ)
    local okp, name, _, skill, maxSkill = pcall(gpi, i)
    if okp and name then
      local nn = norm(tostring(name))
      if nn:find("peche", 1, true) or nn:find("fishing", 1, true) then
        return tonumber(skill), tonumber(maxSkill)
      end
    end
  end
  return nil
end

F.BaitStatus = function()
  -- READY : enchant actif vérifié. BAIT REQUIRED : canne + appât en sacs sans
  -- enchant. OUT OF STOCK : canne, pas d'enchant, aucun appât. UNKNOWN : sinon.
  local enchant = F.WeaponEnchantActive()
  if enchant == true then
    local lure = F.LureInventory()
    return "READY", "appât actif (enchant vérifié)" ..
      (lure and (" ; "..lure.name.." x"..lure.count.." en sacs") or "")
  end
  local rod = F.RodEquipped()
  if rod == false or rod == nil then
    return "UNKNOWN", "canne non vérifiée (équipez la canne d'abord)"
  end
  local lure = F.LureInventory()
  if lure then
    return "BAIT REQUIRED", "aucun enchant ; "..lure.name.." x"..lure.count..
      " — cliquez APPLIQUER APPÂT puis votre canne"
  end
  return "OUT OF STOCK", "aucun enchant et aucun appât détecté en sacs"
end

-- FIX 2026-10-20 : publication de l'etat « canne / appat ».
-- Sans cela, le bot ne pouvait que deviner : il envoyait une touche de
-- barre qui ne pose AUCUN appat (le slot S ne contient pas l'appat) et
-- declarait un succes ou un echec invente. L'addon, lui, a l'API reelle
-- (GetInventoryItemLink du slot 16, GetItemCount, GetWeaponEnchantInfo) :
-- c'est la seule source de verite.
--
-- CANAL : SavedVariables uniquement (CasinobabeDB.fishing.bait).
-- NOTE 2026-10-20 : un canal direct par fichier a ete teste et RETIRE :
-- io.open est bloque dans ce client (les 3 chemins testes n'ont jamais ete
-- ecrits). Les SavedVariables ne sont ecrites qu'a la deconnexion / au
-- /reload : l'etat est donc en retard d'une session. Le bot desktop le
-- sait et le signale plutot que d'inventer un etat.
F.PublishBaitState = function()
  local fdb = DB()
  fdb.bait = fdb.bait or {}
  local rodOk, rodLabel = F.RodEquipped()
  local lure = F.LureInventory()
  local enchant = F.WeaponEnchantActive()
  local status, detail = F.BaitStatus()
  fdb.bait.rodEquipped = rodOk
  fdb.bait.rodLabel = rodLabel
  fdb.bait.lureId = lure and lure.id or nil
  fdb.bait.lureName = lure and lure.name or nil
  fdb.bait.lureCount = lure and lure.count or 0
  fdb.bait.enchant = enchant
  fdb.bait.status = status
  fdb.bait.detail = detail
  fdb.bait.publishedAt = nowISO()
  -- Note : pas d'ecriture fichier (io.open bloque dans ce client, verifie
  -- le 2026-10-20). Le canal disponible est SavedVariables uniquement.
  return fdb.bait
end

F.ObserveBaitInventory = function(reason)
  local fdb = DB()
  fdb.bait = fdb.bait or {}
  fdb.bait.inventoryObs = fdb.bait.inventoryObs or {}
  local lure = F.LureInventory()
  -- publication pour le bot desktop (voir FIX 2026-10-20)
  if F.PublishBaitState then pcall(F.PublishBaitState) end
  table.insert(fdb.bait.inventoryObs, {
    ts = nowISO(), reason = tostring(reason or "?"),
    lureId = lure and lure.id or nil,
    lureName = lure and lure.name or nil,
    count = lure and lure.count or 0,
    enchant = F.WeaponEnchantActive(),
  })
  while #fdb.bait.inventoryObs > 100 do table.remove(fdb.bait.inventoryObs, 1) end
end

F.CheckEnchantTransition = function()
  -- Appelée sur UNIT_INVENTORY_CHANGED : seule une transition false→true,
  -- lue via l'API, enregistre une application CONFIRMÉE (jamais le clic seul).
  local now = F.WeaponEnchantActive()
  local fdb = DB()
  fdb.bait = fdb.bait or {}
  local was = fdb.bait.enchantKnown
  fdb.bait.enchantKnown = now
  if was == false and now == true then
    fdb.bait.events = fdb.bait.events or {}
    fdb.bait.seq = (fdb.bait.seq or 0) + 1
    table.insert(fdb.bait.events, {
      id = nowISO() .. "-" .. fdb.bait.seq, ts = nowISO(),
      kind = "APPLIED_CONFIRMED", source = "GetWeaponEnchantInfo false→true",
    })
    fdb.totals.baitApplied = (fdb.totals.baitApplied or 0) + 1
    Log("Appât appliqué CONFIRMÉ (enchant actif vérifié).")
    pushHistory({ ev = "BAIT", kind = "applied", item = "?",
                  zone = zoneInfo(), conf = "confirmé (GetWeaponEnchantInfo)" })
    if F.UI then pcall(function() F.UI.Refresh() end) end
  end
end

-- ---------------------------------------------------------------------------
-- Risque par spot : morts, agressions, ennemis (compteurs, jamais de jugement
-- "dangereux" sur un événement isolé — voir RecommendNext)
-- ---------------------------------------------------------------------------
F.RecordSpotRisk = function(zone, kind, enemy)
  local fdb = DB()
  local s = fdb.spots[zone]
  if not s then
    s = { zone = zone, catches = 0, targets = 0, firstTs = nowISO(),
          lastTs = nowISO(), targetTs = {}, lastX = nil, lastY = nil }
    fdb.spots[zone] = s
  end
  s.risk = s.risk or { deaths = 0, aggro = 0, enemies = {}, lastCheck = nil }
  if kind == "death" then s.risk.deaths = s.risk.deaths + 1 end
  if kind == "aggro" then
    s.risk.aggro = s.risk.aggro + 1
    local e = (enemy and enemy ~= "") and enemy or "inconnu"
    s.risk.enemies[e] = (s.risk.enemies[e] or 0) + 1
  end
  s.risk.lastCheck = nowISO()
  s.lastTs = nowISO()
end

-- ---------------------------------------------------------------------------
-- Bancs : événements append-only (jamais réécrits, jamais déduits)
-- ---------------------------------------------------------------------------
F.RecordPoolEvent = function(kind)
  if kind ~= "seen" and kind ~= "gone" then return nil end
  local fdb = DB()
  fdb.poolEvents = fdb.poolEvents or {}
  fdb.poolSeq = (fdb.poolSeq or 0) + 1
  local zone, x, y = zoneInfo()
  local sub = Safe(G("GetSubZoneText")) or ""
  local ev = {
    id = nowISO() .. "-" .. fdb.poolSeq, ts = nowISO(), kind = kind,
    spot = F.selectedSpot or zone, subZone = sub, x = x, y = y,
    source = "joueur (bouton BANC VU/PARTI, visuel)",
    confidence = "déclaré visuellement par le joueur",
  }
  table.insert(fdb.poolEvents, ev)
  while #fdb.poolEvents > 500 do table.remove(fdb.poolEvents, 1) end
  pushHistory({ ev = "POOL", kind = kind, item = ev.spot,
                zone = zone, conf = "déclaré (bouton)" })
  Log(kind == "seen" and "BANC VU enregistré." or "Banc parti enregistré.")
  if F.UI then pcall(function() F.UI.Refresh() end) end
  return ev.id
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
  fb:SetSize(300, 330)
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

  -- Bancs : le joueur déclare ce qu'il VOIT (bouton matériel, jamais auto)
  local poolSeen = makeButton(fb, 138, 20, "BANC VU", function() F.RecordPoolEvent("seen") end)
  poolSeen:SetPoint("TOPLEFT", 8, -172)
  local poolGone = makeButton(fb, 138, 20, "BANC PARTI", function() F.RecordPoolEvent("gone") end)
  poolGone:SetPoint("TOPLEFT", 154, -172)

  -- Appât : bouton SÉCURISÉ (SecureActionButtonTemplate). Le clic matériel du
  -- joueur amorce le meilleur appât détecté ; le joueur clique ensuite sa
  -- canne (le ciblage d'enchant exige ce 2e clic, jamais automatisé).
  -- L'application n'est confirmée que par GetWeaponEnchantInfo().
  local baitBtn = CreateFrame("Button", "Casinobabe_BaitButton", fb,
    "SecureActionButtonTemplate")
  if baitBtn.SetSize then baitBtn:SetSize(284, 20) end
  if baitBtn.SetPoint then baitBtn:SetPoint("TOP", 0, -196) end
  if baitBtn.SetText then baitBtn:SetText("APPLIQUER APPÂT") end
  if baitBtn.SetScript then
    baitBtn:SetScript("PreClick", function(self)
      local icl = G("InCombatLockdown")
      if icl and icl() then
        Log("Appât : impossible en combat (bouton sécurisé verrouillé).")
        return
      end
      local lure = F.LureInventory and F.LureInventory()
      if not lure then
        Log("Appât : aucun appât détecté en sacs (OUT OF STOCK).")
        return
      end
      if self.SetAttribute then
        self:SetAttribute("type", "item")
        self:SetAttribute("item", lure.name)
      end
    end)
  end
  F.baitButton = baitBtn

  local info = fb:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  info:SetPoint("TOP", 0, -220)
  info:SetText("BITE NOT AUTOMATICALLY DETECTABLE — WATCH THE BOBBER / observez le flotteur, cliquez-le.")

  local checklist = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  checklist:SetPoint("TOP", 0, -236)
  checklist:SetText("")

  local spotLine = fb:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  spotLine:SetPoint("TOP", 0, -284)
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
  -- The fishing bar is OFF the casino panel by default: it was anchored to the
  -- casino frame's TOPRIGHT and covered it. It stays hidden unless the player
  -- asks for it (`/cbfish ui`), which flips uiHidden to false and persists it.
  -- The module, its counters and all /cbfish commands keep working either way.
  if fdb.uiHidden ~= false then
    fdb.uiHidden = true
    fb:Hide()
  else
    fb:Show()
  end
  F.UI.Toggle = function()
    local d = DB()
    d.uiHidden = not (d.uiHidden == true)
    if d.uiHidden then fb:Hide() else fb:Show() end
    Log(d.uiHidden and "Fishing panel hidden (state and counters still live)."
                    or "Fishing panel shown.")
    return d.uiHidden
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
    "PLAYER_DEAD", "PLAYER_ALIVE", "COMBAT_LOG_EVENT_UNFILTERED",
    "BAG_UPDATE", "UNIT_INVENTORY_CHANGED", "PLAYER_LOGIN",
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
  elseif cmd == "pool" then
    local sub = tostring(arg or ""):lower()
    if sub == "seen" then F.RecordPoolEvent("seen")
    elseif sub == "gone" then F.RecordPoolEvent("gone")
    else Log("Usage : /cbfish pool seen|gone") end
  elseif cmd == "bait" then
    local st, detail = F.BaitStatus()
    Log("Appât [" .. tostring(st) .. "]: " .. tostring(detail))
  elseif cmd == "targets" then
    local names = DB().targets.names or {}
    Log("Cibles suivies (" .. #names .. ") :")
    for _, n in ipairs(names) do Log(" - " .. tostring(n)) end
  elseif cmd == "target" then
    local sub, name = (arg or ""):match("^(%S*)%s*(.-)$")
    if sub == "add" then F.SetTarget(name, true)
    elseif sub == "remove" then F.SetTarget(name, false)
    else Log("Usage : /cbfish target add <nom> | target remove <nom>") end
  elseif cmd == "ui" then
    -- Hidden by default (it used to overlap the casino panel). This is the
    -- only way to bring it back; the choice is persisted.
    if F.UI and F.UI.Toggle then F.UI.Toggle()
    else Log("Fishing UI not built yet.") end
  elseif cmd == "test" then
    -- INJECTION DE TEST UNIQUEMENT (hors production) : simule un message de butin
    F.RecordLootMessage(arg or "")
  else
    Log("Commandes : /cbfish on|off|pause|resume|status|reset confirm|export [csv]|spots|targets|target add/remove <nom>|pool seen|gone|bait|ui")
  end
  if F.UI then pcall(function() F.UI.Refresh() end) end
end

-- ---------------------------------------------------------------------------
-- Init : UI + événements, ancrage sur le panneau Casinobabe si présent
-- ---------------------------------------------------------------------------
  local ok, err = pcall(function()
    buildUI()
    installEvents()
    -- Deliberately NOT re-anchored to the casino panel any more: anchoring the
    -- fishing frame to the panel's TOPRIGHT made it overlap the casino UI.
    -- The fishing bar keeps its own position (drag to move, saved in barPos).
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
