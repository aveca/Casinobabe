SLASH_CASINOBAE1="/cb"
SlashCmdList.CASINOBAE=function(message)
 local command=string.lower((message or ""):match("^%s*(%S*)") or "")
 if command=="status" then print("|cffffcc00CasinoBae|r state:",CasinoBae.STATE); return end
 print("|cffffcc00CasinoBae|r: /cb status")
end
