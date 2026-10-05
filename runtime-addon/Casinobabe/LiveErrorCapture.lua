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

local function Capture(message)
  if type(message) ~= "string" then return end
  if not string.find(message, "Casinobabe", 1, true) then return end

  BUS.seq = (BUS.seq or 0) + 1
  local record = {
    id = tostring(time()) .. "-" .. tostring(BUS.seq),
    time = time(),
    message = message,
    stack = (debugstack and debugstack(2, 50, 50)) or "",
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
    Capture(message)
    return previousErrorHandler(message)
  end)
end
