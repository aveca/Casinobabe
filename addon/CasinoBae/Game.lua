CasinoBae.Game=CasinoBae.Game or {}

function CasinoBae.Game:StartRand()
 if CasinoBae.STATE~="LOBBY_OPEN" then return false,"Lobby not open" end
 local count=0
 for _ in pairs(CasinoBae.lobby.players) do count=count+1 end
 if count<2 then return false,"At least 2 players required" end
 CasinoBae.lobby.game="RAND"
 CasinoBae.lobby.round=CasinoBae.lobby.round+1
 CasinoBae:SetState("GAME_WAITING_FOR_ROLLS",{round=CasinoBae.lobby.round})
 CasinoBae:Emit("GAME_STARTED",{game="RAND",round=CasinoBae.lobby.round})
 return true
end

function CasinoBae.Game:RecordRoll(player,value)
 if CasinoBae.STATE~="GAME_WAITING_FOR_ROLLS" then return false end
 if type(value)~="number" or value<1 or value>100 then return false end
 CasinoBae.rolls=CasinoBae.rolls or {}
 CasinoBae.rolls[player]=value
 CasinoBae:Emit("ROLL_CONFIRMED",{player=player,value=value})
 return true
end
