SLASH_CASINOBAE1="/cb"
SlashCmdList.CASINOBAE=function(message)
 local command,args=(message or ""):match("^%s*(%S*)%s*(.*)$"); command=string.lower(command or "")
 if command=="status" then CasinoBae.UI:PrintStatus(); return end
 if command=="lobby" then CasinoBae:CreateLobby(); return end
 if command=="join" and args~="" then CasinoBae:AddPlayer(args); CasinoBae:Announce(args.." a rejoint le lobby."); return end
 if command=="remove" and args~="" then CasinoBae:RemovePlayer(args); return end
 if command=="start" then local ok,err=CasinoBae.Game:StartRand(); if not ok then CasinoBae:Announce("Impossible de lancer: "..err) end; return end
 if command=="rules" then CasinoBae:Announce("RAND 1-100 — chacun lance /rand — le plus haut gagne — égalité = relance."); return end
 if command=="invite" and args~="" then CasinoBae:RequireAction("Invite manuellement "..args.." dans WoW. CasinoBae attend ensuite la mise à jour du groupe.","INVITE_PLAYER"); return end
 if command=="action" and args~="" then CasinoBae:RequireAction(args,"MANUAL"); return end
 if command=="zone" then CasinoBae:RequireAction("Change de zone puis reviens. CasinoBae attend PLAYER_ENTERING_WORLD.","ZONE_CHANGE"); return end
 print("|cffff4f9aCasinoBae|r /cb status | lobby | join NAME | remove NAME | start | rules | invite NAME | action MESSAGE | zone")
end
