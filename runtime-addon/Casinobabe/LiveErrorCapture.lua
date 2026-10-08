-- CasinoBae Live Error Capture
-- WoW addons cannot write arbitrary files or open sockets. This captures only
-- Casinobabe errors into SavedVariables so an external Windows dev loop can
-- consume them without screenshots or copy/paste.

CasinobabeErrorBus = CasinobabeErrorBus or {
  version = 1,
  seq = 0,
  pending = {},
  last = nil,
}

local BUS = CasinobabeErrorBus

local function ExtractSourceLine(stack)
  if not stack then return "unknown", 0 end
  -- Parse WoW debugstack format: "stdstring.format [%string] Load:0x..." or "CASINOBABE_GETLINE"
  -- Look for patterns like "Interface\\AddOns\\Casinobabe\\Casinobabe.lua:123" or "Casinobabe.lua:45"
  local source, line = "unknown", 0
  -- Pattern: filename:linenumber
  for s in string.gmatch(stack, "[^\n]*") do
    local fn, lin = s:match("(.+:)%s*(%d+)")
    if fn and lin then
      -- Prioritize Casinobabe files
      if fn:find("Casinobabe") then
        source = fn
        line = tonumber(lin)
        break
      end
    end
  end
  -- Fallback: if no Casinobabe file found, return first stack entry with line
  if source == "unknown" then
    local fn, lin = stack:match("Interface\\AddOns\\([^:]+):(%d+)")
    if fn then source = "Interface\\AddOns\\" .. fn end
    local lin2 = stack:match(":(%d+)$")
    if lin2 then line = tonumber(lin2) end
  end
  return source, line
end

local function Capture(message, testId)
  if type(message) ~= "string" then return end
  if not string.find(message, "Casinobabe", 1, true) then return end

  BUS.seq = (BUS.seq or 0) + 1
  local stack = debugstack and debugstack(2, 50, 50) or ""
  local source, line = ExtractSourceLine(stack)

  local record = {
    id = tostring(time()) .. "-" .. tostring(BUS.seq),
    timestamp = time(),
    message = message,
    source = source,
    line = line,
    stack = stack,
    session = testId or "unknown",
  }

  BUS.last = record
  BUS.pending = BUS.pending or {}
  table.insert(BUS.pending, record)
  while #BUS.pending > 25 do
    table.remove(BUS.pending, 1)
  end
end

local previousErrorHandler = geterrorhandler and geterrorhandler()
if seterrorhandler and previousErrorHandler then
  seterrorhandler(function(message)
    Capture(message, "unknown")
    return previousErrorHandler(message)
  end)
end

-- Export for external access
function CasinobabeErrorBus.GetPending() return BUS.pending end
function CasinobabeErrorBus.Clear() BUS.pending = {} BUS.last = nil BUS.seq = 0 end