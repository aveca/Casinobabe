CasinoBae.Game=CasinoBae.Game or {}

function CasinoBae.Game:StartRand()
 if CasinoBae.STATE~="LOBBY_OPEN" then return false,"Lobby not open" end
 local count=0 for _ in pairs(CasinoBae.lobby.players) do count=count+1 end
 if count<2 then return false,"At least 2 players required" end
 CasinoBae.lobby.game="RAND"; CasinoBae.lobby.round=CasinoBae.lobby.round+1; CasinoBae.rolls={}
 CasinoBae:SetState("GAME_WAITING_FOR_ROLLS",{round=CasinoBae.lobby.round})
 CasinoBae:Emit("GAME_STARTED",{game="RAND",round=CasinoBae.lobby.round})
 return true
end

function CasinoBae.Game:RecordRoll(player,value)
 if CasinoBae.STATE~="GAME_WAITING_FOR_ROLLS" or not CasinoBae.lobby.players[player] then return false end
 if type(value)~="number" or value<1 or value>100 then return false end
 CasinoBae.rolls=CasinoBae.rolls or {}
 CasinoBae.rolls[player]=value
 CasinoBae:Emit("ROLL_CONFIRMED",{player=player,value=value})
 local total=0 for _ in pairs(CasinoBae.lobby.players) do total=total+1 end
 local got=0 for _ in pairs(CasinoBae.rolls) do got=got+1 end
 if got>=total then self:FinishRound() end
 return true
end

function CasinoBae.Game:FinishRound()
 local winner,best
 for player,value in pairs(CasinoBae.rolls or {}) do if not best or value>best then winner,best=player,value end end
 if not winner then return end
 CasinoBae:SetState("ROUND_COMPLETE",{winner=winner,value=best})
 CasinoBae:Emit("ROUND_COMPLETE",{winner=winner,value=best,round=CasinoBae.lobby.round})
end

function CasinoBae.Game:ParseRollMessage(message)
 if type(message)~="string" then return false end
 local player,value,max=message:match("^(.+) rolls (%d+) %((%d+)-(%d+)%)$")
 if player and value then return self:RecordRoll(player,tonumber(value)) end
 local p,v=message:match("^(.+) rolls (%d+) %(")
 if p and v then return self:RecordRoll(p,tonumber(v)) end
 return false
end
