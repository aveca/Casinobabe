CasinoBae=CasinoBae or {}
CasinoBae.VERSION="0.2.0"
CasinoBae.STATE="IDLE"
CasinoBae.players={}
CasinoBae.lobby={host=nil,players={},game=nil,round=0}

function CasinoBae:SetState(state,detail)
 self.STATE=state
 self.stateDetail=detail
 self:Emit("STATE_CHANGED",{state=state,detail=detail})
end

function CasinoBae:Emit(name,payload)
 self.lastEvent={name=name,payload=payload,state=self.STATE}
end

function CasinoBae:AddPlayer(name)
 if not name or name=="" then return false end
 self.players[name]=true
 self.lobby.players[name]=true
 self:Emit("PLAYER_JOINED",{name=name})
 return true
end

function CasinoBae:CreateLobby()
 self.lobby.host=UnitName("player")
 self.lobby.players={}
 self.lobby.players[self.lobby.host]=true
 self.lobby.game=nil
 self.lobby.round=0
 self:SetState("LOBBY_OPEN")
 self:Emit("LOBBY_CREATED",{host=self.lobby.host})
end

function CasinoBae:RequireAction(message,action)
 self:SetState("ACTION_REQUIRED",{message=message,action=action})
 self:Emit("ACTION_REQUIRED",{message=message,action=action})
end

function CasinoBae:ConfirmAction(eventName,payload)
 if self.STATE=="ACTION_REQUIRED" then
  self:SetState("READY",{confirmedBy=eventName})
  self:Emit("ACTION_CONFIRMED",{event=eventName,payload=payload})
 end
end
