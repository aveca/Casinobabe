-- CasinoBae live error bridge
-- Captures addon Lua errors into SavedVariables for an external Windows sentinel.
-- WoW cannot write arbitrary files or open sockets; persistence is therefore the
-- supported SavedVariables boundary. /reload or logout flushes this data to disk.

CasinobabeErrorBus = CasinobabeErrorBus or {
  version = 1,
  seq = 0,
  last = nil,
  pending = {},
}

local BUS = CasinobabeErrorBus
local addonPrefix = "Casinobabe"

local function PushError(message)
  if type(message) ~= "string" then return end
  if not string.find(message, addonPrefix, 1, true) then return end

  BUS.seq = (BUS.seq or 0) + 1
  local record = {
    id = tostring(time()) .. "-" .. tostring(BUS.seq),
    time = time(),
    message = message,
    stack = (debugstack and debugstack(2, 40, 40)) or "",
  }

  BUS.last = record
  BUS.pending = BUS.pending or {}
  table.insert(BUS.pending, record)
  while #BUS.pending > 20 do table.remove(BUS.pending, 1) end

  print("|cffff4040Casinobabe LIVE ERROR CAPTURED|r " .. message)
end

local previousHandler = geterrorhandler and geterrorhandler()
if seterrorhandler and previousHandler then
  seterrorhandler(function(message)
    PushError(message)
    return previousHandler(message)
  end)
end
