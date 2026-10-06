--[[
WoW API Mock Environment for CasinoBae Offline Testing
Load this BEFORE loading Casinobabe.lua to provide a complete WoW API simulation.
]]

-- ============================================================================
-- GLOBAL STATE & CONFIGURATION
-- ============================================================================

local WoWMock = {}
WoWMock.version = "1.0.0"
WoWMock.targetInterface = 20505 -- WoW Classic Anniversary

-- Track all API calls for debugging
WoWMock.apiCalls = {}
WoWMock.errors = {}
WoWMock.warnings = {}

-- Configuration
WoWMock.config = {
    playerName = "Casinobae",
    playerRealm = "TestRealm",
    isDealer = true,
    zone = "Orgrimmar",
    subZone = "Valley of Strength",
    inInstance = false,
    instanceType = "none",
    groupType = "raid", -- "party", "raid", "none"
    groupMembers = 5,
    raidRoster = {},
    channels = {
        { slot = 1, id = 1, name = "General" },
        { slot = 2, id = 2, name = "Trade" },
        { slot = 3, id = 3, name = "LocalDefense" },
        { slot = 6, id = 6, name = "CasinoBae" }
    },
    time = 0,
    getTime = 0,
    money = { player = 1000000, target = 0 }, -- copper
    tradeOpen = false,
    tradeTarget = nil,
    chatLog = {},
    uiFrames = {},
        allFrames = {},
    timers = {},
    tickers = {},
    addonMessages = {},
    registeredPrefixes = {},
    whoResults = {},
    pendingWhoQuery = false
}

-- ============================================================================
-- UTILITY FUNCTIONS
-- ============================================================================

local function logApiCall(api, args)
    table.insert(WoWMock.apiCalls, {
        api = api,
        args = args,
        time = WoWMock.config.time
    })
end

local function createError(phase, fn, file, line, message, traceback)
    local fingerprint = string.format("%s|%s|%s|%d|%s", "Casinobabe",
        "AddonPrint", file or "unknown", line or 0, message or "")
    local err = {
        phase = phase,
        ["function"] = fn,
        file = file,
        line = line,
        message = message,
        traceback = traceback,
        fingerprint = fingerprint,
        timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ")
    }
    table.insert(WoWMock.errors, err)
    return err
end

-- Handler d'erreur par defaut (comme WoW : affiche en rouge + stocke).
-- Les tests peuvent lire l'erreur via WoWMock.config.lastError.
local function defaultErrorHandler(msg)
    msg = tostring(msg)
    print("|cffFF0000Lua Error|r: " .. msg)
    WoWMock.config.lastError = msg
end

-- Handler stable : geterrorhandler() renvoie toujours la meme table, sinon
-- `seterrorhandler(old)` ne chaine rien (old change a chaque appel).
local currentErrorHandler = defaultErrorHandler

function geterrorhandler()
    return function(msg) return currentErrorHandler(msg) end
end

function seterrorhandler(handler)
    currentErrorHandler = handler
end

function notifyErrorHandler(msg)
    pcall(currentErrorHandler, tostring(msg))
end
WoWMock.notifyErrorHandler = notifyErrorHandler

local function warn(msg)
    table.insert(WoWMock.warnings, { message = msg, time = WoWMock.config.time })
end

-- Strict global environment - catch undefined globals
local declaredGlobals = {}
local function declareGlobal(name)
    declaredGlobals[name] = true
end

local function isKnownWoWGlobal(name)
    local known = {
        -- Core Lua
        "print", "error", "assert", "pcall", "xpcall", "select", "ipairs", "pairs", "next",
        "type", "tonumber", "tostring", "rawget", "rawset", "rawlen", "setmetatable", "getmetatable",
        "table", "string", "math", "coroutine", "debug", "os", "io", "package",
        -- WoW Globals (APIs)
        "CreateFrame", "C_Timer", "UnitName", "GetRealZoneText", "GetSubZoneText",
        "GetZoneText", "IsInInstance", "SendChatMessage", "PlaySound", "GetChannelList",
        "GetTargetTradeMoney", "GetPlayerTradeMoney", "InitiateTrade", "GetNumGroupMembers",
        "GetNumRaidMembers", "GetRaidRosterInfo", "GetTime", "time", "date",
        "SendAddonMessage", "RegisterAddonMessagePrefix", "C_ChatInfo", "C_FriendList",
        "C_TradeInfo", "GetNumWhoResults", "SendWho", "SetWhoToUi",
        "FlashClientIcon", "GetCursorPosition", "GetScreenWidth", "GetScreenHeight",
        "UIParent", "Minimap", "BackdropTemplateMixin", "UISpecialFrames",
        "C_Timer", "LibStub", "LibDBIcon10_Casinobabe",
        -- CasinoBae internal
        "CasinobabeDB", "CasinobabeErrorBus", "CB", "CasinoBae", "Casinobabe", "AddonPrint",
        -- Event names
        "PLAYER_LOGIN", "PLAYER_ENTERING_WORLD", "ADDON_LOADED", "CHAT_MSG_ADDON",
        "CHAT_MSG_SYSTEM", "CHAT_MSG_WHISPER", "CHAT_MSG_WHISPER_INFORM",
        "CHAT_MSG_SAY", "CHAT_MSG_YELL", "CHAT_MSG_EMOTE", "CHAT_MSG_TEXT_EMOTE",
        "CHAT_MSG_CHANNEL", "CHAT_MSG_PARTY", "CHAT_MSG_PARTY_LEADER",
        "CHAT_MSG_RAID", "CHAT_MSG_RAID_LEADER", "CHAT_MSG_INSTANCE_CHAT",
        "CHAT_MSG_INSTANCE_CHAT_LEADER", "CHAT_MSG_GUILD", "CHAT_MSG_OFFICER",
        "CHAT_MSG_CHANNEL_NOTICE", "GROUP_ROSTER_UPDATE", "PARTY_MEMBERS_CHANGED",
        "RAID_ROSTER_UPDATE", "PLAYER_TARGET_CHANGED", "ZONE_CHANGED",
        "ZONE_CHANGED_NEW_AREA", "TRADE_SHOW", "TRADE_ACCEPT_UPDATE", "TRADE_CLOSED",
        "UI_INFO_MESSAGE", "WHO_LIST_UPDATE"
    }
    for _, k in ipairs(known) do
        if k == name then return true end
    end
    return false
end

-- Metatable for strict global access
local strictGlobalMt = {
    __index = function(t, k)
        if declaredGlobals[k] then
            return rawget(t, k)
        end
        if isKnownWoWGlobal(k) then
            -- Allow known WoW globals
            return rawget(t, k)
        end
        -- This is an undefined global access
        createError("UNDEFINED_GLOBAL", "global_read", "runtime", 0,
            string.format("Attempt to read undefined global: %s", k), debug.traceback())
        return nil
    end,
    __newindex = function(t, k, v)
        if not declaredGlobals[k] and not isKnownWoWGlobal(k) then
            -- Global WRITES are legal Lua/WoW (lint-level only). Reads of
            -- undefined globals are the real crash predictors and stay errors.
            WoWMock.warnings = WoWMock.warnings or {}
            table.insert(WoWMock.warnings, string.format("GLOBAL_WRITE %s = %s", k, type(v)))
            logApiCall("GlobalWrite", { name = k, kind = type(v) })
        end
        rawset(t, k, v)
        declaredGlobals[k] = true
    end
}

-- Apply strict global checking
setmetatable(_G, strictGlobalMt)

-- Pre-declare all known WoW globals
for _, name in ipairs({
    "CreateFrame", "C_Timer", "UnitName", "GetRealZoneText", "GetSubZoneText", "GetZoneText",
    "IsInInstance", "SendChatMessage", "PlaySound", "GetChannelList", "GetTargetTradeMoney",
    "GetPlayerTradeMoney", "InitiateTrade", "GetNumGroupMembers", "GetNumRaidMembers",
    "GetRaidRosterInfo", "GetTime", "time", "date", "SendAddonMessage", "RegisterAddonMessagePrefix",
    "C_ChatInfo", "C_FriendList", "C_TradeInfo", "GetNumWhoResults", "SendWho", "SetWhoToUi",
    "FlashClientIcon", "GetCursorPosition", "GetScreenWidth", "GetScreenHeight",
    "UIParent", "Minimap", "BackdropTemplateMixin", "UISpecialFrames", "LibStub",
    "CasinobabeDB", "CasinobabeErrorBus", "CB", "CasinoBae",
    "PLAYER_LOGIN", "PLAYER_ENTERING_WORLD", "ADDON_LOADED", "CHAT_MSG_ADDON",
    "CHAT_MSG_SYSTEM", "CHAT_MSG_WHISPER", "CHAT_MSG_WHISPER_INFORM", "CHAT_MSG_SAY",
    "CHAT_MSG_YELL", "CHAT_MSG_EMOTE", "CHAT_MSG_TEXT_EMOTE", "CHAT_MSG_CHANNEL",
    "CHAT_MSG_PARTY", "CHAT_MSG_PARTY_LEADER", "CHAT_MSG_RAID", "CHAT_MSG_RAID_LEADER",
    "CHAT_MSG_INSTANCE_CHAT", "CHAT_MSG_INSTANCE_CHAT_LEADER", "CHAT_MSG_GUILD",
    "CHAT_MSG_OFFICER", "CHAT_MSG_CHANNEL_NOTICE", "GROUP_ROSTER_UPDATE",
    "PARTY_MEMBERS_CHANGED", "RAID_ROSTER_UPDATE", "PLAYER_TARGET_CHANGED",
    "ZONE_CHANGED", "ZONE_CHANGED_NEW_AREA", "TRADE_SHOW", "TRADE_ACCEPT_UPDATE",
    "TRADE_CLOSED", "UI_INFO_MESSAGE", "WHO_LIST_UPDATE"
}) do
    declareGlobal(name)
end

-- ============================================================================
-- TIME MANAGEMENT
-- ============================================================================

function time()
    return WoWMock.config.time
end

function GetTime()
    return WoWMock.config.getTime
end

function date(format, t)
    t = t or WoWMock.config.time
    return os.date(format, t)
end

function WoWMock.advanceTime(seconds)
    WoWMock.config.time = WoWMock.config.time + seconds
    WoWMock.config.getTime = WoWMock.config.getTime + seconds
    -- Process timers
    local expired = {}
    for id, timer in pairs(WoWMock.config.timers) do
        if timer.endTime <= WoWMock.config.time then
            table.insert(expired, id)
            local ok, err = pcall(timer.callback)
            if not ok then
                createError("TIMER_CALLBACK", timer.name or "unknown", "timer", 0, tostring(err), debug.traceback())
            end
        end
    end
    for _, id in ipairs(expired) do
        WoWMock.config.timers[id] = nil
    end
    -- Process tickers
    for id, ticker in pairs(WoWMock.config.tickers) do
        ticker.nextTick = ticker.nextTick + ticker.interval
        if ticker.nextTick <= WoWMock.config.time then
            local ok, err = pcall(ticker.callback, ticker.interval)
            if not ok then
                createError("TICKER_CALLBACK", ticker.name or "unknown", "ticker", 0, tostring(err), debug.traceback())
            end
        end
    end
end

-- ============================================================================
-- C_TIMER MOCK
-- ============================================================================

C_Timer = {}
C_Timer._timers = {}
C_Timer._tickers = {}
C_Timer._nextId = 1

function C_Timer.After(delay, callback)
    logApiCall("C_Timer.After", { delay = delay })
    local id = C_Timer._nextId
    C_Timer._nextId = C_Timer._nextId + 1
    local timer = {
        id = id,
        name = "C_Timer.After#" .. id,
        callback = callback,
        endTime = WoWMock.config.time + delay,
        delay = delay
    }
    C_Timer._timers[id] = timer
    WoWMock.config.timers[id] = timer
    return id
end

function C_Timer.NewTicker(interval, callback)
    logApiCall("C_Timer.NewTicker", { interval = interval })
    local id = C_Timer._nextId
    C_Timer._nextId = C_Timer._nextId + 1
    local ticker = {
        id = id,
        name = "C_Timer.NewTicker#" .. id,
        callback = callback,
        interval = interval,
        nextTick = WoWMock.config.time + interval
    }
    C_Timer._tickers[id] = ticker
    WoWMock.config.tickers[id] = ticker
    return {
        Cancel = function() C_Timer._tickers[id] = nil; WoWMock.config.tickers[id] = nil end,
        _id = id
    }
end

function C_Timer.Cancel(id)
    logApiCall("C_Timer.Cancel", { id = id })
    C_Timer._timers[id] = nil
    WoWMock.config.timers[id] = nil
end

-- ============================================================================
-- UNIT & ZONE APIS
-- ============================================================================

function UnitName(unit)
    logApiCall("UnitName", { unit = unit })
    if unit == "player" then
        return WoWMock.config.playerName
    elseif unit == "target" then
        return WoWMock.config.tradeTarget
    elseif unit == "npc" then
        return WoWMock.config.tradeTarget
    elseif unit and unit:match("^party(%d+)$") then
        local idx = tonumber(unit:match("^party(%d+)$"))
        if WoWMock.config.raidRoster[idx] then
            return WoWMock.config.raidRoster[idx].name
        end
    elseif unit and unit:match("^raid(%d+)$") then
        local idx = tonumber(unit:match("^raid(%d+)$"))
        if WoWMock.config.raidRoster[idx] then
            return WoWMock.config.raidRoster[idx].name
        end
    end
    return nil
end

function GetRealZoneText()
    logApiCall("GetRealZoneText", {})
    return WoWMock.config.zone
end

function GetSubZoneText()
    logApiCall("GetSubZoneText", {})
    return WoWMock.config.subZone
end

function GetZoneText()
    logApiCall("GetZoneText", {})
    return WoWMock.config.zone
end

function IsInInstance()
    logApiCall("IsInInstance", {})
    return WoWMock.config.inInstance, WoWMock.config.instanceType
end

-- ============================================================================
-- GROUP / RAID APIS
-- ============================================================================

function GetNumGroupMembers()
    logApiCall("GetNumGroupMembers", {})
    return WoWMock.config.groupMembers
end

function GetNumRaidMembers()
    logApiCall("GetNumRaidMembers", {})
    return WoWMock.config.groupMembers
end

function GetRaidRosterInfo(index)
    logApiCall("GetRaidRosterInfo", { index = index })
    local member = WoWMock.config.raidRoster[index]
    if member then
        return member.name, member.rank, member.subgroup, member.level, member.class,
               member.fileName, member.zone, member.online, member.isDead,
               member.role, member.isML
    end
    return nil
end

function UnitIsGroupLeader(unit)
    logApiCall("UnitIsGroupLeader", { unit = unit })
    if unit == "player" then
        return WoWMock.config.isDealer
    end
    return false
end

function UnitInParty(unit)
    logApiCall("UnitInParty", { unit = unit })
    -- Simplified: check if unit is in our raid roster
    for _, member in ipairs(WoWMock.config.raidRoster) do
        if member.name == UnitName(unit) then return true end
    end
    return false
end

function UnitInRaid(unit)
    logApiCall("UnitInRaid", { unit = unit })
    return UnitInParty(unit)
end

-- ============================================================================
-- CHAT & COMMUNICATION
-- ============================================================================

function SendChatMessage(text, channel, language, target)
    logApiCall("SendChatMessage", { text = text, channel = channel, language = language, target = target })
    local entry = {
        text = text,
        channel = channel,
        language = language,
        target = target,
        time = WoWMock.config.time,
        sender = WoWMock.config.playerName
    }
    table.insert(WoWMock.config.chatLog, entry)
    return true
end

function PlaySound(soundID, channel)
    logApiCall("PlaySound", { soundID = soundID, channel = channel })
    return true
end

function GetChannelList()
    logApiCall("GetChannelList", {})
    local result = {}
    for _, ch in ipairs(WoWMock.config.channels) do
        table.insert(result, ch.slot)
        table.insert(result, ch.name)
    end
    return unpack(result)
end

-- ============================================================================
-- TRADE APIS
-- ============================================================================

function GetTargetTradeMoney()
    logApiCall("GetTargetTradeMoney", {})
    return WoWMock.config.money.target
end

function GetPlayerTradeMoney()
    logApiCall("GetPlayerTradeMoney", {})
    return WoWMock.config.money.player
end

function InitiateTrade(target)
    logApiCall("InitiateTrade", { target = target })
    WoWMock.config.tradeOpen = true
    WoWMock.config.tradeTarget = target
    return true
end

function AcceptTrade()
    logApiCall("AcceptTrade", {})
    return true
end

function CloseTrade()
    logApiCall("CloseTrade", {})
    WoWMock.config.tradeOpen = false
    WoWMock.config.tradeTarget = nil
    WoWMock.config.money.target = 0
    return true
end

-- ============================================================================
-- ADDON MESSAGING
-- ============================================================================

C_ChatInfo = {}
C_ChatInfo.SendAddonMessage = function(prefix, message, channel, target)
    logApiCall("C_ChatInfo.SendAddonMessage", { prefix = prefix, message = message, channel = channel, target = target })
    table.insert(WoWMock.config.addonMessages, {
        prefix = prefix, message = message, channel = channel, target = target, time = WoWMock.config.time
    })
    return true
end

C_ChatInfo.RegisterAddonMessagePrefix = function(prefix)
    logApiCall("C_ChatInfo.RegisterAddonMessagePrefix", { prefix = prefix })
    WoWMock.config.registeredPrefixes[prefix] = true
    return true
end

C_ChatInfo.IsAddonMessagePrefixRegistered = function(prefix)
    return WoWMock.config.registeredPrefixes[prefix] == true
end

-- Legacy support
SendAddonMessage = C_ChatInfo.SendAddonMessage
RegisterAddonMessagePrefix = C_ChatInfo.RegisterAddonMessagePrefix

-- ============================================================================
-- FRIEND LIST / WHO
-- ============================================================================

C_FriendList = {}
C_FriendList.GetNumWhoResults = function()
    return #WoWMock.config.whoResults
end

C_FriendList.SendWho = function(query)
    logApiCall("C_FriendList.SendWho", { query = query })
    WoWMock.config.pendingWhoQuery = true
    -- Simulate results after a short delay
    WoWMock.config.whoResults = {
        { name = "Casinobae", level = 60, class = "Mage", zone = "Orgrimmar", guild = "The House" },
        { name = "Player1", level = 58, class = "Warrior", zone = "Orgrimmar", guild = "Guild1" },
        { name = "Player2", level = 55, class = "Priest", zone = "Orgrimmar", guild = "Guild2" }
    }
    -- Trigger WHO_LIST_UPDATE event
    WoWMock.fireEvent("WHO_LIST_UPDATE")
    return true
end

C_FriendList.SetWhoToUi = function(enabled)
    logApiCall("C_FriendList.SetWhoToUi", { enabled = enabled })
end

-- ============================================================================
-- EVENT SYSTEM
-- ============================================================================

local eventHandlers = {}

local FramePrototype = {}
FramePrototype.__index = FramePrototype

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:UnregisterEvent(event)
    logApiCall("Frame:UnregisterEvent", { event = event })
    if eventHandlers[event] then
        for i, h in ipairs(eventHandlers[event]) do
            if h == self then
                table.remove(eventHandlers[event], i)
                break
            end
        end
    end
end

function FramePrototype:SetScript(event, callback)
    logApiCall("Frame:SetScript", { event = event })
    self.scripts = self.scripts or {}
    self.scripts[event] = callback
end

function FramePrototype:GetScript(event)
    self.scripts = self.scripts or {}
    return self.scripts[event]
end

function FramePrototype:Show()
    logApiCall("Frame:Show", {})
    self.visible = true
end

function FramePrototype:Hide()
    logApiCall("Frame:Hide", {})
    self.visible = false
end

function FramePrototype:IsShown()
    return self.visible == true
end

function FramePrototype:SetSize(w, h)
    self.width = w
    self.height = h
end

function FramePrototype:GetWidth()
    return self.width or 0
end

function FramePrototype:GetHeight()
    return self.height or 0
end

function FramePrototype:SetPoint(point, relativeTo, relativePoint, x, y)
    self.points = self.points or {}
    table.insert(self.points, { point, relativeTo, relativePoint, x, y })
end

function FramePrototype:GetPoint(index)
    if self.points and self.points[index] then
        return unpack(self.points[index])
    end
    return nil
end

function FramePrototype:SetFrameLevel(level)
    self.frameLevel = level
end

function FramePrototype:GetFrameLevel()
    return self.frameLevel or 1
end

function FramePrototype:SetFrameStrata(strata)
    self.frameStrata = strata
end

function FramePrototype:EnableMouse(enabled)
    self.mouseEnabled = enabled
end

function FramePrototype:SetMovable(movable)
    self.movable = movable
end

function FramePrototype:RegisterForDrag(button)
    self.dragButton = button
end

function FramePrototype:SetClampedToScreen(clamped)
    self.clampedToScreen = clamped
end

function FramePrototype:SetBackdrop(backdrop)
    self.backdrop = backdrop
end

function FramePrototype:SetBackdropColor(r, g, b, a)
    self.backdropColor = { r, g, b, a }
end

function FramePrototype:SetBackdropBorderColor(r, g, b, a)
    self.backdropBorderColor = { r, g, b, a }
end

function FramePrototype:CreateTexture(name, layer)
    local tex = {
        name = name,
        layer = layer,
        color = { 1, 1, 1, 1 },
        texture = nil,
        points = {},
        SetSize = function(self, w, h) self.width = w; self.height = h end,
        SetWidth = function(self, w) self.width = w end,
        SetHeight = function(self, h) self.height = h end,
        SetTexCoord = function(self, l, r, t, b) self.texCoord = { l, r, t, b } end,
        SetVertexColor = function(self, r, g, b, a) self.vertexColor = { r, g, b, a or 1 } end,
        SetBlendMode = function(self, mode) self.blendMode = mode end,
        SetDrawLayer = function(self, layer) self.layer = layer end,
        SetDesaturated = function(self, flag) self.desaturated = flag end,
        SetRotation = function(self, rad) self.rotation = rad end,
        SetAtlas = function(self, atlas) self.atlas = atlas end,
        SetColorTexture = function(self, r, g, b, a) self.color = { r, g, b, a } end,
        SetTexture = function(self, tex) self.texture = tex end,
        SetPoint = function(self, point, relativeTo, relativePoint, x, y)
            self.points = self.points or {}
            table.insert(self.points, { point, relativeTo, relativePoint, x, y })
        end,
        SetAllPoints = function(self, other)
            self.relativeTo = other
        end,
        SetAlpha = function(self, a) self.alpha = a end,
        GetAlpha = function(self) return self.alpha or 1 end,
        Hide = function(self) self.visible = false end,
        Show = function(self) self.visible = true end
    }
    return tex
end

function FramePrototype:CreateFontString(name, layer, inherits)
    local fs = {
        name = name,
        layer = layer,
        inherits = inherits,
        text = "",
        font = "Fonts\\FRIZQT__.TTF",
        fontSize = 12,
        fontFlags = "",
        color = { 1, 1, 1, 1 },
        justifyH = "LEFT",
        justifyV = "TOP",
        points = {},
        SetSize = function(self, w, h) self.width = w; self.height = h end,
        SetFontObject = function(self, obj) self.fontObject = obj end,
        SetTextInsets = function(self, l, r, t, b) self.textInsets = { l, r, t, b } end,
        SetShadowOffset = function(self, x, y) self.shadowOffset = { x, y } end,
        SetShadowColor = function(self, r, g, b, a) self.shadowColor = { r, g, b, a } end,
        SetNonSpaceWrap = function(self, v) self.nonSpaceWrap = v end,
        SetWordWrap = function(self, v) self.wordWrap = v end,
        SetIndentedWordWrap = function(self, v) self.indentedWordWrap = v end,
        SetMaxLines = function(self, n) self.maxLines = n end,
        SetDrawLayer = function(self, layer) self.layer = layer end,
        GetStringHeight = function(self) return (self.fontSize or 12) * 1.2 end,
        GetStringWidth = function(self) return #(self.text or "") * ((self.fontSize or 12) * 0.6) end,
        SetText = function(self, text) self.text = tostring(text) end,
        GetText = function(self) return self.text end,
        SetFont = function(self, font, size, flags) self.font = font; self.fontSize = size; self.fontFlags = flags end,
        SetTextColor = function(self, r, g, b, a) self.color = { r, g, b, a or 1 } end,
        SetJustifyH = function(self, h) self.justifyH = h end,
        SetJustifyV = function(self, v) self.justifyV = v end,
        SetPoint = function(self, point, relativeTo, relativePoint, x, y)
            self.points = self.points or {}
            table.insert(self.points, { point, relativeTo, relativePoint, x, y })
        end,
        SetWidth = function(self, w) self.width = w end,
        SetHeight = function(self, h) self.height = h end,
        Hide = function(self) self.visible = false end,
        Show = function(self) self.visible = true end
    }
    return fs
end

function FramePrototype:CreateButton(name, inherits)
    local btn = setmetatable({
        name = name,
        inherits = inherits,
        size = { 0, 0 },
        points = {},
        scripts = {},
        textures = {},
        fontStrings = {},
        SetSize = function(self, w, h) self.size = { w, h } end,
        SetPoint = function(self, point, relativeTo, relativePoint, x, y)
            self.points = self.points or {}
            table.insert(self.points, { point, relativeTo, relativePoint, x, y })
        end,
        SetScript = function(self, event, callback) self.scripts = self.scripts or {}; self.scripts[event] = callback end,
        GetScript = function(self, event) self.scripts = self.scripts or {}; return self.scripts[event] end,
        SetText = function(self, text) self.text = text end,
        GetText = function(self) return self.text end,
        SetNormalTexture = function(self, tex) self.normalTexture = tex end,
        SetHighlightTexture = function(self, tex) self.highlightTexture = tex end,
        SetPushedTexture = function(self, tex) self.pushedTexture = tex end,
        Disable = function(self) self.enabled = false end,
        Enable = function(self) self.enabled = true end,
        Hide = function(self) self.visible = false end,
        Show = function(self) self.visible = true end,
        IsShown = function(self) return self.visible ~= false end,
        CreateTexture = function(self, name, layer)
            local tex = FramePrototype:CreateTexture(name, layer)
            self.textures[name] = tex
            return tex
        end,
        CreateFontString = function(self, name, layer, inherits)
            local fs = FramePrototype:CreateFontString(name, layer, inherits)
            self.fontStrings[name] = fs
            return fs
        end
    }, frameMetatable(frameType))
    return btn
end

function FramePrototype:CreateFrame(frameType, name, parent, template)
    return CreateFrame(frameType, name, parent, template)
end

function FramePrototype:GetName() return self.name end
function FramePrototype:GetParent() return self.parent end
function FramePrototype:SetParent(parent) self.parent = parent end
function FramePrototype:SetScale(s) self.scale = s end
function FramePrototype:GetScale() return self.scale or 1 end
function FramePrototype:SetAlpha(a) self.alpha = a end
function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:GetParent() return self.parent end
function FramePrototype:SetParent(parent) self.parent = parent end
function FramePrototype:SetScale(s) self.scale = s end
function FramePrototype:GetScale() return self.scale or 1 end
function FramePrototype:SetAlpha(a) self.alpha = a end
function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:SetParent(parent) self.parent = parent end
function FramePrototype:SetScale(s) self.scale = s end
function FramePrototype:GetScale() return self.scale or 1 end
function FramePrototype:SetAlpha(a) self.alpha = a end
function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:SetScale(s) self.scale = s end
function FramePrototype:GetScale() return self.scale or 1 end
function FramePrototype:SetAlpha(a) self.alpha = a end
function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:GetScale() return self.scale or 1 end
function FramePrototype:SetAlpha(a) self.alpha = a end
function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:SetAlpha(a) self.alpha = a end
function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:GetAlpha() return self.alpha or 1 end
function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:SetShown(shown)
    if shown then self:Show() else self:Hide() end
end

function FramePrototype:GetCenter()
    local w, h = self.width or 0, self.height or 0
    return (self.centerX or w / 2), (self.centerY or h / 2)
end

function FramePrototype:ClearAllPoints() self.points = {} end
function FramePrototype:SetAllPoints(other) self.allPoints = other or self.parent or true end
function FramePrototype:GetChildren() return unpack(self.children or {}) end
function FramePrototype:IsVisible() return self.visible ~= false end
function FramePrototype:IsShowing() return self.visible ~= false end
function FramePrototype:SetEnabled(enabled) self.enabled = enabled ~= false end
function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetAllPoints(other) self.allPoints = other or self.parent or true end
function FramePrototype:GetChildren() return unpack(self.children or {}) end
function FramePrototype:IsVisible() return self.visible ~= false end
function FramePrototype:IsShowing() return self.visible ~= false end
function FramePrototype:SetEnabled(enabled) self.enabled = enabled ~= false end
function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:GetChildren() return unpack(self.children or {}) end
function FramePrototype:IsVisible() return self.visible ~= false end
function FramePrototype:IsShowing() return self.visible ~= false end
function FramePrototype:SetEnabled(enabled) self.enabled = enabled ~= false end
function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:IsVisible() return self.visible ~= false end
function FramePrototype:IsShowing() return self.visible ~= false end
function FramePrototype:SetEnabled(enabled) self.enabled = enabled ~= false end
function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:IsShowing() return self.visible ~= false end
function FramePrototype:SetEnabled(enabled) self.enabled = enabled ~= false end
function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetEnabled(enabled) self.enabled = enabled ~= false end
function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:IsEnabled() return self.enabled ~= false end
function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetUserPlaced(v) self.userPlaced = v end
function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetID(id) self.id = id end
function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:GetID() return self.id or 0 end
function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:EnableMouseWheel(v) self.mouseWheel = v end
function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:StopMovingOrSizing() self.moving = false end
function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetDrawLayer(layer) self.layer = layer end
-- Cooldown : l'addon appelle SetCooldown sur des frames de type Cooldown.
function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetCooldown(start, duration) self.cooldown = { start = start, duration = duration } end
function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:GetCooldown() return self.cooldown and self.cooldown.start, self.cooldown and self.cooldown.duration end
function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetCooldownFinished() self.cooldown = nil end

function FramePrototype:RegisterEvent(event)
    logApiCall("Frame:RegisterEvent", { event = event })
    eventHandlers[event] = eventHandlers[event] or {}
    table.insert(eventHandlers[event], self)
end

function FramePrototype:SetWidth(w) self.width = w end
function FramePrototype:SetHeight(h) self.height = h end

function FramePrototype:GetWidth()
    return self.width or 0
end

function FramePrototype:SetHeight(h) self.height = h end

function FramePrototype:GetWidth()
    return self.width or 0
end

-- Prototypes par type de widget. Declare AVANT frameMetatable : Lua lie les
-- locaux de maniere positionnelle, une declaration plus bas serait inexistante
-- au moment de la lecture.
local UIObjectPrototypes = {}

-- Chaque frameType recupere ses methodes propres (Button:SetText,
-- EditBox:GetText, ...), puis FramePrototype. Sans cela, CreateFrame("Button")
-- ne sait pas SetHighlightTexture alors que WoW si.
local function frameMetatable(frameType)
    local proto = UIObjectPrototypes and UIObjectPrototypes[frameType]
    if not proto then
        return FramePrototype
    end
    return {
        __index = function(_, key)
            local v = proto[key]
            if v ~= nil then return v end
            return FramePrototype[key]
        end
    }
end

-- Global CreateFrame
function CreateFrame(frameType, name, parent, template)
    logApiCall("CreateFrame", { frameType = frameType, name = name, parent = parent and parent.name or parent, template = template })
    local frame = setmetatable({
        name = name,
        frameType = frameType,
        parent = parent,
        template = template,
        visible = true,
        width = 0,
        height = 0,
        frameLevel = 1,
        scripts = {},
        textures = {},
        fontStrings = {},
        buttons = {},
        children = {}
    }, FramePrototype)

    -- Registre complet (y compris frames anonymes) : c'est lui qui fait
    -- tourner les OnUpdate (demoWatcher, drag du minimap, watchdog...).
    table.insert(WoWMock.config.allFrames, frame)

    if parent then
        parent.children = parent.children or {}
        table.insert(parent.children, frame)
    end

    -- Real WoW allows anonymous frames (name == nil); only named frames are
    -- registered globally. Indexing with nil is a mock-only crash, not an addon bug.
    if name ~= nil then
        WoWMock.config.uiFrames = WoWMock.config.uiFrames or {}
        WoWMock.config.uiFrames[name] = frame
    end
    return frame
end

-- Event firing
function WoWMock.fireEvent(event, ...)
    local handlers = eventHandlers[event]
    if handlers then
        for _, handler in ipairs(handlers) do
            if handler.scripts and handler.scripts.OnEvent then
                local ok, err = pcall(handler.scripts.OnEvent, handler, event, ...)
                if not ok then
                    createError("EVENT_HANDLER", "OnEvent", "event", 0,
                        string.format("Error in %s handler: %s", event, tostring(err)), debug.traceback())
                WoWMock.notifyErrorHandler(tostring(err))
                end
            end
        end
    end
end

-- ============================================================================
-- UI OBJECTS (Texture, FontString, StatusBar, ScrollFrame, etc.)
-- ============================================================================

-- (deplace plus haut) local UIObjectPrototypes = {} -- voir frameMetatable

-- Texture
UIObjectPrototypes.Texture = {
    SetColorTexture = function(self, r, g, b, a) self.color = { r, g, b, a or 1 } end,
    SetTexture = function(self, tex) self.texture = tex end,
    SetVertexColor = function(self, r, g, b, a) self.vertexColor = { r, g, b, a or 1 } end,
    SetPoint = function(self, point, relativeTo, relativePoint, x, y)
        self.points = self.points or {}
        table.insert(self.points, { point, relativeTo, relativePoint, x, y })
    end,
    SetAllPoints = function(self, other) self.relativeTo = other end,
    SetAlpha = function(self, a) self.alpha = a end,
    GetAlpha = function(self) return self.alpha or 1 end,
    Hide = function(self) self.visible = false end,
    Show = function(self) self.visible = true end,
    SetWidth = function(self, w) self.width = w end,
    SetHeight = function(self, h) self.height = h end,
    SetTexCoord = function(self, l, r, t, b) self.texCoord = { l, r, t, b } end
}

-- FontString
UIObjectPrototypes.FontString = {
    SetText = function(self, text) self.text = tostring(text) end,
    GetText = function(self) return self.text end,
    SetFont = function(self, font, size, flags) self.font = font; self.fontSize = size; self.fontFlags = flags end,
    SetTextColor = function(self, r, g, b, a) self.color = { r, g, b, a or 1 } end,
    SetJustifyH = function(self, h) self.justifyH = h end,
    SetJustifyV = function(self, v) self.justifyV = v end,
    SetPoint = function(self, point, relativeTo, relativePoint, x, y)
        self.points = self.points or {}
        table.insert(self.points, { point, relativeTo, relativePoint, x, y })
    end,
    SetWidth = function(self, w) self.width = w end,
    SetHeight = function(self, h) self.height = h end,
    SetSpacing = function(self, s) self.spacing = s end,
    Hide = function(self) self.visible = false end,
    Show = function(self) self.visible = true end
}

-- StatusBar
UIObjectPrototypes.StatusBar = {
    SetStatusBarTexture = function(self, tex) self.statusBarTexture = tex end,
    GetStatusBarTexture = function(self) return self.statusBarTexture end,
    SetStatusBarColor = function(self, r, g, b, a) self.statusBarColor = { r, g, b, a or 1 } end,
    SetMinMaxValues = function(self, min, max) self.minValue = min; self.maxValue = max end,
    SetValue = function(self, val) self.value = val end,
    GetValue = function(self) return self.value or 0 end,
    SetOrientation = function(self, orient) self.orientation = orient end
}

-- ScrollFrame
UIObjectPrototypes.ScrollFrame = {
    SetScrollChild = function(self, child) self.scrollChild = child end,
    GetScrollChild = function(self) return self.scrollChild end,
    SetHorizontalScroll = function(self, offset) self.horizontalScroll = offset end,
    SetVerticalScroll = function(self, offset) self.verticalScroll = offset end,
    GetHorizontalScroll = function(self) return self.horizontalScroll or 0 end,
    GetVerticalScroll = function(self) return self.verticalScroll or 0 end,
    UpdateScrollChildRect = function(self) end
}

-- Button
UIObjectPrototypes.Button = {
    SetText = function(self, text) self.text = text end,
    GetText = function(self) return self.text end,
    SetNormalTexture = function(self, tex) self.normalTexture = tex end,
    SetHighlightTexture = function(self, tex) self.highlightTexture = tex end,
    SetPushedTexture = function(self, tex) self.pushedTexture = tex end,
    SetDisabledTexture = function(self, tex) self.disabledTexture = tex end,
    SetScript = function(self, event, callback) self.scripts = self.scripts or {}; self.scripts[event] = callback end,
    GetScript = function(self, event) self.scripts = self.scripts or {}; return self.scripts[event] end,
    Disable = function(self) self.enabled = false end,
    Enable = function(self) self.enabled = true end,
    IsEnabled = function(self) return self.enabled ~= false end,
    Click = function(self)
        if self.scripts and self.scripts.OnClick then
            local ok, err = pcall(self.scripts.OnClick, self)
            if not ok then createError("ONCLICK", self.name or "button", "onclick", 0, tostring(err), debug.traceback()) end
        end
    end,
    Hide = function(self) self.visible = false end,
    Show = function(self) self.visible = true end,
    IsShown = function(self) return self.visible ~= false end,
    SetSize = function(self, w, h) self.width = w; self.height = h end,
    SetPoint = function(self, point, relativeTo, relativePoint, x, y)
        self.points = self.points or {}
        table.insert(self.points, { point, relativeTo, relativePoint, x, y })
    end
}

-- EditBox
UIObjectPrototypes.EditBox = {
    SetText = function(self, text) self.text = tostring(text) end,
    GetText = function(self) return self.text end,
    SetNumeric = function(self, numeric) self.numeric = numeric end,
    SetAutoFocus = function(self, focus) self.autoFocus = focus end,
    ClearFocus = function(self) self.hasFocus = false end,
    SetFontObject = function(self, obj) self.fontObject = obj end,
    SetTextInsets = function(self, l, r, t, b) self.textInsets = { l, r, t, b } end,
    SetFont = function(self, font, size, flags) self.font = font; self.fontSize = size; self.fontFlags = flags end,
    SetTextColor = function(self, r, g, b, a) self.color = { r, g, b, a or 1 } end,
    SetPoint = function(self, point, relativeTo, relativePoint, x, y)
        self.points = self.points or {}
        table.insert(self.points, { point, relativeTo, relativePoint, x, y })
    end,
    SetSize = function(self, w, h) self.width = w; self.height = h end,
    Hide = function(self) self.visible = false end,
    Show = function(self) self.visible = true end
}

-- ============================================================================
-- BACKDROP TEMPLATE
-- ============================================================================

BackdropTemplateMixin = {
    OnLoad = function(self) end,
    OnSizeChanged = function(self) end
}

-- ============================================================================
-- LIBSTUB MOCK
-- ============================================================================

LibStub = {
    libs = {},
    minors = {},
    -- minor interne requis : la vraie LibStub (Libs/LibStub) compare
    -- `LibStub.minor < LIBSTUB_MINOR` pour decider de se reinstaller.
    minor = 0
}

function LibStub:GetLibrary(major, silent)
    if self.libs[major] then return self.libs[major] end
    if not silent then
        error("Library " .. major .. " not found")
    end
    return nil
end

function LibStub:NewLibrary(major, minor)
    if self.libs[major] and self.minors[major] >= minor then
        return nil
    end
    self.libs[major] = self.libs[major] or {}
    self.minors[major] = minor
    return self.libs[major]
end

function LibStub:IterateLibraries()
    return pairs(self.libs)
end

-- Mock LibDBIcon-1.0
local libDBIcon = LibStub:NewLibrary("LibDBIcon-1.0", 1)
if libDBIcon then
    libDBIcon.registry = {}
    libDBIcon.buttons = {}

    function libDBIcon:Register(name, dataObject, db)
        logApiCall("LibDBIcon:Register", { name = name })
        self.registry[name] = { dataObject = dataObject, db = db }
        -- Create a mock button
        local btn = CreateFrame("Button", "LibDBIcon10_" .. name, Minimap)
        btn.name = name
        btn.dataObject = dataObject
        btn.db = db
        self.buttons[name] = btn
        return true
    end

    function libDBIcon:Show(name)
        if self.buttons[name] then self.buttons[name]:Show() end
    end

    function libDBIcon:Hide(name)
        if self.buttons[name] then self.buttons[name]:Hide() end
    end

    function libDBIcon:IsRegistered(name)
        return self.registry[name] ~= nil
    end

    function libDBIcon:GetMinimapButton(name)
        return self.buttons[name]
    end

    function libDBIcon:Refresh(name, db)
        -- No-op in mock
    end
end

-- ============================================================================
-- DEBUG & ERROR HANDLING
-- ============================================================================

function geterrorhandler()
    return function(msg)
        createError("ERROR_HANDLER", "geterrorhandler", "error", 0, msg, debug.traceback())
    end
end

function seterrorhandler(handler)
    -- No-op in mock
end

-- ============================================================================
-- MATH/STRING/TABLE EXTENSIONS
-- ============================================================================

-- Ensure math.random works
math.randomseed(os.time())

-- ============================================================================

-- ============================================================================
-- WoW string aliases (le client expose ces globaux)
-- ============================================================================
strmatch = string.match
strfind = string.find
strsub = string.sub
strlen = string.len
strlower = string.lower
strupper = string.upper
strrep = string.rep
format = string.format
strsplit = function(delim, str) return str end
strjoin = function(delim, ...) return table.concat({...}, delim) end
tinsert = table.insert
tremove = table.remove
wipe = function(t) for k in pairs(t) do t[k] = nil end end


-- ============================================================================
-- Globales WoW de base (ajoutees pour que les bibliotheques du dossier Libs
-- et le runtime se chargent comme dans le client)
-- ============================================================================
function GetLocale() return "enUS" end
function UnitFactionGroup() return "Horde" end
function UnitLevel() return 60 end
function UnitClass() return "Warrior", "WARRIOR" end
function UnitRace() return "Orc", "Orc" end
function GetRealmName() return WoWMock.config.playerRealm end
function GetMoney() return WoWMock.config.money.player end
function InCombatLockdown() return false end
function StaticPopup_Hide() end
function debugstack() return "" end

-- INITIALIZATION
-- ============================================================================

-- Initialize SavedVariables
CasinobabeDB = CasinobabeDB or {}
CasinobabeErrorBus = CasinobabeErrorBus or {}

-- Initialize CB namespace
CB = CB or {}

-- Mock UIParent and Minimap
UIParent = CreateFrame("Frame", "UIParent")
Minimap = CreateFrame("Frame", "Minimap")

-- StaticPopup globals always exist in real WoW (mock-only gap otherwise).
-- rawset + declaredGlobals: define without logging our own init as errors.
rawset(_G, "StaticPopupDialogs", {})
declaredGlobals["StaticPopupDialogs"] = true
function StaticPopup_Show(which) logApiCall("StaticPopup_Show", { which = which }) return nil end
-- Slash command registry always exists in real WoW.
rawset(_G, "SlashCmdList", {})
declaredGlobals["SlashCmdList"] = true
-- Raid/group API exists in real WoW (addon guards with `IsInRaid and ...`).
-- Config-driven like GetNumGroupMembers above (mock default groupMembers = 5).
function IsInRaid() return (WoWMock.config.groupMembers or 0) > 0 end
function IsInGroup() return (WoWMock.config.groupMembers or 0) > 0 end
function GetNumPartyMembers() return WoWMock.config.groupMembers end
function GetNumSubgroupMembers() return WoWMock.config.groupMembers end
-- Localized UI globals always exist in real WoW (addon falls back with `or`).
rawset(_G, "CLOSE", "Close")
declaredGlobals["CLOSE"] = true
rawset(_G, "YES", "Yes")
declaredGlobals["YES"] = true
rawset(_G, "NO", "No")
declaredGlobals["NO"] = true

-- Expose WoWMock globally for test control
_G.WoWMock = WoWMock

print("|cffFFD700[WoW Mock]|r WoW API Mock Environment loaded v" .. WoWMock.version)
print("|cffFFD700[WoW Mock]|r Target Interface: " .. WoWMock.targetInterface)
print("|cffFFD700[WoW Mock]|r Strict global checking: ENABLED")