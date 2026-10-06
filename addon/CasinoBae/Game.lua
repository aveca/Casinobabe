CasinoBae.Game=CasinoBae.Game or {}
local function normalizeName(name) name=(name or ""):gsub("|c%x%x%x%x%x%x%x%x",""):gsub("|r",""):gsub("^%s+",""):gsub("%s+$",""); return name end
function CasinoBae.Game:StartRand()
 if CasinoBae.STATE~="LOBBY_OPEN" and CasinoBae.STATE~="ROUND_COMPLETE" and CasinoBae.STATE~="TIE" then return false,"Lobby not open" end
 local count=0; for _ in pairs(CasinoBae.lobby.players) do count=count+1 end; if count<2 then return false,"At least 2 players required" end
  CasinoBae.lobby.game="RAND"; CasinoBae.lobby.round=CasinoBae.lobby.round+1; CasinoBae.rolls={}; CasinoBae:SetState("GAME_WAITING_FOR_ROLLS",{round=CasinoBae.lobby.round})
  CasinoBae:Emit("GAME_STARTED",{game="RAND",round=CasinoBae.lobby.round,players=count});
  if CasinoBae.Style and CasinoBae.Style.enabled then CasinoBae.Style.Play(CasinoBae.Style.DiceInvite(CasinoBae.lobby.round))
  else
    CasinoBae:Announce("ROUND "..CasinoBae.lobby.round.." — chaque joueur fait /rand maintenant.")
    CasinoBae:Emote("ROAR")
  end
  return true
end
function CasinoBae.Game:RecordRoll(player,value)
 player=normalizeName(player); if CasinoBae.STATE~="GAME_WAITING_FOR_ROLLS" or not CasinoBae.lobby.players[player] then return false end
 if type(value)~="number" or value<1 or value>100 or CasinoBae.rolls[player]~=nil then return false end
 CasinoBae.rolls[player]=value; CasinoBae:Emit("ROLL_CONFIRMED",{player=player,value=value}); local total,got=0,0; for _ in pairs(CasinoBae.lobby.players) do total=total+1 end; for _ in pairs(CasinoBae.rolls) do got=got+1 end; if got>=total then self:FinishRound() end; return true
end
function CasinoBae.Game:FinishRound()
 local winner,best,tie=nil,nil,false; for player,value in pairs(CasinoBae.rolls or {}) do if not best or value>best then winner,best,tie=player,value,false elseif value==best then tie=true end end; if not winner then return end
  if tie then CasinoBae:SetState("TIE",{value=best}); CasinoBae:Emit("ROUND_TIE",{value=best,round=CasinoBae.lobby.round});
  if CasinoBae.Style and CasinoBae.Style.enabled then CasinoBae.Style.Play(CasinoBae.Style.Tie(best))
  else CasinoBae:Announce("ÉGALITÉ à "..best.." — relancez /cb start."); CasinoBae:Emote("SHRUG") end
  return end
  CasinoBae:SetState("ROUND_COMPLETE",{winner=winner,value=best}); CasinoBae:Emit("ROUND_COMPLETE",{winner=winner,value=best,round=CasinoBae.lobby.round});
  if CasinoBae.Style and CasinoBae.Style.enabled then CasinoBae.Style.Play(CasinoBae.Style.Win(winner,best))
  else CasinoBae:Announce("WINNER — "..winner.." avec "..best.."."); CasinoBae:Emote("CHEER") end
end
function CasinoBae.Game:ParseRollMessage(message)
 if type(message)~="string" then return false end; local m=normalizeName(message); local value
 value=m:match("^You roll%s+(%d+)%s+%(%s*1%s*%-%s*100%s*%)") or m:match("^Vous obtenez%s+(%d+)%s+%(%s*1%s*%-%s*100%s*%)") or m:match("^Vous lancez%s+(%d+)%s+%(%s*1%s*%-%s*100%s*%)")
 if value then local me=UnitName("player"); if me then return self:RecordRoll(me,tonumber(value)) end end
 local patterns={"^(.+) rolls%s+(%d+)%s+%(%s*1%s*%-%s*100%s*%)","^(.+) lance%s+(%d+)%s+%(%s*1%s*%-%s*100%s*%)","^(.+) obtient%s+(%d+)%s+%(%s*1%s*%-%s*100%s*%)","^(.+) lance un dé à%s+(%d+)%s+%(","^(.+) rolls%s+(%d+)%s+%("}
 for _,pattern in ipairs(patterns) do local p,v=m:match(pattern); if p and v then return self:RecordRoll(normalizeName(p),tonumber(v)) end end; return false
end