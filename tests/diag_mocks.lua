C_Map = {
  GetBestMapForUnit = function(u) return 1446 end,
  GetPlayerMapPosition = function(map, u)
    if map == 1446 then return { x = 0.50, y = 0.50 } end
    return nil
  end,
}
C_DeathInfo = {
  GetCorpseMapPosition = function(map)
    if map == 1446 then return { x = 0.25, y = 0.75 } end
    return nil
  end,
}
UnitIsGhost = function(u) return true end
UnitIsPlayer = function(u) return true end
UnitPosition = function(u) return 100.0, 200.0, 30.0 end
C_Spell = { GetSpellInfo = function(id) return { name = "Retrieve Corpse" } end }
_G["GetCorpseMapPosition"] = nil
_G["RetrieveCorpse"] = nil
_G["CastSpellByName"] = nil
time = function() return 1700000000 end
SlashCmdList = {}
local created = {}
CreateFrame = function(kind, name, parent)
  local f = {}
  function f:RegisterEvent(e) table.insert(created, e) end
  function f:SetScript(h, fn) self[h] = fn end
  function f:Show() end
  function f:Hide() end
  return f
end
UIParent = { CreateTexture = function() return nil end }
C_Timer = {
  After = function(d, fn) return { Cancel = function() end } end,
  NewTicker = function(d, fn) return { Cancel = function() end } end,
}
ChatFrame1 = { AddMessage = function() end }
DEFAULT_CHAT_FRAME = ChatFrame1
-- WoW ships Lua 5.1: math.atan2 exists there, not in the Lua 5.5 used by lupa.
math.atan2 = math.atan2 or function(y, x) return math.atan2_lua(y, x) end
math.atan2_lua = function(y, x) return math.atan(y / x) end
math.degrees = math.degrees or function(r) return r * 180 / math.pi end
