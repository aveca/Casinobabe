CasinoBae.UI=CasinoBae.UI or {}
local frame

local function setBackdrop(f,bg,border)
 f:SetBackdrop({bgFile="Interface/Tooltips/Background",edgeFile="Interface/Tooltips/Border",edgeSize=14,insets={left=6,right=6,top=6,bottom=6}})
 f:SetBackdropColor(unpack(bg or {0.025,0.02,0.04,0.98}))
 f:SetBackdropBorderColor(unpack(border or {0.35,0.15,0.35,1}))
end

local function button(parent,text,x,y,w,fn)
 local b=CreateFrame("Button",nil,parent,"UIPanelButtonTemplate")
 b:SetSize(w or 90,28); b:SetPoint("TOPLEFT",x,y); b:SetText(text); b:SetScript("OnClick",fn)
 return b
end

local function ensure()
 if frame then return frame end
 frame=CreateFrame("Frame","CasinoBaeStatusFrame",UIParent,"BackdropTemplate")
 frame:SetSize(610,385); frame:SetPoint("CENTER",0,-40); frame:Hide()
 frame:SetMovable(true); frame:EnableMouse(true); frame:RegisterForDrag("LeftButton")
 frame:SetScript("OnDragStart",frame.StartMoving); frame:SetScript("OnDragStop",frame.StopMovingOrSizing)
 setBackdrop(frame,{0.025,0.018,0.04,0.99},{1,0.22,0.60,0.8})

 frame.header=CreateFrame("Frame",nil,frame)
 frame.header:SetPoint("TOPLEFT",8,-8); frame.header:SetPoint("TOPRIGHT",-8,-8); frame.header:SetHeight(58)
 setBackdrop(frame.header,{0.08,0.025,0.07,1},{1,0.25,0.6,0.25})
 frame.title=frame.header:CreateFontString(nil,"OVERLAY","GameFontHighlightLarge"); frame.title:SetPoint("TOPLEFT",16,-12); frame.title:SetText("CASINOBAE")
 frame.subtitle=frame.header:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.subtitle:SetPoint("TOPLEFT",17,-34); frame.subtitle:SetText("HOST COCKPIT  •  SOCIAL GAME CONTROL")
 frame.status=frame.header:CreateFontString(nil,"OVERLAY","GameFontNormal"); frame.status:SetPoint("TOPRIGHT",-16,-19)

 frame.state=frame:CreateFontString(nil,"OVERLAY","GameFontHighlight"); frame.state:SetPoint("TOPLEFT",20,-82)
 frame.detail=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.detail:SetPoint("TOPLEFT",20,-105); frame.detail:SetWidth(565); frame.detail:SetJustifyH("LEFT")

 frame.stats=CreateFrame("Frame",nil,frame,"BackdropTemplate"); frame.stats:SetPoint("TOPLEFT",18,-132); frame.stats:SetSize(574,48)
 setBackdrop(frame.stats,{0.05,0.04,0.07,1},{0.5,0.3,0.5,0.35})
 frame.players=frame.stats:CreateFontString(nil,"OVERLAY","GameFontNormal"); frame.players:SetPoint("LEFT",14,0)
 frame.game=frame.stats:CreateFontString(nil,"OVERLAY","GameFontNormal"); frame.game:SetPoint("RIGHT",-14,0)

 frame.section=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.section:SetPoint("TOPLEFT",20,-195); frame.section:SetText("HOST ACTIONS")

 button(frame,"LOBBY",20,-214,90,function() CasinoBae:CreateLobby() end)
 button(frame,"START",118,-214,90,function() local ok,err=CasinoBae.Game:StartRand(); if not ok then CasinoBae:Announce(err) end end)
 button(frame,"RULES",216,-214,90,function() CasinoBae:Announce("RAND 1-100 — chacun lance /rand — plus haut gagne — égalité = relance.") end)
 button(frame,"CLOSE",314,-214,90,function() CasinoBae:SetState("READY",{closed=true}); CasinoBae:Announce("LOBBY CLOSED.") end)
 button(frame,"HIDE",412,-214,90,function() frame:Hide() end)

 frame.social=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.social:SetPoint("TOPLEFT",20,-258); frame.social:SetText("SOCIAL / PRESENTATION")
 button(frame,"SAY",20,-277,90,function() CasinoBae:Say("CasinoBae: prochaine manche !") end)
 button(frame,"EMOTE",118,-277,90,function() CasinoBae:Emote("CHEER") end)
 button(frame,"YELL",216,-277,90,function() CasinoBae:Yell("CASINOBAE — PROCHAINE MANCHE !") end)
 button(frame,"DEMO",314,-277,90,function() CasinoBae:Announce("DEMO: les résultats réels viennent toujours des événements WoW.") end)
 button(frame,"STATUS",412,-277,90,function() CasinoBae.UI:PrintStatus() end)

 frame.hint=frame:CreateFontString(nil,"OVERLAY","GameFontNormalSmall"); frame.hint:SetPoint("BOTTOMLEFT",20,14); frame.hint:SetText("|cffff4f9a/cb help|r  •  Déplace cette fenêtre par sa barre supérieure.")
 return frame
end

function CasinoBae.UI:Show() ensure():Show(); self:Refresh() end
function CasinoBae.UI:Hide() ensure():Hide() end

function CasinoBae.UI:Refresh()
 local f=ensure()
 local state=tostring(CasinoBae.STATE)
 f.state:SetText("ÉTAT  |cffff4f9a"..state.."|r")
 local d=CasinoBae.stateDetail
 local detail=""
 if d then detail=d.message or d.action or (d.winner and ("WINNER: "..d.winner.."  •  "..tostring(d.value))) or (d.round and ("ROUND "..tostring(d.round))) or "" end
 f.detail:SetText(detail)
 local n=0 for _ in pairs(CasinoBae.lobby.players or {}) do n=n+1 end
 f.players:SetText("|cffffc6dfPLAYERS|r  "..n.."     |cffffc6dfROUND|r  "..tostring(CasinoBae.lobby.round or 0))
 f.game:SetText("|cffffc6dfGAME|r  "..tostring(CasinoBae.lobby.game or "—"))
 if state=="GAME_WAITING_FOR_ROLLS" then f.status:SetText("|cff6ee7a1● LIVE|r")
 elseif state=="ROUND_COMPLETE" then f.status:SetText("|cffff4f9a● WINNER|r")
 elseif state=="ACTION_REQUIRED" then f.status:SetText("|cffffcc66● ACTION REQUIRED|r")
 else f.status:SetText("|cffaaa4b0● READY|r") end
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