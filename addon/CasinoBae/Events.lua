local frame=CreateFrame("Frame")
for _,event in ipairs({"PLAYER_LOGIN","PLAYER_ENTERING_WORLD","CHAT_MSG_WHISPER","CHAT_MSG_WHISPER_INFORM","CHAT_MSG_SYSTEM","CHAT_MSG_EMOTE","CHAT_MSG_TEXT_EMOTE","GROUP_ROSTER_UPDATE","PLAYER_LEAVING_WORLD"}) do frame:RegisterEvent(event) end
frame:SetScript("OnEvent",function(_,event,...)
 if event=="PLAYER_LOGIN" then
  CasinoBae:SetState("READY")
  CasinoBae:Emit("PLAYER_LOGIN")
 elseif event=="PLAYER_ENTERING_WORLD" then
  if CasinoBae.pendingAction and CasinoBae.pendingAction.action=="ZONE_CHANGE" then CasinoBae:ConfirmAction(event,{...}) end
  CasinoBae:Emit(event,{...})
 elseif event=="PLAYER_LEAVING_WORLD" then
  CasinoBae:Emit(event,{...})
 elseif event=="CHAT_MSG_WHISPER" then
  local message,sender=...
  local normalized=string.lower((message or ""):match("^%s*(.-)%s*$") or "")
  CasinoBae:Emit("WHISPER_RECEIVED",{message=message,sender=sender})
  if CasinoBae.STATE=="LOBBY_OPEN" and (normalized=="join" or normalized=="cb" or normalized=="casino" or normalized=="play") then
   if CasinoBae:AddPlayer(sender) then
    CasinoBae:Announce(sender.." rejoint le lobby.")
    SendChatMessage("CasinoBae: tu es dans le lobby. Attends le lancement.","WHISPER",nil,sender)
   end
  elseif normalized=="status" then
   SendChatMessage("CasinoBae: état "..tostring(CasinoBae.STATE),"WHISPER",nil,sender)
  elseif normalized=="rules" then
   SendChatMessage("CasinoBae: /rand 1-100, plus haut gagne, égalité = relance.","WHISPER",nil,sender)
  end
 elseif event=="CHAT_MSG_WHISPER_INFORM" then
  local message,recipient=...
  CasinoBae:Emit("WHISPER_SENT",{message=message,recipient=recipient})
 elseif event=="CHAT_MSG_SYSTEM" then
  local message=...
  if CasinoBae.Game then CasinoBae.Game:ParseRollMessage(message) end
  CasinoBae:Emit("SYSTEM_MESSAGE",{message=message})
 elseif event=="CHAT_MSG_EMOTE" then
  local message,sender=...
  CasinoBae:Emit("EMOTE_RECEIVED",{message=message,sender=sender})
 elseif event=="CHAT_MSG_TEXT_EMOTE" then
  local message,sender=...
  CasinoBae:Emit("TEXT_EMOTE_RECEIVED",{message=message,sender=sender})
 elseif event=="GROUP_ROSTER_UPDATE" then
  if CasinoBae.pendingAction and CasinoBae.pendingAction.action=="INVITE_PLAYER" then
   local target=CasinoBae.pendingAction.extra
   if target and UnitInParty(target) then
    CasinoBae:ConfirmAction(event,{player=target,verified=true})
   else
    CasinoBae:Emit("INVITE_WAITING",{player=target,verified=false})
   end
  end
  CasinoBae:Emit(event)
 end
end)
