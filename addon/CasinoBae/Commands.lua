SLASH_CASINOBAE1="/cb"
SlashCmdList.CASINOBAE=function(message)
 local command,args=(message or ""):match("^%s*(%S*)%s*(.*)$"); command=string.lower(command or "")
 if command=="status" then CasinoBae.UI:PrintStatus(); return end
 if command=="lobby" then CasinoBae:CreateLobby(); return end
 if command=="add" and args~="" then CasinoBae:AddPlayer(args); CasinoBae:Announce(args.." a rejoint le lobby."); return end
 if command=="start" then local ok,err=CasinoBae.Game:StartRand(); CasinoBae:Announce(ok and "RAND lancé — tous les joueurs doivent utiliser /rand." or ("Impossible de lancer: "..err)); return end
 if command=="rules" then CasinoBae:Announce("Règle RAND: chaque joueur lance /rand, le plus haut résultat gagne."); return end
 if command=="invite" and args~="" then CasinoBae:RequireAction("Invite manuellement "..args.." dans WoW.","INVITE_PLAYER"); return end
 if command=="action" and args~="" then CasinoBae:RequireAction(args,"MANUAL"); return end
 print("|cffffcc00CasinoBae|r /cb status | lobby | add NAME | start | rules | invite NAME | action MESSAGE")
end
