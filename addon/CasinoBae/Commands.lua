SLASH_CASINOBAE1="/cb"
SlashCmdList.CASINOBAE=function(message)
 local command,args=(message or ""):match("^%s*(%S*)%s*(.*)$"); command=string.lower(command or "")
 if command=="" or command=="ui" then CasinoBae.UI:Show(); return end
 if command=="hide" then CasinoBae.UI:Hide(); return end
 if command=="status" then CasinoBae.UI:PrintStatus(); return end
 if command=="lobby" then CasinoBae:CreateLobby(); CasinoBae.UI:Refresh(); return end
 if command=="join" and args~="" then CasinoBae:AddPlayer(args); CasinoBae:Announce(args.." a rejoint le lobby."); CasinoBae.UI:Refresh(); return end
 if command=="remove" and args~="" then CasinoBae:RemovePlayer(args); CasinoBae.UI:Refresh(); return end
 if command=="start" then local ok,err=CasinoBae.Game:StartRand(); if not ok then CasinoBae:Announce("Impossible de lancer: "..err) end; CasinoBae.UI:Refresh(); return end
 if command=="rules" then CasinoBae:Announce("RAND 1-100 — chacun lance /rand — plus haut gagne — égalité = relance."); return end
 if command=="invite" and args~="" then CasinoBae:RequireAction("Invite manuellement "..args.." dans WoW. CasinoBae attend ensuite la mise à jour du groupe.","INVITE_PLAYER"); return end
 if command=="action" and args~="" then CasinoBae:RequireAction(args,"MANUAL"); return end
 print("|cffff4f9aCasinoBae|r /cb ui | hide | status | lobby | join NAME | remove NAME | start | rules | invite NAME | action MESSAGE")
end
