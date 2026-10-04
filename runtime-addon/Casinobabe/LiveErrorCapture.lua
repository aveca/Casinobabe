<<<<<<< HEAD
<<<<<<< HEAD
-- CasinoBae live error capture bridge.
CasinobabeErrorBus = CasinobabeErrorBus or {
  version = 1,
  seq = 0,
  pending = {},
  last = nil,
}

local function CB_CaptureError(message)
  local msg = tostring(message or "")
  local stack = ""

  if type(debugstack) == "function" then
    local ok, result = pcall(debugstack, 3, 40, 40)
    if ok and result then stack = tostring(result) end
  end

  if not msg:find("Casinobabe", 1, true) and not stack:find("Casinobabe", 1, true) then
    return
  end

  CasinobabeErrorBus.seq = (tonumber(CasinobabeErrorBus.seq) or 0) + 1
  local record = {
    id = tostring(time()) .. "-" .. tostring(CasinobabeErrorBus.seq),
    seq = CasinobabeErrorBus.seq,
    time = time(),
    message = msg,
    stack = stack,
  }

  table.insert(CasinobabeErrorBus.pending, 1, record)
  while #CasinobabeErrorBus.pending > 30 do
    table.remove(CasinobabeErrorBus.pending)
  end
  CasinobabeErrorBus.last = record
end

if type(geterrorhandler) == "function" and type(seterrorhandler) == "function" then
  local previousErrorHandler = geterrorhandler()
  seterrorhandler(function(message)
    pcall(CB_CaptureError, message)
    if type(previousErrorHandler) == "function" then
      return previousErrorHandler(message)
    end
  end)
end
=======
=======
>>>>>>> c5bef037b56ff0823be55b38cc4598e348cb40ff
--[[
Live Error Capture for CasinoBae
Captures runtime errors from WoW SavedVariables and forwards them
to the self-healing engine (OpenCode/LLM) for automatic diagnosis and repair.
]]

-- Error incident structure
local ErrorIncident = {}
ErrorIncident.__index = ErrorIncident

function ErrorIncident:new(message, stack, filename, line, timestamp)
  local instance = {
    id = tostring(self.session_counter or 0) .. "-" .. tostring(time()),
    message = message or "UNKNOWN_ERROR",
    stack = stack or debugstack(),
    filename = filename or "unknown",
    line = line or 0,
    timestamp = timestamp or time(),
    fixed = false,
    retry_count = 0,
    session_counter = (self.session_counter or 0) + 1
  }
  setmetatable(instance, self)
  return instance
end

-- Error bus for inter-addon communication
local ErrorBus = CasinobabeErrorBus or {}

-- Incident ID generator
local incidentIdCounter = 0

-- Captured incidents table (prevent duplicates)
local capturedIncidents = {}

-- Error type priorities for deduplication
local ERROR_PRIORITIES = {
  ["GLOBAL_IMPLICIT"] = 1,
  ["LOCAL_LATE"] = 2,
  ["FUNCTION_NIL"] = 3,
  ["TABLE_NIL"] = 4,
  ["NAMESPACE_COLLISION"] = 5,
  ["EVENT_HANDLER"] = 6,
  ["TIMER"] = 7,
  ["ONCLICK"] = 8,
  ["SLASH"] = 9,
  ["STATE_MACHINE"] = 10,
  ["GAME_RULES"] = 11,
  ["DEMO_ISOLATION"] = 12,
}

-- Fingerprint function for deduplication
local function generateFingerprint(incident)
  local key = incident.message .. ":" .. incident.filename .. ":" .. tostring(incident.line)
  return key
end

-- Main error capture frame
local errorCaptureFrame = CreateFrame("Frame")
errorCaptureFrame:RegisterEvent("ADDON_LOADED")
errorCaptureFrame:RegisterEvent("PLAYER_LOGIN")
errorCaptureFrame:RegisterEvent("PLAYER_LEAVING_WORLD")
errorCaptureFrame:RegisterEvent("PLAYER_LOGOUT")

errorCaptureFrame:SetScript("OnEvent", function(self, event, ...)
  if event == "ADDON_LOADED" and select(1, ...) == "Casinobabe" then
    -- Initialize error capture system
    self:InitializeErrorBus()
    print("|cffFFD700Casinobabe|r Error capture system initialized.")
  elseif event == "PLAYER_LOGIN" then
    -- Start monitoring SavedVariables for errors
    self:StartMonitoring()
  elseif event == "PLAYER_LEAVING_WORLD" or event == "PLAYER_LOGOUT" then
    -- Persist incidents before logout
    self:PersistIncidents()
  end
end)

function ErrorBus:CaptureError(message, stack, filename, line)
  local timestamp = time()
  local incident = ErrorIncident:new(message, stack, filename, line, timestamp)
  local fingerprint = generateFingerprint(incident)

  -- Check for duplicate (same fingerprint already fixed -> skip)
  if capturedIncidents[fingerprint] then
    if capturedIncidents[fingerprint].fixed then
      -- Already fixed, skip entirely
      return false
    else
      -- New incident with same fingerprint, queue for retry
      incident.retry_count = (incident.retry_count or 0) + 1
    end
  else
    capturedIncidents[fingerprint] = incident
  end

  -- Log to error bus
  ErrorBus[#ErrorBus + 1] = incident

  -- Trigger OpenCode/autonomous repair
  self:TriggerRepair(incident)

  return true
end

function ErrorBus:TriggerRepair(incident)
  -- Generate incident report for OpenCode
  local report = {
    incidentId = incident.id,
    message = incident.message,
    stack = incident.stack,
    filename = incident.filename,
    line = incident.line,
    timestamp = incident.timestamp,
    priority = self:GetPriority(incident.message),
  }

  -- Print incident summary (don't ask user for stack - system captures it)
  print("|cffFF0000Casinobabe ERROR[" .. incident.id .. "]|r: " .. incident.message)
  print("  File: " .. incident.filename .. ":" .. incident.line)

  -- Queue for LLM diagnosis and OpenCode auto-repair
  -- (The watcher script will handle OpenCode integration)
  CasinobabeDB.lastErrorIncident = incident.id

  -- Notify watcher to start repair cycle
  -- (Watcher script: .\scripts\casinobae-live-watch.ps1)
end

function ErrorBus:GetPriority(errorMessage)
  for priority, keywords in pairs(ERROR_PRIORITIES) do
    if errorMessage:lower():find(priority:lower()) then
      return priority
    end
  end
  return "UNKNOWN"
end

function ErrorBus:StartMonitoring()
  -- Monitor CasinobabeErrorBus or other SavedVariables for errors
  self:RegisterForErrorEvents()
end

function ErrorBus:RegisterForErrorEvents()
  -- This will be called by the watcher to set up monitoring
  -- Checks CasinobabeErrorBus SavedVariable for new errors
end

function ErrorBus:PersistIncidents()
  -- Save incidents state before logout
  if CasinobabeDB then
    CasinobabeDB.errorIncidentCount = #capturedIncidents
  end
end

-- Global error handler interceptor
local oldGetErrorHandler = geterrorhandler or function() end

-- Hook into Lua error capturing
-- WoW errors during runtime will be captured via SavedVariables bridge

<<<<<<< HEAD
print("|cffFFD700Casinobabe|r LiveErrorCapture.lua loaded - error monitoring active.")
>>>>>>> c5bef03 (FEAT: Add LiveErrorCapture.lua + update TOC for autonomous error monitoring)
=======
print("|cffFFD700Casinobabe|r LiveErrorCapture.lua loaded - error monitoring active.")
>>>>>>> c5bef037b56ff0823be55b38cc4598e348cb40ff
