CasinoBae.UI=CasinoBae.UI or {}
function CasinoBae.UI:SetActionRequired(message)
 CasinoBae:SetState("ACTION_REQUIRED",message)
 CasinoBae:Emit("ACTION_REQUIRED",{message=message})
end
