CasinoBae.UI=CasinoBae.UI or {}
local frame
local function button(parent,text,x,y,w,fn)
 local b=CreateFrame("Button",nil,parent,"UIPanelButtonTemplate")
 b:SetSize(w or 88,24); b:SetPoint("TOPLEFT",x,y); b:SetText(text); b:SetScript("OnClick",fn); return b
end
local function ensure()
 if frame then return frame end
 frame=CreateFrame("Frame","CasinoBaeStatusFrame",UIParent,"BackdropTemplate")
 frame:SetSize(500,290); frame:SetPoint("CENTER",0,-80); frame:Hide()
 frame:SetMovable(true); frame:EnableMouse(true); frame:RegisterForDrag("LeftButton")
 frame:SetScript("OnDragStart",frame.StartMoving); frame:SetScript("OnDragStop",frame.StopMovingOrSizing)
 frame:SetBackdrop({bgFile="Interface/Tooltips/Background",edgeFile="Interface/Tooltips/Border",edgeSize=14,insets={left=5,right=5,top=5,bottom=5}})
 frame:SetBackdropColor(0.035,0.025,0.055,0.98)
 frame.title=frame:CreateFontString(nil,"OVERLAY","GameFontHighlightLarge"); frame.title:SetPoint("TOPLEFT",18,-16); frame.title:SetText("CasinoBae  •  HOST COCKPIT")
 frame.state=frame:CreateFontString(nil,"OVERLAY","GameFontNormal"); frame.state:SetPoint("TOPLEFT",18,-48)
 frame.detail=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.detail:SetPoint("TOPLEFT",18,-70); frame.detail:SetWidth(460); frame.detail:SetJustifyH("LEFT")
 frame.players=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.players:SetPoint("TOPLEFT",18,-104)
 button(frame,"LOBBY",18,-130,82,function() CasinoBae:CreateLobby() end)
 button(frame,"START",108,-130,82,function() local ok,err=CasinoBae.Game:StartRand(); if not ok then CasinoBae:Announce(err) end end)
 button(frame,"RULES",198,-130,82,function() CasinoBae:Announce("RAND 1-100 — chacun lance /rand — plus haut gagne — égalité = relance.") end)
 button(frame,"CLOSE",288,-130,82,function() CasinoBae:SetState("READY",{closed=true}); CasinoBae:Announce("LOBBY CLOSED.") end)
 button(frame,"HIDE",378,-130,82,function() frame:Hide() end)
 button(frame,"/SAY",18,-166,82,function() CasinoBae:Say("CasinoBae: prochaine manche dans quelques secondes.") end)
 button(frame,"/EMOTE",108,-166,82,function() CasinoBae:Emote("CHEER") end)
 button(frame,"/YELL*",198,-166,82,function() CasinoBae:Yell("CASINOBAE — prochaine manche !") end)
 button(frame,"DEMO",288,-166,82,function() CasinoBae:Announce("DEMO UI: les résultats réels viennent toujours de WoW.") end)
 frame.hint=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.hint:SetPoint("BOTTOMLEFT",18,16); frame.hint:SetText("/cb ui · /cb status · /cb lobby · /cb start · *YELL peut être protégé par WoW")
 return frame
end
function CasinoBae.UI:Show() ensure():Show(); self:Refresh() end
function CasinoBae.UI:Hide() ensure():Hide() end
function CasinoBae.UI:Refresh()
 local f=ensure(); f.state:SetText("STATE: "..tostring(CasinoBae.STATE))
 local d=CasinoBae.stateDetail
 local detail=""
 if d then detail=d.message or d.action or (d.winner and ("winner: "..d.winner.." / "..tostring(d.value))) or (d.round and ("round "..tostring(d.round))) or "" end
 f.detail:SetText(detail)
 local n=0 for _ in pairs(CasinoBae.lobby.players or {}) do n=n+1 end
 f.players:SetText("PLAYERS: "..n.."   |   ROUND: "..tostring(CasinoBae.lobby.round or 0).."   |   GAME: "..tostring(CasinoBae.lobby.game or "—"))
end
function CasinoBae.UI:SetActionRequired(message,action) CasinoBae:RequireAction(message,action or "MANUAL"); self:Show() end
function CasinoBae.UI:PrintStatus()
 self:Refresh(); self:Show()
 print("|cffff4f9aCasinoBae|r state: "..tostring(CasinoBae.STATE))
 local d=CasinoBae.stateDetail
 if d then print(" detail: "..tostring(d.message or d.action or d.winner or d.round or "")) end
 local n=0 for _ in pairs(CasinoBae.lobby.players or {}) do n=n+1 end
 print(" players: "..n.." round: "..tostring(CasinoBae.lobby.round or 0))
end