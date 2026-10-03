-- CasinoBae offline integration harness. Mocks the WoW APIs used by Core/Game/Events/Commands.
local events = {}
local function reset()
  CasinoBae = nil
  CasinoBaeDB = nil
  SlashCmdList = {}
  events = {}
end
function date() return "12:00:00" end
local player = "Host"
function UnitName(unit) if unit=="player" then return player end end
local party = {}
function UnitInParty(name) return party[name] == true end
function SendChatMessage(msg, kind, _, target)
  events[#events+1] = {kind=kind,msg=msg,target=target}
end
function print(...) end
function RaidNotice_AddMessage() end
RaidWarningFrame = {}
ChatTypeInfo = {RAID_WARNING={}}
C_ChatInfo = {PerformEmote=function() return true end}
function DoEmote() end
UIParent = {}
local frameMock = {}
function CreateFrame()
  local f = {}
  function f:RegisterEvent(e) f.events=f.events or {}; f.events[e]=true end
  function f:SetScript(_,fn) f.handler=fn end
  return f
end

local function loadBase()
  dofile("addon/CasinoBae/Core.lua")
  dofile("addon/CasinoBae/Game.lua")
end

local function event(name,...)
  assert(frameMock.handler, "event handler missing")
  frameMock.handler(nil,name,...)
end

-- Events.lua gets the last created frame; capture it.
local realCreateFrame = CreateFrame
CreateFrame = function(...)
  frameMock = realCreateFrame(...)
  return frameMock
end

reset()
loadBase()
dofile("addon/CasinoBae/Events.lua")
event("PLAYER_LOGIN")
assert(CasinoBae.STATE=="READY")
event("CHAT_MSG_WHISPER","join","Alice")
-- Event arguments are positional in WoW; this mock matches the addon extraction.
assert(CasinoBae.lobby.players["Alice"]==nil, "join should require an open lobby")
CasinoBae:CreateLobby()
event("CHAT_MSG_WHISPER","join","Alice")
assert(CasinoBae.lobby.players["Alice"]==true)
event("CHAT_MSG_WHISPER","rules","Alice")
assert(#events>0 and events[#events].kind=="WHISPER")

-- Add a second real player and start.
CasinoBae:AddPlayer("Bob")
assert(CasinoBae.Game:StartRand())
assert(CasinoBae.STATE=="GAME_WAITING_FOR_ROLLS")

-- Realistic system messages.
event("CHAT_MSG_SYSTEM","Alice rolls 42 (1-100).")
assert(CasinoBae.rolls["Alice"]==42)
event("CHAT_MSG_SYSTEM","Bob rolls 87 (1-100).")
assert(CasinoBae.rolls["Bob"]==87)
assert(CasinoBae.STATE=="ROUND_COMPLETE")
assert(CasinoBae.stateDetail.winner=="Bob")
assert(CasinoBae.stateDetail.value==87)

-- Duplicate rolls are ignored.
assert(CasinoBae.Game:RecordRoll("Bob",99)==false)

-- Tie path.
CasinoBae:SetState("LOBBY_OPEN")
assert(CasinoBae.Game:StartRand())
event("CHAT_MSG_SYSTEM","Alice rolls 55 (1-100).")
event("CHAT_MSG_SYSTEM","Bob rolls 55 (1-100).")
assert(CasinoBae.STATE=="TIE")
assert(CasinoBae.stateDetail.value==55)

-- Self-roll localization paths.
CasinoBae:SetState("LOBBY_OPEN")
CasinoBae:AddPlayer("Host")
assert(CasinoBae.Game:StartRand())
event("CHAT_MSG_SYSTEM","You roll 91 (1-100).")
assert(CasinoBae.rolls["Host"]==91)

-- Invalid/out-of-range/outsider rolls are rejected.
assert(CasinoBae.Game:RecordRoll("Nobody",50)==false)
assert(CasinoBae.Game:RecordRoll("Alice",101)==false)

-- ACTION_REQUIRED must not fake an invite; only a real roster update confirms it.
CasinoBae:RequireAction("Invite Alice","INVITE_PLAYER","Alice")
event("GROUP_ROSTER_UPDATE")
assert(CasinoBae.STATE=="ACTION_REQUIRED")
party.Alice=true
event("GROUP_ROSTER_UPDATE")
assert(CasinoBae.STATE=="READY")

-- Command parser smoke test.
CasinoBae.UI={Show=function() end,Hide=function() end,PrintStatus=function() end}
dofile("addon/CasinoBae/Commands.lua")
SlashCmdList.CASINOBAE("status")
SlashCmdList.CASINOBAE("rules")
SlashCmdList.CASINOBAE("say hello")
SlashCmdList.CASINOBAE("yell hello")
SlashCmdList.CASINOBAE("emote CHEER")

print("CASINOBAE OFFLINE INTEGRATION TESTS: PASS")
