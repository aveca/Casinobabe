CasinoBae=CasinoBae or {}
CasinoBae.VERSION="0.1.0"
CasinoBae.STATE="IDLE"
function CasinoBae:SetState(state,detail) self.STATE=state self.stateDetail=detail end
function CasinoBae:Emit(name,payload) self.lastEvent={name=name,payload=payload,state=self.STATE} end
