CasinoBae.UI=CasinoBae.UI or {}

function CasinoBae.UI:SetActionRequired(message,action)
 CasinoBae:RequireAction(message,action or "MANUAL")
 print("|cffffaa00CasinoBae ACTION_REQUIRED|r "..tostring(message))
end

function CasinoBae.UI:PrintStatus()
 print("|cffffcc00CasinoBae|r state: "..tostring(CasinoBae.STATE))
 if CasinoBae.stateDetail then print(" detail: "..tostring(CasinoBae.stateDetail.message or CasinoBae.stateDetail.action or "")) end
 local n=0 for _ in pairs(CasinoBae.lobby.players or {}) do n=n+1 end
 print(" players: "..n)
end
