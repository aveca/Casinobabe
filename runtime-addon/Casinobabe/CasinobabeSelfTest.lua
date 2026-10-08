-- ============================================================================
-- CasinobabeSelfTest.lua v1.1.1 — QA autonome in-client
--
-- Garanties :
--   * ZÉRO entrée synthétique OS (aucune injection souris/clavier/login)
--   * Exécution = handler OnClick réel du bouton (chemin du clic humain)
--   * GATE strict par personnage+royaume (noms uniques par région) :
--     ne s'exécute que sur le compte casino (YACOV972 / Thunderstrike).
--     L'instance personnelle charge ce code mais ne l'exécute JAMAIS.
--   * IIFE auto-invoquée : zéro local ajouté à la portée du chunk hôte
--     (Casinobabe.lua frôle la limite 200 locals/chunk de Lua 5.1).
--   * Armement automatique à la 1re installation, cycles bornés, auto-désarme.
--   * Persistance SavedVariables prouvée par cycle ReloadUI automatique.
--   * Ne masque PAS les défauts : diagnostics A-F explicites par run.
-- ============================================================================

(function()
  local ST_VERSION = "1.1.1"
  local ADDON_NAME = "Casinobabe"
  local MAX_RUNS   = 20

  local QA_REALM = "Thunderstrike"
  local QA_CHARS = {
    ["Casinøbabe"]  = true,
    ["Câsínobâbe"] = true,
    ["Infection"]   = true,
  }

  local function IsQATarget()
    if not UnitName then return false end
    local n = UnitName("player")
    return (n ~= nil) and QA_CHARS[n] and (GetRealmName() == QA_REALM)
  end

  local function QADB()
    CasinobabeDB = CasinobabeDB or {}
    CasinobabeDB.qa = CasinobabeDB.qa or {}
    local qa = CasinobabeDB.qa
    if qa.armed     == nil then qa.armed = false end
    if qa.maxCycles == nil then qa.maxCycles = 2 end
    qa.cycle = qa.cycle or 0
    qa.runs  = qa.runs or {}
    return qa
  end

  local function QALog(msg)
    print(("|cffADD8E6[CB-QA]|r %s"):format(tostring(msg)))
  end

  local function WithCapturedPrint(fn)
    local old = rawget(_G, "print")
    local captured = {}
    _G.print = function(...)
      local n = select("#", ...)
      local parts = {}
      for i = 1, n do parts[#parts + 1] = tostring(select(i, ...)) end
      captured[#captured + 1] = table.concat(parts, " ")
      if old then old(...) end
    end
    local ok, err = pcall(fn)
    _G.print = old
    return ok, err, captured
  end

  local function ShallowSnap()
    local s = {}
    if type(CasinobabeDB) == "table" then
      for k, v in pairs(CasinobabeDB) do
        if type(v) == "table" then s[k] = "t#" .. tostring(#v) else s[k] = tostring(v) end
      end
    end
    return s
  end

  local function ErrSeq()
    if type(CasinobabeErrorBus) == "table" then return CasinobabeErrorBus.seq or 0 end
    return 0
  end

  -- Scénario MOVE_TO_SPOT — diagnostics A..F
  local function Scenario_MoveToSpot()
    local res = {
      scenario  = "MOVE_TO_SPOT",
      ts        = os.date("!%Y-%m-%dT%H:%M:%SZ"),
      build     = (GetBuildInfo()),
      addonVer  = (GetAddOnMetadata and GetAddOnMetadata(ADDON_NAME, "Version")) or "?",
      stVersion = ST_VERSION,
      errSeqBefore = ErrSeq(),
    }

    local panel = _G["Casinobabe_Panel"]
    local btn = panel and panel.moveSpotBtn or nil
    res.A_buttonExists = btn ~= nil                                   -- A
    if btn then
      res.visible = btn:IsVisible() and true or false
      res.enabled = btn:IsEnabled() and true or false
      local handler = btn:GetScript("OnClick")
      res.B_handlerExists = handler ~= nil                            -- B
      if handler then
        local before = ShallowSnap()
        local ok, err, captured = WithCapturedPrint(function()
          handler(btn, "LeftButton", true)
        end)
        res.C_handlerOk  = ok                                         -- C
        res.handlerErr   = ok and "" or tostring(err)
        res.captured     = captured
        res.reacted      = false
        for _, line in ipairs(captured) do
          if line:find("MOVE TO SPOT", 1, true) then res.reacted = true end
        end

        -- D : état métier modifié par le handler ?
        local after = ShallowSnap()
        local changed = {}
        for k, v in pairs(after) do
          if before[k] ~= v then changed[#changed + 1] = k end
        end
        for k in pairs(before) do
          if after[k] == nil then changed[#changed + 1] = "DEL:" .. k end
        end
        res.changedKeys    = changed
        res.D_stateChanged = #changed > 0                             -- D

        -- F : comportement legacy attendu = journal clickLog MOVE_TO_SPOT
        local cl = (type(CasinobabeDB) == "table") and CasinobabeDB.clickLog or nil
        local last = (type(cl) == "table" and #cl > 0) and tostring(cl[#cl]) or ""
        res.F_legacyClickLog = (last:find("MOVE TO SPOT", 1, true) ~= nil) -- F
      end
    end

    res.errSeqAfter = ErrSeq()
    res.newLuaError = (res.errSeqAfter or 0) > (res.errSeqBefore or 0)
    res.E_persistence = nil -- renseigné post-ReloadUI

    res.pass = (res.A_buttonExists == true) and (res.B_handlerExists == true)
           and (res.C_handlerOk == true) and (res.reacted == true)
    res.functional = res.pass
                 and (res.D_stateChanged == true)
                 and (res.F_legacyClickLog == true)
    if res.pass and not res.functional then
      res.functionalNote = "bouton+handler OK, persistance OK possible, mais aucun effet métier/journal -> fonctionnalite reellement CASSEE (print-only)"
    end
    return res
  end

  -- Moteur de cycle : vérif persistance (E) -> exécution -> ReloadUI
  local sessionStarted = false

  local function RunCycle()
    if sessionStarted then return end
    sessionStarted = true

    C_Timer.After(4, function()
      if not IsQATarget() then return end
      local qa = QADB()

      if not qa.installed then
        qa.installed = true
        qa.armed = true
        qa.cycle = 0
        qa.maxCycles = 2
        QALog("1re installation détectée : armement automatique (2 cycles)")
      end

      if qa.pendingVerifyRunId then
        for _, r in ipairs(qa.runs) do
          if r.id == qa.pendingVerifyRunId then
            r.E_persistence = true
            qa.lastVerdict  = (r.pass and "PASS" or "FAIL")
                            .. "|functional=" .. tostring(r.functional)
                            .. "|persisted=true"
            QALog(("persistance vérifiée run=%s verdict=%s"):format(r.id, qa.lastVerdict))
          end
        end
        qa.pendingVerifyRunId = nil
      end

      if not qa.armed or qa.cycle >= qa.maxCycles then
        qa.armed = false
        return
      end

      qa.cycle = qa.cycle + 1
      local res = Scenario_MoveToSpot()
      res.id    = ("run-%d-c%d"):format(time(), qa.cycle)
      res.cycle = qa.cycle
      qa.runs[#qa.runs + 1] = res
      while #qa.runs > MAX_RUNS do table.remove(qa.runs, 1) end
      qa.pendingVerifyRunId = res.id
      qa.lastRunId = res.id

      QALog(("MOVE_TO_SPOT A=%s B=%s C=%s D=%s F=%s err=%s")
        :format(tostring(res.A_buttonExists), tostring(res.B_handlerExists),
                tostring(res.C_handlerOk), tostring(res.D_stateChanged),
                tostring(res.F_legacyClickLog), tostring(res.handlerErr)))
      QALog("ReloadUI pour preuve de persistance...")
      C_Timer.After(1.5, ReloadUI)
    end)
  end

  local ev = CreateFrame("Frame")
  ev:RegisterEvent("PLAYER_LOGIN")
  ev:RegisterEvent("PLAYER_ENTERING_WORLD")
  ev:SetScript("OnEvent", function(_, event)
    -- Gate AVANT toute écriture : l'instance personnelle ne doit voir
    -- AUCUNE modification de son CasinobabeDB (même un compteur).
    if not IsQATarget() then return end
    if event == "PLAYER_LOGIN" then
      local qa = QADB()
      qa.bootCount = (qa.bootCount or 0) + 1
      RunCycle()
    elseif event == "PLAYER_ENTERING_WORLD" then
      RunCycle()
    end
  end)

  -- Contrôle optionnel : /cbqa run | status | disarm (gate inclus)
  SLASH_CASINOBABE_QA1 = "/cbqa"
  SlashCmdList["CASINOBABE_QA"] = function(msg)
    if not IsQATarget() then QALog("refusé : personnage hors périmètre QA") return end
    local qa = QADB()
    msg = (msg or ""):match("^%s*(.-)%s*$"):lower()
    if msg == "run" then
      qa.armed = true; qa.cycle = 0; qa.maxCycles = 2
      QALog("armé (2 cycles) — ReloadUI")
      ReloadUI()
    elseif msg == "disarm" then
      qa.armed = false
      QALog("désarmé")
    elseif msg == "status" then
      QALog(("armed=%s cycle=%d/%d boots=%d runs=%d verdict=%s")
        :format(tostring(qa.armed), qa.cycle, qa.maxCycles,
                qa.bootCount or 0, #qa.runs, tostring(qa.lastVerdict)))
    else
      QALog("usage: /cbqa run | status | disarm")
    end
  end
end)()
