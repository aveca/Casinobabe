--[[
CasinoBae Auto-Repair System
Analyzes all test failures, clusters by root cause, generates minimal patches.
]]

-- Load harness results
local resultsFile = io.open("tests/offline_harness_results.json", "r")
local harnessResults = {}
if resultsFile then
    local content = resultsFile:read("*a")
    resultsFile:close()
    -- Simple JSON parse (for basic fields)
    harnessResults.passed = content:match('"passed":%s*(%d+)') or 0
    harnessResults.failed = content:match('"failed":%s*(%d+)') or 0
    harnessResults.readyForLive = content:match('"readyForLive":%s*(%w+)') == "true"
end

-- ============================================================================
-- ROOT CAUSE CLUSTERING
-- ============================================================================

local RootCauseAnalyzer = {}
RootCauseAnalyzer.clusters = {}

-- Known root cause patterns
local ROOT_CAUSE_PATTERNS = {
    {
        name = "NAMESPACE_COLLISION_CB",
        patterns = { "CB%.", "CB%[", "CB%.GameRules", "CB%.CasinoEmote" },
        symptoms = { "attempt to index.*nil", "GameRules nil", "CasinoEmote nil" },
        fix = "Add local binding: local X = CB.X after CB.X definition"
    },
    {
        name = "MISSING_LOCAL_BINDING",
        patterns = { "local%s+%w+%s*=" },
        symptoms = { "attempt to call a nil value", "attempt to index.*nil" },
        fix = "Add local binding for module after CB definition"
    },
    {
        name = "UNDEFINED_GLOBAL",
        patterns = { "GameRules", "CasinoSound", "CasinoEmote", "CasinoVariants", "AddonPrint", "DEALER_CONN_STATES", "shortName" },
        symptoms = { "attempt to index global.*nil", "attempt to call global.*nil" },
        fix = "Replace global with CB.* or add local binding"
    },
    {
        name = "FORMAT_NIL_ARG",
        patterns = { "string%.format" },
        symptoms = { "bad argument #%d+ to 'format'", "string expected, got nil" },
        fix = "Add nil checks before string.format calls"
    },
    {
        name = "CALLBACK_NIL",
        patterns = { "SetScript", "C_Timer%.After", "C_Timer%.NewTicker" },
        symptoms = { "attempt to call a nil value", "bad argument #2 to 'SetScript'" },
        fix = "Verify callback function exists before registration"
    },
    {
        name = "UI_OBJECT_NIL",
        patterns = { "CreateFrame", "statBar", "panel" },
        symptoms = { "attempt to index.*nil", "attempt to call method.*nil" },
        fix = "Verify UI object creation succeeded before use"
    },
    {
        name = "DEALER_STATE_NIL",
        patterns = { "dealerSessions", "DEALER_STATES", "DealerGetSession" },
        symptoms = { "attempt to index.*nil", "bad argument #1 to 'pairs'" },
        fix = "Add nil checks in dealer state machine"
    }
}

function RootCauseAnalyzer:analyzeError(err)
    local message = err.message or ""
    local phase = err.phase or ""
    local fn = err.function or ""

    -- Match against known patterns
    for _, cause in ipairs(ROOT_CAUSE_PATTERNS) do
        local matched = false

        -- Check symptoms
        for _, symptom in ipairs(cause.symptoms) do
            if message:match(symptom) then
                matched = true
                break
            end
        end

        -- Check patterns in function/phase
        if not matched then
            for _, pattern in ipairs(cause.patterns) do
                if fn:match(pattern) or phase:match(pattern) or message:match(pattern) then
                    matched = true
                    break
                end
            end
        end

        if matched then
            return cause.name
        end
    end

    -- Fallback: use phase + function
    return phase .. "|" .. fn
end

function RootCauseAnalyzer:clusterErrors(errors)
    local clusters = {}

    for _, err in ipairs(errors) do
        local cause = self:analyzeError(err)
        if not clusters[cause] then
            clusters[cause] = {
                name = cause,
                count = 0,
                errors = {},
                files = {},
                lines = {}
            }
        end
        clusters[cause].count = clusters[cause].count + 1
        table.insert(clusters[cause].errors, err)
        if err.file then table.insert(clusters[cause].files, err.file) end
        if err.line then table.insert(clusters[cause].lines, err.line) end
    end

    return clusters
end

-- ============================================================================
-- PATCH GENERATOR
-- ============================================================================

local PatchGenerator = {}
PatchGenerator.sourceLines = {}

function PatchGenerator:loadSource()
    local f = io.open("runtime-addon/Casinobabe/Casinobabe.lua", "r")
    if f then
        self.sourceLines = {}
        for line in f:lines() do
            table.insert(self.sourceLines, line)
        end
        f:close()
    end
end

function PatchGenerator:generatePatch(cluster)
    local patches = {}

    if cluster.name == "NAMESPACE_COLLISION_CB" then
        -- Find CB.GameRules or CB.CasinoEmote definitions and add local bindings
        for i, line in ipairs(self.sourceLines) do
            if line:match("CB%.GameRules%s*=") and not line:match("local%s+GameRules") then
                -- Check if local binding exists nearby
                local hasLocal = false
                for j = i+1, math.min(i+10, #self.sourceLines) do
                    if self.sourceLines[j]:match("local%s+GameRules%s*=") then
                        hasLocal = true
                        break
                    end
                end
                if not hasLocal then
                    table.insert(patches, {
                        type = "INSERT_AFTER",
                        line = i,
                        code = "GameRules = CB.GameRules",
                        description = "Add local binding for GameRules"
                    })
                end
            end
            if line:match("CB%.CasinoEmote%s*=") and not line:match("local%s+CasinoEmote") then
                local hasLocal = false
                for j = i+1, math.min(i+10, #self.sourceLines) do
                    if self.sourceLines[j]:match("local%s+CasinoEmote%s*=") then
                        hasLocal = true
                        break
                    end
                end
                if not hasLocal then
                    table.insert(patches, {
                        type = "INSERT_AFTER",
                        line = i,
                        code = "local CasinoEmote = CB.CasinoEmote",
                        description = "Add local binding for CasinoEmote"
                    })
                end
            end
        end
    end

    if cluster.name == "MISSING_LOCAL_BINDING" then
        -- Check for MakeBorder, DealerGetConnState, shortName
        local bindings = {
            { pattern = "CB%.MakeBorder%s*=", localName = "MakeBorder", cbName = "CB.MakeBorder" },
            { pattern = "CB%.DealerGetConnState%s*=", localName = "DealerGetConnState", cbName = "CB.DealerGetConnState" },
            { pattern = "function%s+CB%.shortName", localName = "shortName", cbName = "CB.shortName" }
        }

        for _, binding in ipairs(bindings) do
            for i, line in ipairs(self.sourceLines) do
                if line:match(binding.pattern) then
                    local hasLocal = false
                    for j = i+1, math.min(i+15, #self.sourceLines) do
                        if self.sourceLines[j]:match("local%s+" .. binding.localName .. "%s*=") then
                            hasLocal = true
                            break
                        end
                    end
                    if not hasLocal then
                        table.insert(patches, {
                            type = "INSERT_AFTER",
                            line = i,
                            code = "local " .. binding.localName .. " = " .. binding.cbName,
                            description = "Add local binding for " .. binding.localName
                        })
                    end
                    break
                end
            end
        end
    end

    if cluster.name == "UNDEFINED_GLOBAL" then
        -- Replace global references with CB.* or local bindings
        -- This is more complex - would need AST parsing
        -- For now, generate a pattern-based fix
        table.insert(patches, {
            type = "PATTERN_REPLACE",
            pattern = "(^%s*)(%w+)%s*:%s*([%w_]+)%s*%(",
            replacement = function(match, indent, var, method)
                -- Check if var should be CB.var or local
                return indent .. var .. ":" .. method .. "("
            end,
            description = "Fix global variable references (requires manual review)"
        })
    end

    if cluster.name == "FORMAT_NIL_ARG" then
        -- Add nil checks before string.format
        table.insert(patches, {
            type = "PATTERN_REPLACE",
            pattern = "(string%.format%s*%()([^%)]+)(%)",
            replacement = function(match, prefix, args, suffix)
                -- This is a placeholder - real fix needs context-aware analysis
                return prefix .. args .. suffix
            end,
            description = "Add nil checks for string.format arguments (requires manual review)"
        })
    end

    if cluster.name == "CALLBACK_NIL" then
        table.insert(patches, {
            type = "PATTERN_REPLACE",
            pattern = "(SetScript%s*%([^,]+,%s*)([%w_%.]+)(%s*%))",
            replacement = function(match, prefix, fn, suffix)
                return prefix .. "(type(" .. fn .. ") == 'function' and " .. fn .. " or function() end)" .. suffix
            end,
            description = "Guard callback registration with type check"
        })
    end

    return patches
end

function PatchGenerator:applyPatches(patches)
    -- Sort patches by line number descending (so earlier inserts don't shift later lines)
    table.sort(patches, function(a, b) return (a.line or 9999) > (b.line or 9999) end)

    local applied = 0
    for _, patch in ipairs(patches) do
        if patch.type == "INSERT_AFTER" and patch.line then
            table.insert(self.sourceLines, patch.line + 1, patch.code)
            applied = applied + 1
            print(string.format("  Applied: Line %d - %s", patch.line, patch.description))
        elseif patch.type == "PATTERN_REPLACE" then
            -- Pattern-based replacement would need more sophisticated handling
            print(string.format("  Pattern patch queued: %s", patch.description))
        end
    end

    return applied
end

function PatchGenerator:saveSource()
    local f = io.open("runtime-addon/Casinobabe/Casinobabe.lua", "w")
    if f then
        f:write(table.concat(self.sourceLines, "\n"))
        f:close()
        return true
    end
    return false
end

-- ============================================================================
-- AUTO-REPAIR MAIN
-- ============================================================================

local function runAutoRepair()
    print("\n|cffFFD700[AUTO-REPAIR]|r Starting auto-repair analysis...")

    -- Load harness errors
    local errors = WoWMock and WoWMock.errors or {}

    if #errors == 0 then
        print("|cff78EB96[AUTO-REPAIR]|r No errors to repair!")
        return true
    end

    -- Cluster errors
    local clusters = RootCauseAnalyzer:clusterErrors(errors)

    print(string.format("|cffFFD700[AUTO-REPAIR]|r Found %d root cause cluster(s):", #clusters))
    for name, cluster in pairs(clusters) do
        print(string.format("  - %s: %d error(s)", name, cluster.count))
    end

    -- Load source
    PatchGenerator:loadSource()

    -- Generate and apply patches for each cluster
    local totalPatches = 0
    for name, cluster in pairs(clusters) do
        print(string.format("\n|cffFFD700[AUTO-REPAIR]|r Processing cluster: %s", name))
        local patches = PatchGenerator:generatePatch(cluster)
        if #patches > 0 then
            local applied = PatchGenerator:applyPatches(patches)
            totalPatches = totalPatches + applied
        else
            print("  No automatic patches available for this cluster")
        end
    end

    if totalPatches > 0 then
        print(string.format("\n|cffFFD700[AUTO-REPAIR]|r Applied %d patch(es), saving...", totalPatches))
        if PatchGenerator:saveSource() then
            print("|cff78EB96[AUTO-REPAIR]|r Source saved successfully")
            return true
        else
            print("|cffEB5E4F[AUTO-REPAIR]|r Failed to save source")
            return false
        end
    else
        print("|cffFFAA00[AUTO-REPAIR]|r No automatic patches generated - manual intervention needed")
        return false
    end
end

-- Export for use by fix loop
_G.runAutoRepair = runAutoRepair
_G.RootCauseAnalyzer = RootCauseAnalyzer
_G.PatchGenerator = PatchGenerator

print("|cffFFD700[AUTO-REPAIR]|r Auto-repair module loaded")