-----------------------------------------------------------------------
-- LibDBIcon-1.0 (self-contained, TBC-Classic compatible)
--
-- Lightweight minimap icon library. Bundled with Casinobabe.
-- Based on the standard LibDBIcon-1.0 API, adapted to use `self` (TBC 2.5.5,
-- not the Vanilla `this`/`arg1` globals) and without hard dependencies on
-- LibDataBroker-1.1 / CallbackHandler-1.0 so it loads safely on its own.
--
-- Registered through LibStub with a low minor, so if another addon provides a
-- newer/official LibDBIcon-1.0 it simply takes precedence.
-----------------------------------------------------------------------
local DBICON10 = "LibDBIcon-1.0"
local DBICON10_MINOR = 1

if not LibStub then return end
local lib = LibStub:NewLibrary(DBICON10, DBICON10_MINOR)
if not lib then return end -- a newer LibDBIcon-1.0 is already loaded

lib.objects = lib.objects or {}
lib.notCreated = lib.notCreated or {}
lib.loggedIn = lib.loggedIn or false

local function getAnchors(frame)
	local x, y = frame:GetCenter()
	if not x or not y then return "CENTER" end
	local hhalf = (x > UIParent:GetWidth()*2/3) and "RIGHT" or (x < UIParent:GetWidth()/3) and "LEFT" or ""
	local vhalf = (y > UIParent:GetHeight()/2) and "TOP" or "BOTTOM"
	return vhalf..hhalf, frame, (vhalf == "TOP" and "BOTTOM" or "TOP")..hhalf
end

local function onEnter(self)
	if self.isMoving then return end
	local obj = self.dataObject
	if obj.OnTooltipShow then
		GameTooltip:SetOwner(self, "ANCHOR_NONE")
		GameTooltip:SetPoint(getAnchors(self))
		obj.OnTooltipShow(GameTooltip)
		GameTooltip:Show()
	elseif obj.OnEnter then
		obj.OnEnter(self)
	end
end

local function onLeave(self)
	local obj = self.dataObject
	GameTooltip:Hide()
	if obj.OnLeave then obj.OnLeave(self) end
end

local minimapShapes = {
	["ROUND"] = {true, true, true, true},
	["SQUARE"] = {false, false, false, false},
	["CORNER-TOPLEFT"] = {false, false, false, true},
	["CORNER-TOPRIGHT"] = {false, false, true, false},
	["CORNER-BOTTOMLEFT"] = {false, true, false, false},
	["CORNER-BOTTOMRIGHT"] = {true, false, false, false},
	["SIDE-LEFT"] = {false, true, false, true},
	["SIDE-RIGHT"] = {true, false, true, false},
	["SIDE-TOP"] = {false, false, true, true},
	["SIDE-BOTTOM"] = {true, true, false, false},
	["TRICORNER-TOPLEFT"] = {false, true, true, true},
	["TRICORNER-TOPRIGHT"] = {true, false, true, true},
	["TRICORNER-BOTTOMLEFT"] = {true, true, false, true},
	["TRICORNER-BOTTOMRIGHT"] = {true, true, true, false},
}

local function updatePosition(button)
	local angle = math.rad(button.db and button.db.minimapPos or button.minimapPos or 225)
	local x, y, q = math.cos(angle), math.sin(angle), 1
	if x < 0 then q = q + 1 end
	if y > 0 then q = q + 2 end
	local minimapShape = (GetMinimapShape and GetMinimapShape()) or "ROUND"
	local quadTable = minimapShapes[minimapShape] or minimapShapes["ROUND"]
	if quadTable[q] then
		x, y = x*80, y*80
	else
		local diagRadius = 103.13708498985 -- math.sqrt(2*(80)^2)-10
		x = math.max(-80, math.min(x*diagRadius, 80))
		y = math.max(-80, math.min(y*diagRadius, 80))
	end
	button:ClearAllPoints()
	button:SetPoint("CENTER", Minimap, "CENTER", x, y)
end

local function onClick(self, button) if self.dataObject.OnClick then self.dataObject.OnClick(self, button) end end

local function onUpdate(self)
	local mx, my = Minimap:GetCenter()
	local px, py = GetCursorPosition()
	local scale = Minimap:GetEffectiveScale()
	px, py = px / scale, py / scale
	local pos = math.deg(math.atan2(py - my, px - mx)) % 360
	if self.db then self.db.minimapPos = pos else self.minimapPos = pos end
	updatePosition(self)
end

local function onDragStart(self)
	self:SetScript("OnUpdate", onUpdate)
	self.isMoving = true
	GameTooltip:Hide()
end

local function onDragStop(self)
	self:SetScript("OnUpdate", nil)
	self.isMoving = nil
end

local function createButton(name, object, db)
	local button = CreateFrame("Button", "LibDBIcon10_"..name, Minimap)
	button.dataObject = object
	button.db = db
	button:SetFrameStrata("MEDIUM")
	button:SetWidth(31); button:SetHeight(31)
	button:SetFrameLevel(8)
	button:RegisterForClicks("anyUp")
	button:RegisterForDrag("LeftButton")
	button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

	local overlay = button:CreateTexture(nil, "OVERLAY")
	overlay:SetWidth(53); overlay:SetHeight(53)
	overlay:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
	overlay:SetPoint("TOPLEFT", 0, 0)

	local background = button:CreateTexture(nil, "BACKGROUND")
	background:SetWidth(20); background:SetHeight(20)
	background:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
	background:SetPoint("TOPLEFT", 7, -5)

	local icon = button:CreateTexture(nil, "ARTWORK")
	icon:SetWidth(17); icon:SetHeight(17)
	if object.icon then icon:SetTexture(object.icon) end
	icon:SetPoint("TOPLEFT", 7, -6)
	button.icon = icon

	button:SetScript("OnEnter", onEnter)
	button:SetScript("OnLeave", onLeave)
	button:SetScript("OnClick", onClick)
	if not db or not db.lock then
		button:SetScript("OnDragStart", onDragStart)
		button:SetScript("OnDragStop", onDragStop)
	end

	lib.objects[name] = button

	if lib.loggedIn then
		updatePosition(button)
		if not db or not db.hide then button:Show() else button:Hide() end
	end
end

local function check(name)
	if lib.notCreated[name] then
		createButton(name, lib.notCreated[name][1], lib.notCreated[name][2])
		lib.notCreated[name] = nil
	end
end

-- Position/show everything once we're logged in (gives GetMinimapShape addons
-- a chance to load first).
if not lib.loginFrame then
	lib.loginFrame = CreateFrame("Frame")
	lib.loginFrame:SetScript("OnEvent", function(self)
		for _, object in pairs(lib.objects) do
			updatePosition(object)
			if not object.db or not object.db.hide then object:Show() else object:Hide() end
		end
		lib.loggedIn = true
		self:UnregisterAllEvents()
		self:SetScript("OnEvent", nil)
	end)
	lib.loginFrame:RegisterEvent("PLAYER_LOGIN")
end

function lib:Register(name, object, db)
	if not object.icon then error("Can't register LDB objects without icons set!") end
	if lib.objects[name] or lib.notCreated[name] then return end
	if not db or not db.hide then
		createButton(name, object, db)
	else
		lib.notCreated[name] = {object, db}
	end
end

function lib:Lock(name)
	if lib.objects[name] then
		lib.objects[name]:SetScript("OnDragStart", nil)
		lib.objects[name]:SetScript("OnDragStop", nil)
	end
	if lib.objects[name] and lib.objects[name].db then lib.objects[name].db.lock = true end
end

function lib:Unlock(name)
	if lib.objects[name] then
		lib.objects[name]:SetScript("OnDragStart", onDragStart)
		lib.objects[name]:SetScript("OnDragStop", onDragStop)
	end
	if lib.objects[name] and lib.objects[name].db then lib.objects[name].db.lock = nil end
end

function lib:Hide(name)
	if lib.objects[name] then lib.objects[name]:Hide() end
end

function lib:Show(name)
	check(name)
	if lib.objects[name] then
		lib.objects[name]:Show()
		updatePosition(lib.objects[name])
	end
end

function lib:IsRegistered(name)
	return (lib.objects[name] or lib.notCreated[name]) and true or false
end

function lib:Refresh(name, db)
	check(name)
	local button = lib.objects[name]
	if not button then return end
	if db then button.db = db end
	updatePosition(button)
	if not button.db or not button.db.hide then button:Show() else button:Hide() end
end

function lib:GetMinimapButton(name)
	return lib.objects[name]
end

function lib:GetButtonList()
	local list = {}
	for name in pairs(lib.objects) do list[#list + 1] = name end
	return list
end

function lib:EnableLibrary() end
function lib:DisableLibrary() end
