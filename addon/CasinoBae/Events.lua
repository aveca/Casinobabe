local frame=CreateFrame("Frame")
for _,event in ipairs({"PLAYER_LOGIN","PLAYER_ENTERING_WORLD","CHAT_MSG_WHISPER","CHAT_MSG_WHISPER_INFORM","CHAT_MSG_SYSTEM"}) do frame:RegisterEvent(event) end
frame:SetScript("OnEvent",function(_,event,...)
 if event=="PLAYER_LOGIN" then CasinoBae:SetState("READY"); CasinoBae:Emit("PLAYER_LOGIN")
 elseif event=="PLAYER_ENTERING_WORLD" then CasinoBae:Emit(event,{...})
 elseif event=="CHAT_MSG_WHISPER" then local message,sender=...; CasinoBae:AddPlayer(sender); CasinoBae:Emit("WHISPER_RECEIVED",{message=message,sender=sender})
 elseif event=="CHAT_MSG_WHISPER_INFORM" then local message,recipient=...; CasinoBae:Emit("WHISPER_SENT",{message=message,recipient=recipient})
 elseif event=="CHAT_MSG_SYSTEM" then local message=...; if CasinoBae.Game then CasinoBae.Game:ParseRollMessage(message) end; CasinoBae:Emit("SYSTEM_MESSAGE",{message=message}) end
end)
