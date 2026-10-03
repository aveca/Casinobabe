SLASH_CASINOBAE1="/cb"
SlashCmdList.CASINOBAE=function(message)
 local command,args=(message or ""):match("^%s*(%S*)%s*(.*)$"); command=string.lower(command or "")
 if command=="status" then print("|cffffcc00CasinoBae|r state:",CasinoBae.STATE); return end
 if command=="lobby" then CasinoBae:CreateLobby(); print("|cffffcc00CasinoBae|r lobby opened."); return end
 if command=="add" and args~="" then CasinoBae:AddPlayer(args); print("|cffffcc00CasinoBae|r player added:",args); return end
 if command=="start" then local ok,err=CasinoBae.Game:StartRand(); print("|cffffcc00CasinoBae|r",ok and "RAND started" or ("Cannot start: "..err)); return end
 if command=="action" and args~="" then CasinoBae:RequireAction(args,"MANUAL"); print("|cffffaa00CasinoBae ACTION_REQUIRED|r:",args); return end
 print("|cffffcc00CasinoBae|r commands: /cb status | /cb lobby | /cb add NAME | /cb start | /cb action MESSAGE")
end
