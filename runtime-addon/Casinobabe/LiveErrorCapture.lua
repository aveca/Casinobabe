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
