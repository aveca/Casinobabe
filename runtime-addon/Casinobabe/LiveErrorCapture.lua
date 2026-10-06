-- CasinoBae Live Error Capture
-- WoW addons cannot write arbitrary files or open sockets. Uncaught errors are
-- therefore persisted into SavedVariables so an external Windows dev loop
-- (scripts/casinobae-live-sentinel.cjs, scripts/casinobae-error-sentinel.cjs)
-- can consume them without screenshots or copy/paste.
--
-- Two channels are fed by the single handler installed below:
--   1. CasinobabeErrorBus            : structured queue (seq/pending/last)
--   2. CasinobabeDB.lastError(.Count): compact incident persisted for tooling

-- ---------------------------------------------------------------------------
-- Channel 1 : CasinobabeErrorBus (SavedVariable, DATA ONLY).
-- WoW cannot serialise functions: nothing but scalars/tables may be written
-- here, which is why every helper below lives in a local scope.
-- ---------------------------------------------------------------------------
CasinobabeErrorBus = CasinobabeErrorBus or {
  version = 1,
  seq = 0,
  pending = {},
  last = nil,
}

local function BusAppend(message, stack)
  local bus = CasinobabeErrorBus
  if type(bus) ~= "table" or type(message) ~= "string" then return end
  -- Only Casinobabe incidents are ours; never swallow other addons' errors.
  if not string.find(message, "Casinobabe", 1, true) then return end

  bus.seq = (bus.seq or 0) + 1
  local record = {
    id = tostring(time()) .. "-" .. tostring(bus.seq),
    time = time(),
    message = message,
    stack = tostring(stack or ""),
  }

  bus.last = record
  bus.pending = bus.pending or {}
  table.insert(bus.pending, record)
  while #bus.pending > 25 do
    table.remove(bus.pending, 1)
  end
end

-- ---------------------------------------------------------------------------
-- Incident machinery (local table: never stored into a SavedVariable).
-- ---------------------------------------------------------------------------
local ErrorIncident = {}
ErrorIncident.__index = ErrorIncident

function ErrorIncident:new(message, stack, filename, line, timestamp)
  local instance = {
    id = tostring(time()) .. "-" .. tostring((self.session_counter or 0) + 1),
    message = message or "UNKNOWN_ERROR",
    stack = stack or "",
    filename = filename or "unknown",
    line = line or 0,
    timestamp = timestamp or time(),
    fixed = false,
    retry_count = 0,
    session_counter = (self.session_counter or 0) + 1,
  }
  setmetatable(instance, self)
  return instance
end

-- Error type priorities (used for triage, not for filtering).
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

local function generateFingerprint(incident)
  return incident.message .. ":" .. incident.filename .. ":" .. tostring(incident.line)
end

local ErrorBus = {}
local incidentIdCounter = 0
local capturedIncidents = {}

function ErrorBus:GetPriority(errorMessage)
  local text = tostring(errorMessage or ""):lower()
  for priority in pairs(ERROR_PRIORITIES) do
    if text:find(priority:lower(), 1, true) then
      return priority
    end
  end
  return "UNKNOWN"
end

function ErrorBus:CaptureError(message, stack, filename, line)
  local incident = ErrorIncident:new(message, stack, filename, line, time())
  local fingerprint = generateFingerprint(incident)

  if capturedIncidents[fingerprint] then
    if capturedIncidents[fingerprint].fixed then
      return false
    end
    incident.retry_count = (incident.retry_count or 0) + 1
  else
    capturedIncidents[fingerprint] = incident
  end

  incidentIdCounter = incidentIdCounter + 1
  self:TriggerRepair(incident)
  return true
end

function ErrorBus:TriggerRepair(incident)
  -- Print the incident summary. The developer never has to paste a stack:
  -- the same data is persisted below for the offline tooling.
  print("|cffFF0000Casinobabe ERROR[" .. tostring(incident.id) .. "]|r: " .. tostring(incident.message))
  print("  File: " .. tostring(incident.filename) .. ":" .. tostring(incident.line))

  if type(CasinobabeDB) == "table" then
    CasinobabeDB.lastError = {
      id = tostring(incident.id),
      message = tostring(incident.message or ""):sub(1, 500),
      file = tostring(incident.filename or "unknown"):sub(1, 200),
      line = tonumber(incident.line) or 0,
      stack = tostring(incident.stack or ""):sub(1, 1000),
      at = tonumber(incident.timestamp) or time(),
      priority = self:GetPriority(incident.message),
    }
    CasinobabeDB.lastErrorCount = (CasinobabeDB.lastErrorCount or 0) + 1
    CasinobabeDB.lastErrorIncident = tostring(incident.id)
    CasinobabeDB.errorIncidentCount = incidentIdCounter
  end
end

-- ---------------------------------------------------------------------------
-- Single error handler: both channels + chain to the previous handler.
--
-- "Interface\AddOns\Casinobabe\Casinobabe.lua:6510: attempt to call a nil value"
-- "[string \"...\"]:12: unexpected symbol near 'end'"
-- ---------------------------------------------------------------------------
local function parseErrorLocation(message)
  local text = tostring(message or "UNKNOWN_ERROR")
  local filename, line = text:match("^(.-):(%d+):")
  if not filename then return "unknown", 0, text end
  local body = text:match(":%d+:%s*(.*)$") or text
  return filename, tonumber(line) or 0, body
end

local previousHandler = nil
local handlerInstalled = false

local function dispatchError(message)
  local filename, line, body = parseErrorLocation(message)
  pcall(BusAppend, body, (debugstack and debugstack(2, 50, 50)) or "")
  pcall(function()
    ErrorBus:CaptureError(body, (debugstack and debugstack(2, 0, 0)) or "", filename, line)
  end)
  -- Forward to the previous handler (default WoW error UI) - never swallow.
  if previousHandler and previousHandler ~= dispatchError then
    pcall(previousHandler, message)
  end
end

function ErrorBus:InstallHandler()
  if handlerInstalled then return false end
  handlerInstalled = true
  if type(geterrorhandler) == "function" then
    pcall(function() previousHandler = geterrorhandler() end)
  end
  if type(seterrorhandler) == "function" then
    pcall(seterrorhandler, dispatchError)
  end
  self.handlerInstalled = true
  return true
end

-- Event wiring: SavedVariables exist only after ADDON_LOADED, and the handler
-- must also be active for load-time errors, so it is installed twice (idempotent).
local errorCaptureFrame = CreateFrame("Frame", "CasinobabeErrorCaptureFrame")
errorCaptureFrame:RegisterEvent("ADDON_LOADED")
errorCaptureFrame:RegisterEvent("PLAYER_LOGIN")
errorCaptureFrame:RegisterEvent("PLAYER_LOGOUT")

errorCaptureFrame:SetScript("OnEvent", function(self, event, ...)
  if event == "ADDON_LOADED" and select(1, ...) == "Casinobabe" then
    ErrorBus:InstallHandler()
    print("|cffFFD700Casinobabe|r Error capture system initialized.")
  elseif event == "PLAYER_LOGOUT" then
    -- Nothing to flush: every incident is already in CasinobabeDB.
  end
end)

ErrorBus:InstallHandler()

print("|cffFFD700Casinobabe|r LiveErrorCapture.lua loaded - error monitoring active.")
