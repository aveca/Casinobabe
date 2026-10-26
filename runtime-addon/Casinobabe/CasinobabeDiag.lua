--[[
  CasinobabeDiag.lua — DIAGNOSTIC TEMPORAIRE DU RETOUR AU CORPS.

  But : etablir, avec les API NATIVES du client (pas de vision, pas
  d'heuristique pixel), ou se trouve le corps du personnage mort et si
  l'API de recuperation est autorisee.

  Ce que le module mesure et journalise (en etat FANTOME) :

    1. C_Map.GetBestMapForUnit("player")      -> mapID de la zone
    2. C_DeathInfo.GetCorpseMapPosition(mapID) -> position du CORPS sur
       la carte (coordonnees NORMALISEES 0..1 de la carte de zone)
    3. C_Map.GetPlayerMapPosition(mapID, "player") -> position JOUEUR
       sur la meme carte (meme systeme de coordonnees)
    4. UnitPosition("player")                 -> position monde en yards
       (X, Y, Z) + systeme de coordonnees explicite
    5. RetrieveCorpse()                       -> tentative REELLE : la
       fonction est protegee et echoue si le corps est hors de portee.
       L'ERREUR est capturee telle quelle (restriction / securite).

  Canaux de sortie (trois, complementaires) :
    A. SavedVariables  -> CasinobabeDB.corpseDiag (flush au prochain
       rechargement de l'UI ou a la deconnexion)
    B. CHAT            -> print() (visible par l'utilisateur)
    C. PIXELS          -> une bande de carres colores encode le payload
       en ASCII ; le bot la relit par capture d'ecran (lecture
       deterministe, sans OCR).

  Le module est VOLONTAIREMENT temporaire et sans effet de jeu.
--]]

local ADDON, ns = ...

local D = {}
ns.CasinobabeDiag = D

-- ------------------------------------------------------------------
-- sonde d'API : renvoie (valeur, erreur)
-- ------------------------------------------------------------------
local function probe(name, fn, ...)
    if type(fn) ~= "function" then
        return nil, "API ABSENTE: " .. tostring(name)
    end
    local ok, a, b, c = pcall(fn, ...)
    if not ok then
        return nil, "ERREUR: " .. tostring(a)
    end
    return a, nil, b, c
end

local function num(v, digits)
    if type(v) ~= "number" then return "?" end
    return string.format("%." .. (digits or 4) .. "f", v)
end

-- ------------------------------------------------------------------
-- 1. collecte
-- ------------------------------------------------------------------
function D.collect()
    local out = {
        ts = time(),
        ghost = nil,
        alive = nil,
        mapID = nil,
        mapErr = nil,
        playerMap = nil,
        playerMapErr = nil,
        corpseMap = nil,
        corpseMapErr = nil,
        worldX = nil,
        worldY = nil,
        worldZ = nil,
        worldErr = nil,
        retrieve = nil,
        retrieveErr = nil,
        apis = {},
    }

    -- etat du personnage.
    -- ATTENTION : UnitIsPlayer("player") renvoie TRUE pour un fantome
    -- (le fantome est toujours "un joueur" pour le client) : c'est
    -- UnitIsGhost qui fait foi.  On garde les deux mais l'etat retenu est
    -- base sur le fantome.
    if UnitIsGhost then
        local ok, g = pcall(UnitIsGhost, "player")
        out.ghost = ok and g or false
    end
    if UnitIsPlayer then
        local ok, a = pcall(UnitIsPlayer, "player")
        out.isPlayer = ok and a or false
    end
    out.alive = (out.ghost == false) and (out.isPlayer == true)

    -- 1. mapID
    local mapID, mapErr = probe("C_Map.GetBestMapForUnit",
                                C_Map and C_Map.GetBestMapForUnit, "player")
    out.mapID = mapID
    out.mapErr = mapErr

    -- 2. position du joueur sur la carte de zone (coordonnees normalisees)
    local pmap, perr = probe("C_Map.GetPlayerMapPosition",
                              C_Map and C_Map.GetPlayerMapPosition,
                              mapID, "player")
    out.playerMap = pmap
    out.playerMapErr = perr

    -- 3. position du CORPS sur la carte de zone (coordonnees normalisees)
    local cmap, cerr = probe("C_DeathInfo.GetCorpseMapPosition",
                              C_DeathInfo and C_DeathInfo.GetCorpseMapPosition,
                              mapID)
    out.corpseMap = cmap
    out.corpseMapErr = cerr
    -- variante legacy eventuellement presente
    if not cmap then
        local cmap2, cerr2 = probe("GetCorpseMapPosition",
                                   _G["GetCorpseMapPosition"], mapID)
        if cmap2 then
            out.corpseMap = cmap2
            out.corpseMapErr = nil
            out.apis.legacyCorpseMap = "GetCorpseMapPosition"
        end
    end

    -- 4. position monde du joueur (yards, repere monde WoW)
    local px, py, pz = UnitPosition("player")
    if px then
        out.worldX, out.worldY, out.worldZ = px, py, pz
    else
        out.worldErr = "UnitPosition indisponible"
    end

    -- 5. tentative REELLE de recuperation.
    --    Deux voies natives, dans cet ordre :
    --      a) l'API globale RetrieveCorpse() (protegee) : son ERREUR dit
    --         exactement pourquoi l'action est refusee (hors de portee,
    --         protection, spell non connu...) ;
    --      b) le SORT "Retrieve Corpse" (id 5026) : c'est l'action
    --         reellement autorisee par le client.  On l'invoque par son
    --         ID (pas par son nom : le nom localise varie).
    local rok, rerr = probe("RetrieveCorpse", _G["RetrieveCorpse"])
    if rok == nil and rerr then
        out.retrieveErr = rerr
    else
        out.retrieve = tostring(rok)
    end
    -- spell "Retrieve Corpse" : ID 5026 (Tradeskill/ corpse)
    local spellID = 5026
    local known = false
    if C_Spell and C_Spell.GetSpellInfo then
        local sok, sinfo = pcall(C_Spell.GetSpellInfo, spellID)
        known = sok and sinfo ~= nil
    end
    out.retrieveSpellID = spellID
    out.retrieveSpellKnown = known
    if known then
        local cok, cerr2 = probe("CastSpellByName", _G["CastSpellByName"],
                                 "Retrieve Corpse")
        if cok == nil and cerr2 then
            out.castErr = cerr2
        else
            out.castResult = tostring(cok)
        end
    end

    -- 6. etat de la presence du corps
    out.hasCorpseApi = (C_DeathInfo ~= nil)
    out.retrieveCorpseExists = (_G["RetrieveCorpse"] ~= nil)

    -- 7. VECTEUR DE NAVIGATION, en systeme de coordonnees de la carte de
    --    zone (x = ouest->est, y = nord->sud, normalise 0..1) :
    --    c'est la meme reference que celle de la carte du monde, donc
    --    exploitable telle quelle pour viser le corps.
    if out.playerMap and out.corpseMap then
        local dx = out.corpseMap.x - out.playerMap.x
        local dy = out.corpseMap.y - out.playerMap.y
        out.navDX, out.navDY = dx, dy
        out.navDist = math.sqrt(dx * dx + dy * dy)
        -- cap vers le corps, repere NORD (0 = est, +90 = nord)
        out.navBearingDeg = math.degrees(math.atan2(-dy, dx))
        -- position monde du corpse, si l'API la donne
        if C_DeathInfo and C_DeathInfo.GetCorpsePosition then
            local cx, cy, cz = probe("C_DeathInfo.GetCorpsePosition",
                                     C_DeathInfo.GetCorpsePosition)
            if type(cx) == "table" then
                out.corpseWorld = { x = cx.x, y = cx.y, z = cx.z }
            end
        end
    end

    return out
end

-- ------------------------------------------------------------------
-- 2. formatage lisible
-- ------------------------------------------------------------------
function D.format(o)
    local L = {}
    L[#L + 1] = "ghost=" .. tostring(o.ghost) .. " alive=" .. tostring(o.alive)
    L[#L + 1] = "mapID=" .. tostring(o.mapID) .. (o.mapErr and (" [" .. o.mapErr .. "]") or "")
    if o.playerMap then
        L[#L + 1] = "playerMap=" .. num(o.playerMap.x) .. "," .. num(o.playerMap.y)
            .. " (coordonnees normalisees 0..1 de la carte de zone)"
    else
        L[#L + 1] = "playerMap=ERREUR " .. tostring(o.playerMapErr)
    end
    if o.corpseMap then
        L[#L + 1] = "corpseMap=" .. num(o.corpseMap.x) .. "," .. num(o.corpseMap.y)
            .. " (coordonnees normalisees 0..1 de la carte de zone)"
    else
        L[#L + 1] = "corpseMap=ERREUR " .. tostring(o.corpseMapErr)
    end
    if o.worldX then
        L[#L + 1] = "worldPos=" .. num(o.worldX, 2) .. "," .. num(o.worldY, 2)
            .. "," .. num(o.worldZ, 2) .. " (yards, repere monde WoW)"
    else
        L[#L + 1] = "worldPos=ERREUR " .. tostring(o.worldErr)
    end
    L[#L + 1] = "RetrieveCorpse=" .. tostring(o.retrieve) .. " err=" .. tostring(o.retrieveErr)
    L[#L + 1] = "sortRecuperer(id=" .. tostring(o.retrieveSpellID) .. ",connu="
        .. tostring(o.retrieveSpellKnown) .. ",resultat=" .. tostring(o.castResult)
        .. ",err=" .. tostring(o.castErr) .. ")"
    L[#L + 1] = "C_DeathInfo=" .. tostring(o.hasCorpseApi)
        .. " RetrieveCorpse existe=" .. tostring(o.retrieveCorpseExists)
    -- ecart corpse/joueur en coordonnees de carte
    if o.corpseMap and o.playerMap then
        local dx = o.corpseMap.x - o.playerMap.x
        local dy = o.corpseMap.y - o.playerMap.y
        L[#L + 1] = "deltaCarte=" .. num(dx) .. "," .. num(dy)
            .. " dist=" .. num(math.sqrt(dx * dx + dy * dy))
            .. " capVersCorps=" .. num(o.navBearingDeg, 1) .. "deg(repere nord)"
    end
    if o.corpseWorld then
        L[#L + 1] = "corpsMonde=" .. num(o.corpseWorld.x, 2) .. ","
            .. num(o.corpseWorld.y, 2) .. "," .. num(o.corpseWorld.z, 2)
            .. " (yards, repere monde WoW)"
    end
    return table.concat(L, " | ")
end

-- Format COMPACT pour la bande de pixels (canal vivant) : tout ce dont le
-- bot a besoin pour se diriger vers le corps, en quelques dizaines de
-- caracteres, plus les presence/erreurs d'API.
function D.formatCompact(o)
    local L = {}
    -- separateur ";" : les valeurs (message d'erreur) contiennent des
    -- espaces, qui casseraient un separateur espace.
    L[#L + 1] = "g=" .. (o.ghost and "1" or "0")
    L[#L + 1] = "m=" .. tostring(o.mapID)
    if o.playerMap then
        L[#L + 1] = "p=" .. string.format("%.4f,%.4f", o.playerMap.x, o.playerMap.y)
    else
        L[#L + 1] = "p=ERR"
    end
    if o.corpseMap then
        L[#L + 1] = "c=" .. string.format("%.4f,%.4f", o.corpseMap.x, o.corpseMap.y)
    else
        L[#L + 1] = "c=ERR"
    end
    if o.navBearingDeg then
        L[#L + 1] = "b=" .. string.format("%.1f", o.navBearingDeg)
        L[#L + 1] = "d=" .. string.format("%.4f", o.navDist)
    end
    L[#L + 1] = "cd=" .. tostring(o.hasCorpseApi and 1 or 0)
    L[#L + 1] = "rc=" .. tostring(o.retrieveCorpseExists and 1 or 0)
    L[#L + 1] = "sp=" .. tostring(o.retrieveSpellKnown and 1 or 0)
    -- l'erreur de cast est isolee ET tronquee : elle peut contenir des
    -- espaces, qui casseraient le format compact.
    local ce = tostring(o.castErr or "")
    ce = string.gsub(ce, "[%c;]", " ")
    ce = string.sub(ce, 1, 40)
    L[#L + 1] = "ce=" .. ce
    return table.concat(L, ";")
end

-- ------------------------------------------------------------------
-- 3. sortie SavedVariables + chat
-- ------------------------------------------------------------------
function D.publish(o)
    local db = _G["CasinobabeDB"]
    if type(db) == "table" then
        db.corpseDiag = o
    end
    local txt = D.format(o)
    local frame = _G["ChatFrame1"] or _G["DEFAULT_CHAT_FRAME"]
    if frame and frame.AddMessage then
        frame:AddMessage("|cffffd700[DiagCorps]|r " .. txt)
    else
        print("[DiagCorps] " .. txt)
    end
    D.last = txt
    D.drawStrip(txt)
    return txt
end

-- ------------------------------------------------------------------
-- 4. sortie pixels : bande de carres encodes en ASCII
--    (lecture deterministe par capture d'ecran, sans OCR)
--    Le payload est coupe en LIGNES de 60 caracteres, chaque caractere
--    etant un carre de 12 px UI ; une ligne = une rangee de 60 carres.
--    La premiere ligne commence par un MARQUEUR de sync (violet
--    distinctive : 160,0,200) : sa presence prouve que le module a
--    tourne, et sa position donne l'origine de lecture.
-- ------------------------------------------------------------------
local STRIP_SQ = 16
local STRIP_MAX = 60
local SYNC = {160 / 255, 0, 200 / 255}

function D.drawStrip(txt)
    if not _G["UIParent"] then return end
    local f = _G["CasinobabeDiagStrip"]
    if not f then
        f = CreateFrame("Frame", "CasinobabeDiagStrip", UIParent)
        f:SetFrameStrata("HIGH")
        f:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 8, -8)
    end
    f:Show()
    for _, c in ipairs(f.__cells or {}) do c:Hide() end
    f.__cells = {}
    local function cell(i, col, row)
        local c = f.__cells[i]
        if not c then
            c = f:CreateTexture(nil, "ARTWORK")
            c:SetSize(STRIP_SQ, STRIP_SQ)
            c:SetPoint("TOPLEFT", f, "TOPLEFT", (i - 1) * STRIP_SQ,
                       -row * STRIP_SQ)
            f.__cells[i] = c
        end
        c:SetTexture(nil)
        c:SetVertexColor(col[1], col[2], col[3], 1)
        c:Show()
    end
    -- Chaque ligne est un BLOC INDEPENDANT : elle commence par un carre de
    -- synchronisation violet, suivi de STRIP_MAX caracteres.  Les lignes
    -- sont empilees verticalement, toutes a la meme colonne de depart :
    -- le lecteur n'a donc aucune ambiguite de mise en page.
    local function cell_at(col, row, colr)
        local i = row * (STRIP_MAX + 1) + col + 1
        local c = f.__cells[i]
        if not c then
            c = f:CreateTexture(nil, "ARTWORK")
            c:SetSize(STRIP_SQ, STRIP_SQ)
            c:SetPoint("TOPLEFT", f, "TOPLEFT", col * STRIP_SQ, -row * STRIP_SQ)
            -- SetTexture(r,g,b,a) cree une couleur UNIE reellement rendue
            -- (SetTexture(nil) ne dessine rien : c'etait le bug du premier
            -- decodage, la bande restait invisible).
            c:SetTexture(colr[1], colr[2], colr[3], 1)
            f.__cells[i] = c
        end
        c:SetTexture(colr[1], colr[2], colr[3], 1)
        c:SetVertexColor(1, 1, 1, 1)
        c:Show()
    end
    local row, col = 0, 0
    cell_at(col, row, SYNC); col = col + 1
    for i = 1, #txt do
        if col > STRIP_MAX then
            row = row + 1
            col = 0
            cell_at(col, row, SYNC); col = col + 1
        end
        local b = string.byte(txt, i)
        cell_at(col, row, {b / 255, b / 255, b / 255})
        col = col + 1
    end
    f:SetSize((STRIP_MAX + 1) * STRIP_SQ, (row + 1) * STRIP_SQ)
end

-- ------------------------------------------------------------------
-- 5. points d'entree
-- ------------------------------------------------------------------
function D.run()
    local o = D.collect()
    return D.publish(o)
end

-- slash command : /cbd  (diagnostic du corps)
SLASH_CBD1 = "/cbd"
SlashCmdList["CBD"] = function(msg)
    D.run()
end
SLASH_CBD2 = "/casinobabediag"
SlashCmdList["CASINOBABEDIAG"] = function(msg)
    D.run()
end

-- au chargement : si le personnage est fantome, on collecte et on publie
-- (permet la lecture par le bot meme sans passer par le chat, qui est
--  bloque pour un fantome : "You can't chat when you're dead!")
local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("PLAYER_DEAD")
-- ZONE_CHANGED : le fantome qui se deplace change de zone -> on
-- republie la reference native, qui reste ainsi FRAICHE pendant
-- l'approche du corps (le canal WTF est ecrit periodiquement par le
-- client).
pcall(frame.RegisterEvent, frame, "ZONE_CHANGED")
pcall(frame.RegisterEvent, frame, "ZONE_CHANGED_NEW_AREA")
frame:SetScript("OnEvent", function(self, event)
    local isGhost = false
    if UnitIsGhost then
        local ok, g = pcall(UnitIsGhost, "player")
        isGhost = ok and g or false
    end
    if isGhost then
        -- petit delai : le client n'est pas toujours pret
        C_Timer.After(3.0, function() D.run() end)
    end
end)

-- Publication periodique : la BANDE DE PIXELS est le canal vivant (le
-- chat n'est pas lisible de facon fiable et les SavedVariables ne sont
-- ecrits qu'a la deconnexion).  Le bot lit ainsi la position native a
-- chaque instant et peut mesurer sa progression.
local ticker
if C_Timer and C_Timer.NewTicker then
    ticker = C_Timer.NewTicker(2.0, function()
        if not _G["UIParent"] then return end
        local isGhost = false
        if UnitIsGhost then
            local ok, g = pcall(UnitIsGhost, "player")
            isGhost = ok and g or false
        end
        if not isGhost then return end
        local o = D.collect()
        D.lastPayload = o
        D.drawStrip(D.formatCompact(o))
    end)
end
