CasinoBae.UI=CasinoBae.UI or {}
local frame
local function button(parent,text,x,fn)
 local b=CreateFrame("Button",nil,parent,"UIPanelButtonTemplate"); b:SetSize(78,24); b:SetPoint("TOPLEFT",x,-145); b:SetText(text); b:SetScript("OnClick",fn); return b
end
local function ensure()
 if frame then return frame end
 frame=CreateFrame("Frame","CasinoBaeStatusFrame",UIParent,"BackdropTemplate"); frame:SetSize(420,205); frame:SetPoint("CENTER",0,-80); frame:Hide(); frame:SetMovable(true); frame:EnableMouse(true); frame:RegisterForDrag("LeftButton"); frame:SetScript("OnDragStart",frame.StartMoving); frame:SetScript("OnDragStop",frame.StopMovingOrSizing)
 frame:SetBackdrop({bgFile="Interface/Tooltips/Background",edgeFile="Interface/Tooltips/Border",edgeSize=14,insets={left=5,right=5,top=5,bottom=5}}); frame:SetBackdropColor(0.035,0.025,0.055,0.97)
 frame.title=frame:CreateFontString(nil,"OVERLAY","GameFontHighlightLarge"); frame.title:SetPoint("TOPLEFT",18,-16); frame.title:SetText("CasinoBae  •  HOST")
 frame.state=frame:CreateFontString(nil,"OVERLAY","GameFontNormal"); frame.state:SetPoint("TOPLEFT",18,-52)
 frame.detail=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.detail:SetPoint("TOPLEFT",18,-78); frame.detail:SetWidth(380); frame.detail:SetJustifyH("LEFT")
 frame.players=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.players:SetPoint("TOPLEFT",18,-116)
 button(frame,"LOBBY",18,function() CasinoBae:CreateLobby(); CasinoBae.UI:Refresh() end)
 button(frame,"START",104,function() local ok,err=CasinoBae.Game:StartRand(); if not ok then CasinoBae:Announce(err) end; CasinoBae.UI:Refresh() end)
 button(frame,"RULES",190,function() CasinoBae:Announce("RAND 1-100 — chacun lance /rand — plus haut gagne — égalité = relance.") end)
 button(frame,"HIDE",276,function() frame:Hide() end)
 frame.hint=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.hint:SetPoint("BOTTOMLEFT",18,14); frame.hint:SetText("/cb ui · /cb status · /cb lobby · /cb start")
 return frame
end
function CasinoBae.UI:Show() ensure():Show(); self:Refresh() end
function CasinoBae.UI:Hide() ensure():Hide() end
function CasinoBae.UI:Refresh()
 local f=ensure(); f.state:SetText("STATE: "..tostring(CasinoBae.STATE)); local d=CasinoBae.stateDetail; f.detail:SetText(d and (d.message or d.action or ("round "..tostring(d.round or ""))) or "")
 local n=0 for _ in pairs(CasinoBae.lobby.players or {}) do n=n+1 end; f.players:SetText("PLAYERS: "..n.."   |   ROUND: "..tostring(CasinoBae.lobby.round or 0))
end
function CasinoBae.UI:SetActionRequired(message,action) CasinoBae:RequireAction(message,action or "MANUAL"); self:Show() end
function CasinoBae.UI:PrintStatus() self:Refresh(); self:Show(); print("|cffff4f9aCasinoBae|r state: "..tostring(CasinoBae.STATE)); local d=CasinoBae.stateDetail; if d then print(" detail: "..tostring(d.message or d.action or d.round or "")) end; local n=0 for _ in pairs(CasinoBae.lobby.players or {}) do n=n+1 end; print(" players: "..n.." round: "..tostring(CasinoBae.lobby.round or 0)) end
