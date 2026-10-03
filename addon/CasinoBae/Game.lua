CasinoBae.Game=CasinoBae.Game or {}
function CasinoBae.Game:StartRand()
 if CasinoBae.STATE~="LOBBY_OPEN" and CasinoBae.STATE~="ROUND_COMPLETE" and CasinoBae.STATE~="TIE" then return false,"Lobby not open" end
 local count=0 for _ in pairs(CasinoBae.lobby.players) do count=count+1 end
 if count<2 then return false,"At least 2 players required" end
 CasinoBae.lobby.game="RAND"; CasinoBae.lobby.round=CasinoBae.lobby.round+1; CasinoBae.rolls={}
 CasinoBae:SetState("GAME_WAITING_FOR_ROLLS",{round=CasinoBae.lobby.round})
 CasinoBae:Emit("GAME_STARTED",{game="RAND",round=CasinoBae.lobby.round,players=count})
 CasinoBae:Announce("ROUND "..CasinoBae.lobby.round.." — chaque joueur fait /rand maintenant.")
 CasinoBae:Emote("ROAR")
 return true
end
function CasinoBae.Game:RecordRoll(player,value)
 if CasinoBae.STATE~="GAME_WAITING_FOR_ROLLS" or not CasinoBae.lobby.players[player] then return false end
 if type(value)~="number" or value<1 or value>100 then return false end
 if CasinoBae.rolls[player]~=nil then return false end
 CasinoBae.rolls[player]=value
 CasinoBae:Emit("ROLL_CONFIRMED",{player=player,value=value})
 local total,got=0,0
 for _ in pairs(CasinoBae.lobby.players) do total=total+1 end
 for _ in pairs(CasinoBae.rolls) do got=got+1 end
 if got>=total then self:FinishRound() end
 return true
end
function CasinoBae.Game:FinishRound()
 local winner,best,tie=nil,nil,false
 for player,value in pairs(CasinoBae.rolls or {}) do
  if not best or value>best then winner,best,tie=player,value,false
  elseif value==best then tie=true end
 end
 if not winner then return end
 if tie then
  CasinoBae:SetState("TIE",{value=best})
  CasinoBae:Emit("ROUND_TIE",{value=best,round=CasinoBae.lobby.round})
  CasinoBae:Announce("ÉGALITÉ à "..best.." — relancez /cb start.")
  CasinoBae:Emote("SHRUG")
  return
 end
 CasinoBae:SetState("ROUND_COMPLETE",{winner=winner,value=best})
 CasinoBae:Emit("ROUND_COMPLETE",{winner=winner,value=best,round=CasinoBae.lobby.round})
 CasinoBae:Announce("WINNER — "..winner.." avec "..best..".")
 CasinoBae:Emote("CHEER")
end
function CasinoBae.Game:ParseRollMessage(message)
 if type(message)~="string" then return false end
 local patterns={
  "^(.+) rolls (%d+) %(",
  "^(.+) lance (%d+) %(",
  "^(.+) rolls (%d+) %(%d+%-%d+%)$",
  "^(.+) lance un dé à (%d+) %(",
  "^(.+) obtient (%d+) %(1%-100%)"
 }
 for _,pattern in ipairs(patterns) do
  local p,v=message:match(pattern)
  if p and v then return self:RecordRoll(p:gsub("%s+$",""),tonumber(v)) end
 end
 return false
end