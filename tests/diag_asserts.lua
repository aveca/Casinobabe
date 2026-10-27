local ADDON, ns = ...
local D = ns.CasinobabeDiag
assert(D, "CasinobabeDiag table not registered")
local out = D.collect()
assert(type(out) == "table", "collect() must return a table")
assert(out.mapID == 1446, "mapID not captured")
assert(type(out.playerMap) == "table", "playerMap not captured")
assert(type(out.corpseMap) == "table", "corpseMap not captured")
-- The exact fields the old code failed to fill (nil global 'o').
assert(out.navDX ~= nil, "navDX missing: the nav block did not run")
assert(out.navDY ~= nil, "navDY missing: the nav block did not run")
assert(out.navDist ~= nil, "navDist missing")
assert(out.navBearingDeg ~= nil, "navBearingDeg missing")
local txt = D.format(out)
assert(type(txt) == "string" and #txt > 0, "format(out) must render")
assert(type(D.formatCompact(out)) == "string" and #D.formatCompact(out) > 0, "formatCompact(out) must render")
assert(type(out.navDist) == "number", "navDist must be numeric")
assert(out.ghost == true, "ghost state must be captured")
return true
