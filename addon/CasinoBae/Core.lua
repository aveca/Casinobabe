CasinoBae=CasinoBae or {}
CasinoBae.VERSION="0.5.0"
CasinoBae.STATE="IDLE"
CasinoBae.players={}
CasinoBae.lobby={host=nil,players={},game=nil,round=0}
CasinoBae.rolls={}
CasinoBae.pendingAction=nil
CasinoBaeDB=CasinoBaeDB or {queue={}}
CasinoBaeDB.queue=CasinoBaeDB.queue or {}

local function CasinoBaeEscapeBridgeValue(value)
 local s=tostring(value or "")
 return s:gsub("\\","\\\\"):gsub("\t","\\t"):gsub("\n","\\n"):gsub(";","\\;"):gsub("=","\\=")
end

function CasinoBae:QueueBridgeEvent(name,payload)
 local fields={}
 for k,v in pairs(payload or {}) do
  if type(v)=="string" or type(v)=="number" or type(v)=="boolean" then
   fields[#fields+1]=CasinoBaeEscapeBridgeValue(k).."="..CasinoBaeEscapeBridgeValue(v)
  end
 end
 table.sort(fields)
 local q=CasinoBaeDB.queue
 q[#q+1]={name=name,state=self.STATE,time=date("%H:%M:%S"),payload_line=table.concat(fields,";")}
 if #q>200 then table.remove(q,1) end
end

function CasinoBae:SetState(state,detail)
 self.STATE=state
 self.stateDetail=detail
 self:Emit("STATE_CHANGED",{state=state,detail=detail})
end

function CasinoBae:Emit(name,payload)
 self.lastEvent={name=name,payload=payload,state=self.STATE,time=date("%H:%M:%S")}
 if self.OnEvent then self:OnEvent(self.lastEvent) end
 self:QueueBridgeEvent(name,payload)
 if self.UI and self.UI.Refresh then self.UI:Refresh() end
end

function CasinoBae:Announce(message)
 if not message or message=="" then return end
 local text="CasinoBae: "..message
 if RaidNotice_AddMessage and RaidWarningFrame then RaidNotice_AddMessage(RaidWarningFrame,text,ChatTypeInfo.RAID_WARNING) end
 print("|cffff4f9aCasinoBae|r "..message)
end

function CasinoBae:Say(message)
 if type(message)=="string" and message~="" then SendChatMessage(message,"SAY") end
end

function CasinoBae:Yell(message)
 if type(message)=="string" and message~="" then
  SendChatMessage(message,"YELL")
  self:Emit("YELL_SENT",{message=message})
 end
end

function CasinoBae:Emote(token)
 if type(token)~="string" or token=="" then return false end
 if C_ChatInfo and C_ChatInfo.PerformEmote then
  local ok=C_ChatInfo.PerformEmote(token)
  if ok~=false then self:Emit("EMOTE_PERFORMED",{token=token}); return true end
 end
 if DoEmote then DoEmote(token); self:Emit("EMOTE_PERFORMED",{token=token}); return true end
 return false
end

function CasinoBae:AddPlayer(name)
 if type(name)~="string" or name=="" or #name>64 then return false end
 if self.lobby.players[name] then return true end
 self.players[name]=true
 self.lobby.players[name]=true
 self:Emit("PLAYER_JOINED",{name=name})
 return true
end

function CasinoBae:RemovePlayer(name)
 if not self.lobby.players[name] then return false end
 self.lobby.players[name]=nil
 self:Emit("PLAYER_LEFT",{name=name})
 return true
end

function CasinoBae:CreateLobby()
 local host=UnitName("player")
 self.lobby={host=host,players={},game=nil,round=0}
 self.rolls={}
 self.pendingAction=nil
 self:AddPlayer(host)
 self:SetState("LOBBY_OPEN",{host=host})
 self:Emit("LOBBY_CREATED",{host=host})
 self:Announce("LOBBY OPEN — whisper « join » pour rejoindre.")
 self:Emote("POINT")
end

function CasinoBae:RequireAction(message,action,extra)
 self.pendingAction={message=message,action=action,extra=extra}
 self:SetState("ACTION_REQUIRED",self.pendingAction)
 self:Emit("ACTION_REQUIRED",self.pendingAction)
 self:Announce("ACTION REQUISE: "..tostring(message))
end

function CasinoBae:ConfirmAction(eventName,payload)
 if self.STATE~="ACTION_REQUIRED" or not self.pendingAction then return false end
 local a=self.pendingAction.action
 self:SetState("READY",{confirmedBy=eventName,action=a})
 self:Emit("ACTION_CONFIRMED",{event=eventName,payload=payload,action=a})
 self.pendingAction=nil
 return true
end
