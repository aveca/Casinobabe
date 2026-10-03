SLASH_CASINOBAE1="/cb"
SlashCmdList.CASINOBAE=function(message)
 local command,args=(message or ""):match("^%s*(%S*)%s*(.*)$")
 command=string.lower(command or "")
 if command=="" or command=="ui" then CasinoBae.UI:Show(); return end
 if command=="help" or command=="?" then print("|cffff4f9aCasinoBae|r /cb ui status lobby close start rules join remove invite whisper say yell emote announce action demo"); return end
 if command=="hide" then CasinoBae.UI:Hide(); return end
 if command=="status" then CasinoBae.UI:PrintStatus(); return end
 if command=="lobby" then CasinoBae:CreateLobby(); return end
 if command=="close" then CasinoBae:SetState("READY",{closed=true}); CasinoBae:Announce("LOBBY CLOSED."); return end
 if command=="join" and args~="" then CasinoBae:AddPlayer(args); CasinoBae:Announce(args.." a rejoint le lobby."); return end
 if command=="remove" and args~="" then CasinoBae:RemovePlayer(args); return end
 if command=="start" then local ok,err=CasinoBae.Game:StartRand(); if not ok then CasinoBae:Announce("Impossible de lancer: "..err) end; return end
 if command=="rules" then CasinoBae:Announce("RAND 1-100 — chacun lance /rand — plus haut gagne — égalité = relance."); return end
 if command=="say" and args~="" then CasinoBae:Say(args); return end
 if command=="yell" and args~="" then CasinoBae:Yell(args); return end
 if command=="emote" and args~="" then if not CasinoBae:Emote(string.upper(args)) then CasinoBae:Announce("Emote inconnue ou indisponible: "..args) end; return end
 if command=="announce" and args~="" then CasinoBae:Announce(args); return end
 if command=="whisper" then local name,text=(args or ""):match("^(%S+)%s+(.+)$"); if name and text then SendChatMessage(text,"WHISPER",nil,name) else CasinoBae:Announce("Usage: /cb whisper NAME MESSAGE") end; return end
 if command=="invite" and args~="" then CasinoBae:RequireAction("Invite manuellement "..args.." dans WoW. CasinoBae attend un changement réel du groupe; vérifie que "..args.." est bien présent.","INVITE_PLAYER",args); return end
 if command=="action" and args~="" then CasinoBae:RequireAction(args,"MANUAL"); return end
 if command=="demo" then CasinoBae:CreateLobby(); CasinoBae:AddPlayer("DemoPlayer"); CasinoBae:Announce("DEMO: lobby prêt. Lance /cb start pour utiliser le vrai /rand."); return end
 print("|cffff4f9aCasinoBae|r commandes:")
 print("/cb ui | hide | status | lobby | close | start | rules")
 print("/cb join NAME | remove NAME | invite NAME")
 print("/cb whisper NAME MESSAGE | say MESSAGE | yell MESSAGE")
 print("/cb emote TOKEN | announce MESSAGE | action MESSAGE | demo")
end