-- ============================================================================
-- CASINOBAE SHOW ENGINE
-- Native WoW emote + Custom emote + Chat + ASCII show manager
-- SÉPARATION ABSOLUE des 4 couches : NATIVE_EMOTE | CUSTOM_EMOTE | CHAT | ASCII
-- ============================================================================

-- ===== Forward declarations =====
local ShowEngine, ShowStep, ShowScheduler, ShowCooldowns
local NativeEmoteHandler, CustomEmoteHandler, ChatHandler, ASCIIHandler, WaitHandler

-- ===== Show Step Types =====
SHOW_TYPE = {
  NATIVE_EMOTE = "NATIVE_EMOTE",
  CUSTOM_EMOTE = "CUSTOM_EMOTE",
  CHAT = "CHAT",
  ASCII = "ASCII",
  WAIT = "WAIT",
}

-- ===== Show Step Structure =====
-- { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" }
-- { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Le croupier frappe la table." }
-- { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Faites vos jeux !" }
-- { type = SHOW_TYPE.ASCII, template = "JACKPOT" }
-- { type = SHOW_TYPE.WAIT, duration = 1.2 }

-- ===== Show Engine Main Module =====
ShowEngine = {
  -- Engine state
  isRunning = false,
  currentShow = nil,
  stepIndex = 1,
  scheduledSteps = {},
  cooldowns = {},

  -- Show definitions (timelines)
  shows = {},

  -- Anti-spam state
  lastEmoteTime = 0,
  lastChatTime = 0,
  lastShowTime = 0,
  showThrottle = 2, -- minimum seconds between full shows

  -- Initialize show definitions
  InitializeShows = function(self)
    -- Register default shows
    self:RegisterShow("WELCOME", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "WAVE" },
      { type = SHOW_TYPE.WAIT, duration = 0.8 },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Bienvenue à la table." },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Faites vos jeux !" },
      { type = SHOW_TYPE.WAIT, duration = 0.5 },
      { type = SHOW_TYPE.ASCII, template = "CASINO" },
    })

    self:RegisterShow("DICE", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "POINT" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Les dés sont lancés." },
      { type = SHOW_TYPE.ASCII, template = "BIG_DICE" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "TOTAL : 7" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
    })

    self:RegisterShow("JACKPOT", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.WAIT, duration = 1.2 },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Le croupier reste immobile... puis relève lentement les bras." },
      { type = SHOW_TYPE.WAIT, duration = 0.8 },
      { type = SHOW_TYPE.ASCII, template = "JACKPOT" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "🎰 JACKPOT !" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "VICTORY" },
    })

    self:RegisterShow("ROULETTE", {
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "La roue ralentit..." },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Rien ne va plus !" },
      { type = SHOW_TYPE.ASCII, template = "ROULETTE" },
      -- Résultat réel sera injecté plus tard
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GASP" }, -- ou CHEER/CRY/VICTORY selon résultat
    })

    self:RegisterShow("BLACKJACK", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "BOW" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Le croupier ajuste ses cartes." },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "POINT" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Faites vos jeux !" },
    })

    self:RegisterShow("FIRE", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "ROAR" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Les flammes s'élèvent autour de la scène..." },
      { type = SHOW_TYPE.ASCII, template = "FIRE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
    })

    self:RegisterShow("SHOWGIRL", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CURTSEY" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Une artiste entre sur scène..." },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "WAVE" },
    })

    self:RegisterShow("FINALE", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CLAP" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "VICTORY" },
      { type = SHOW_TYPE.ASCII, template = "CASINO" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Le spectacle touche à sa fin." },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Merci d'avoir joué !" },
    })

    self:RegisterShow("POKER", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GLOAT" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "FLEX" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "VICTORY" },
      { type = SHOW_TYPE.WAIT, duration = 0.5 },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Le croupier distribue les cartes." },
      { type = SHOW_TYPE.WAIT, duration = 0.5 },
      { type = SHOW_TYPE.ASCII, template = "TROPHY" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Full house!" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GASP" },
    })

    self:RegisterShow("POKER_LOSS", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GASP" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CRY" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DRINK" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "Le silence tombe sur la table." },
      { type = SHOW_TYPE.ASCII, template = "LOSS" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Better luck next time." },
    })

    print("|cffFFD700Casinobabe|r Show Engine initialized with " .. #self.shows .. " show definitions")
    return true
  end,

  -- Register a new show timeline
  RegisterShow = function(self, name, steps)
    self.shows[name] = steps
    return true
  end,

  -- Get a show timeline by name
  GetShow = function(self, name)
    return self.shows[name]
  end,

  -- Execute a single step based on its type
  ExecuteStep = function(self, step)
    local stepType = step.type
    local stepData = step

    -- Anti-spam check per type
    local now = time()
    if stepType == SHOW_TYPE.NATIVE_EMOTE then
      if (now - self.lastEmoteTime) < 2 then
        print("|cffFFAA00Show Engine|r Emote on cooldown, skipping.")
        return false
      end
      self.lastEmoteTime = now
    elseif stepType == SHOW_TYPE.CHAT then
      if (now - self.lastChatTime) < 1 then
        print("|cffFFAA00Show Engine|r Chat on cooldown, skipping.")
        return false
      end
      self.lastChatTime = now
    end

    -- Dispatch to the appropriate handler
    if stepType == SHOW_TYPE.NATIVE_EMOTE then
      return NativeEmoteHandler:Execute(stepData)
    elseif stepType == SHOW_TYPE.CUSTOM_EMOTE then
      return CustomEmoteHandler:Execute(stepData)
    elseif stepType == SHOW_TYPE.CHAT then
      return ChatHandler:Execute(stepData)
    elseif stepType == SHOW_TYPE.ASCII then
      return ASCIIHandler:Execute(stepData)
    elseif stepType == SHOW_TYPE.WAIT then
      return WaitHandler:Execute(stepData)
    else
      print("|cffEB5E4FShow Engine|r Unknown step type: " .. tostring(stepType))
      return false
    end
  end,

  -- Start a show by name
  StartShow = function(self, showName, onComplete)
    -- Check cooldown
    local now = time()
    if (now - self.lastShowTime) < self.showThrottle then
      print("|cffFFAA00Show Engine|r Show on cooldown (" .. self.showThrottle - (now - self.lastShowTime) .. "s remaining)")
      return false, "show_cooldown"
    end
    self.lastShowTime = now

    -- Cancel any running show
    if self.isRunning then
      self:CancelShow()
    end

    local steps = self:GetShow(showName)
    if not steps then
      print("|cffEB5E4FShow Engine|r Show not found: " .. tostring(showName))
      return false, "show_not_found"
    end

    -- Reset step index
    self.stepIndex = 1
    self.isRunning = true
    self.currentShow = showName
    self.currentSteps = steps
    self.onComplete = onComplete

    -- Execute first step
    self:RunStep()

    return true, showName
  end,

  -- Run the current step and schedule next
  RunStep = function(self)
    if not self.isRunning then return end

    local steps = self.currentSteps
    if self.stepIndex > #steps then
      -- Show complete
      local finishedShow = self.currentShow
      self.isRunning = false
      self.currentShow = nil
      if self.onComplete then
        self.onComplete()
      end
      print("|cff78EB96Show Engine|r Show complete: " .. tostring(finishedShow))
      return
    end

    local step = steps[self.stepIndex]
    if not step then
      self:RunStep() -- try next
      return
    end

    -- Execute this step
    local ok, err = pcall(function()
      return ShowEngine:ExecuteStep(step)
    end)

    if not ok then
      print("|cffEB5E4FShow Engine|r Step error at index " .. self.stepIndex .. ": " .. tostring(err))
      self.stepIndex = self.stepIndex + 1 -- skip to next
      self:RunStep()
      return
    end

    -- Schedule next step based on step type
    local step = steps[self.stepIndex]
    if step.type == SHOW_TYPE.WAIT then
      local duration = step.duration or 0
      if duration > 0 then
        C_Timer.After(duration, function()
          if self.isRunning then
            self.stepIndex = self.stepIndex + 1
            self:RunStep()
          end
        end)
      else
        self.stepIndex = self.stepIndex + 1
        self:RunStep()
      end
    else
      -- For non-wait steps, advance immediately
      self.stepIndex = self.stepIndex + 1
      self:RunStep()
    end
  end,

  -- Cancel current show
  CancelShow = function(self)
    self.isRunning = false
    self.currentShow = nil
    self.stepIndex = 1
    print("|cffFFAA00Show Engine|r Show cancelled")
  end,

  -- Check if a show is currently running
  IsShowing = function(self)
    return self.isRunning
  end,

  -- Get current show status
  GetStatus = function(self)
    if self.isRunning then
      return "SHOW_RUNNING: " .. (self.currentShow or "unknown")
    else
      local rem = (self.showThrottle or 2) - (time() - (self.lastShowTime or time()))
      return rem > 0 and "COOLDOWN: " .. math.floor(rem) .. "s" or "READY"
    end
  end,
}

-- ===== Native Emote Handler =====
-- Uses real WoW emote APIs: DoEmote or C_ChatInfo.PerformEmote
NativeEmoteHandler = {
  Execute = function(self, step)
    local token = step.token
    if not token then
      print("|cffEB5E4FNativeEmote|r Missing token in step")
      return false
    end

    -- Try C_ChatInfo.PerformEmote first (modern WoW)
    if C_ChatInfo and C_ChatInfo.PerformEmote then
      local success = pcall(function()
        C_ChatInfo.PerformEmote(token)
      end)
      if success then
        print("|cff78EB96NativeEmote|r Performed via C_ChatInfo.PerformEmote: " .. token)
        return true
      end
      print("|cffFFAA00NativeEmote|r C_ChatInfo.PerformEmote failed, trying DoEmote")
    end

    -- Fallback to DoEmote (deprecated but still works on many clients)
    if DoEmote then
      local success = pcall(function()
        DoEmote(token)
      end)
      if success then
        print("|cff78EB96NativeEmote|r Performed via DoEmote: " .. token)
        return true
      end
      print("|cffFFAA00NativeEmote|r DoEmote failed for: " .. token)
    else
      print("|cffEB5E4FNativeEmote|r DoEmote not available on this client")
    end

    -- If we get here, try sending the slash as text emote fallback
    -- This is NOT the same as executing the emote - it's just text
    if C_ChatInfo and SendChatMessage then
      SendChatMessage("/" .. token, "EMOTE")
      print("|cffFFAA00NativeEmote|r Sent /" .. token .. " as text emote fallback")
    end

    return false
  end,
}

-- ===== Custom Emote Handler =====
-- Uses /e or /me text RP via SendChatMessage
CustomEmoteHandler = {
  Execute = function(self, step)
    local text = step.text
    if not text then
      print("|cffEB5E4FCustomEmote|r Missing text in step")
      return false
    end

    -- Send as proper /e emote
    if SendChatMessage then
      SendChatMessage(text, "EMOTE")
      print("|cff78EB96CustomEmote|r Sent /e: " .. tostring(text):sub(1, 40) .. (tostring(text):len() > 40 and "..." or ""))
      return true
    end

    return false
  end,
}

-- ===== Chat Handler =====
-- Uses /s or normal chat say
ChatHandler = {
  Execute = function(self, step)
    local channel = step.channel or "SAY"
    local text = step.text

    if not text then
      print("|cffEB5E4FChat|r Missing text in step")
      return false
    end

    -- Validate channel
    local validChannels = { "SAY", "YELL", "EMOTE", "WHISPER", "PARTY", "RAID", "GUILD" }
    local isValid = false
    for _, ch in ipairs(validChannels) do
      if ch == channel then
        isValid = true
        break
      end
    end

    if not isValid then
      -- Default to SAY
      channel = "SAY"
    end

    if SendChatMessage then
      SendChatMessage(text, channel)
      print("|cff78EB96Chat|r Sent [" .. channel .. "]: " .. tostring(text):sub(1, 30) .. (tostring(text):len() > 30 and "..." or ""))
      return true
    end

    return false
  end,
}

-- ===== ASCII Handler =====
-- Sends ASCII art in chunks respecting chat limits
ASCIIHandler = {
  -- ASCII templates stored as multi-line strings
  templates = {
    BIG_DICE = {
      "┌───────┐   ┌───────┐",
      "│   ●   │   │ ●   ● │",
      "│       │   │   ●   │",
      "│   ●   │   │ ●   ● │",
      "└───────┘   └───────┘",
    },
    JACKPOT = {
      "┌───────────────┐",
      "│ ███████████ │",
      "│ ████░░░░░██ │",
      "│ ███████████ │",
      "│ ████░░░░░██ │",
      "│ ███████████ │",
      "└───────────────┘",
    },
    ROULETTE = {
      "┌─────────────────┐",
      "│ ██████████████ │",
      "│ ████░░░░░░░░██ │",
      "│ ██████████████ │",
      "│ ███░░░░░░░░░██ │",
      "│ ██████████████ │",
      "└─────────────────┘",
    },
    FIRE = {
      "      ✦      ",
      "     ║║     ",
      "    (•)    ",
    "     ║║     ",
      "    ═══════  ",
      "     / \\    ",
      "    /   \\   ",
    },
    CASINO = {
      "  ▄▄▄▄▄▄▄▄▄▄▄▄  ",
      " █░░░░░░░░░░░░█ ",
      "█░░●●●●●●●●░░█ ",
      "█░░░░░░░░░░░░█ ",
      " █░░░░░░░░░░░░█ ",
      "  ▀▀▀▀▀▀▀▀▀▀▀▀  ",
    },
    LOSS = {
      "┌─────┐",
      "│ ● ● │",
      "│   ● │",
      "└─────┘",
    },
    TROPHY = {
      "┌───────┐",
      "│ ▲ ▲ │",
      "│ █ █ │",
      "│ ▀ ▀ │",
      "└───────┘",
    },
  },

  Execute = function(self, step)
    local templateName = step.template
    if not templateName then
      print("|cffEB5E4FASCII|r Missing template in step")
      return false
    end

    local lines = self.templates[templateName]
    if not lines then
      print("|cffEB5E4FASCII|r Unknown template: " .. tostring(templateName))
      print("|cffFFAA00Available templates:|r BIG_DICE, JACKPOT, ROULETTE, FIRE, CASINO, LOSS, TROPHY")
      return false
    end

    -- Send lines in chunks to respect chat limits (typically 255 chars per line)
    local maxLineLen = 250 -- safe margin
    local totalSent = 0

    for i, line in ipairs(lines) do
      -- Split long lines
      local chunks = {}
      local len = #line
      local pos = 1
      while pos <= len do
        local chunk = line:sub(pos, pos + maxLineLen - 1)
        table.insert(chunks, chunk)
        pos = pos + maxLineLen
      end

      -- Send each chunk (or the full line if short enough)
      for _, chunk in ipairs(chunks) do
        if SendChatMessage then
          SendChatMessage(chunk, "SAY")
          totalSent = totalSent + 1
        end
      end

      -- Small delay between ASCII lines to avoid flooding
      if i < #lines then
        C_Timer.After(0.1, function() end) -- non-blocking, just yields
      end
    end

    print("|cff78EB96ASCII|r Sent template: " .. templateName .. " (" .. #lines .. " lines, " .. totalSent .. " chunks)")
    return true
  end,
}

-- ===== Wait Handler =====
WaitHandler = {
  Execute = function(self, step)
    local duration = step.duration or 0
    if duration and duration > 0 then
      C_Timer.After(duration, function()
        -- Just marks time passage, actual advance happens in scheduler
      end)
      return true, duration
    end
    return true, 0
  end,
}

-- ===== Scheduler Module =====
ShowScheduler = {
  -- Schedule a show with cooldown management
  ScheduleShow = function(self, showName, onComplete)
    -- Check global show cooldown
    local canRun, remaining = ShowEngine:CheckShowCooldown()
    if not canRun then
      print("|cffFFAA00Scheduler|r Show cooldown active, " .. remaining .. "s remaining")
      return false, "cooldown_active"
    end

    -- Start the show
    local ok, result = ShowEngine:StartShow(showName, onComplete)
    if ok then
      -- Record in cooldown manager
      ShowEngine.cooldowns[showName] = time()
    end
    return ok, result
  end,

  -- Show cooldown check
  CheckShowCooldown = function(self)
    local showCd = ShowEngine.COOLDOWN or 5 -- 5s minimum between show types
    local last = ShowEngine.cooldowns["LAST_SHOW"] or 0
    local now = time()
    if (now - last) < showCd then
      return false, showCd - (now - last)
    end
    return true, 0
  end,

  -- Set show cooldown after completion
  SetShowCooldown = function(self)
    ShowEngine.cooldowns["LAST_SHOW"] = time()
  end,
}

-- ===== Global Cooldowns =====
ShowCooldowns = {
  -- Per-show type cooldowns (in seconds)
  emote = 2,
  chat = 1,
  ascii = 0.5,
  show = 3, -- between full show sequences

  -- Check if action is off cooldown
  IsOffCooldown = function(self, actionType)
    local cd = self[actionType] or 0
    local last = ShowEngine.cooldowns[actionType] or 0
    local now = time()
    return (now - last) >= cd, cd - (now - last)
  end,

  -- Set cooldown after action
  SetCooldown = function(self, actionType)
    ShowEngine.cooldowns[actionType] = time()
  end,
}

-- ===== Initialize the engine =====
-- This must run after the addon loads (ADDON_LOADED event)
function ShowEngine:Initialize()
  -- Run default initialization
  self:InitializeShows()

  -- Print initialization summary
  local showCount = 0
  for _ in pairs(self.shows) do showCount = showCount + 1 end
  print("|cffFFD700Casinobabe|r Show Engine loaded - " .. showCount .. " shows registered")
  return true
end

-- Global initialization function called from the addon
function ShowEngine.OnLoad()
  -- Initialize the engine
  if not ShowEngine:Initialize() then
    print("|cffEB5E4FShow Engine|r Failed to initialize")
    return
  end

  -- Register slash commands
  SlashCmdList["CASINOBAE_SHOW"] = function(cmd)
    local command = cmd:lower():trim()
    local showName = command:match("^(%S+)")
    if showName then
      ShowEngine:ShowCommand(showName)
    else
      ShowEngine:ShowHelp()
    end
  end
  SLASH_CASINOBAE_SHOW1 = "/cbs"
  SLASH_CASINOBAE_SHOW2 = "/cbshow"
  print("|cffFFD700Casinobabe|r Show Engine slash commands registered: /cbs, /cbshow")
end

-- Slash command handler
function ShowEngine:ShowCommand(showName)
  if not showName or showName == "" then
    self:ShowHelp()
    return
  end

  -- Check if it's a known show
  if self.shows[showName] then
    local ok, result = self:StartShow(showName)
    if ok then
      print("|cff78EB96Show Engine|r Starting show: " .. showName)
    else
      print("|cffEB5E4FShow Engine|r Failed to start show: " .. tostring(result))
    end
  else
    print("|cffFFAA00Show Engine|r Unknown show: " .. showName)
    print("|cff78EB96Available shows:|r")
    for name in pairs(self.shows) do
      print("  - " .. name)
    end
  end
end

function ShowEngine:ShowHelp()
  print("|cffFFD700Casinobabe Show Engine Help|r")
  print("Usage: /cbs <show_name>")
  print("")
  print("Available shows:")
  for name in pairs(self.shows) do
    local variant = self.shows[name]
    -- Count native emotes in the show
    local nativeCount = 0
    for _, step in ipairs(variant) do
      if step.type == SHOW_TYPE.NATIVE_EMOTE then
        nativeCount = nativeCount + 1
      end
    end
    print("  /cbs " .. name .. "  (" .. nativeCount .. " native emotes)")
  end
  print("")
  print("Examples:")
  print("  /cbs welcome - Welcome show")
  print("  /cbs dice - Dice show")
  print("  /cbs jackpot - Jackpot show")
  print("  /cbs finale - Finale show")
end

-- Make ShowEngine globally accessible (but controlled)
_G.ShowEngine = ShowEngine

-- ============================================================================
-- END OF SHOW ENGINE
-- ============================================================================