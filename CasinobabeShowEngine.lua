ShowEngine = {
  -- Engine state
  isRunning = false,
  currentShow = nil,
  stepIndex = 1,
  scheduledSteps = {},
  cooldowns = {},

  -- Show definitions (timelines)
  shows = {},

  -- TODO: Ajouter une fonction pour gérer les erreurs et les exceptions

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
  end,

  RegisterShow = function(self, name, steps)
    self.shows[name] = steps
    return true
  end,

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
    end

    -- Execute the step based on its type
    if stepType == SHOW_TYPE.NATIVE_EMOTE then
      PerformEmote(stepData.token)
    elseif stepType == SHOW_TYPE.WAIT then
      coroutine.yield(stepData.duration)
    elseif stepType == SHOW_TYPE.CUSTOM_EMOTE then
      SendChatMessage(stepData.text, "SAY")
    elseif stepType == SHOW_TYPE.CHAT then
      SendChatMessage(stepData.text, stepData.channel)
    elseif stepType == SHOW_TYPE.ASCII then
      -- Handle ASCII art display
    end

    return true
  end,

  StartShow = function(self, showName, onComplete)
    -- Check cooldown
    local now = time()
    if (now - self.lastShowTime) < self.showThrottle then
      print("|cffFFAA00Show Engine|r Show on cooldown (" .. self.showThrottle - (now - self.lastSho
      return false, "show_cooldown"
    end
    self.lastShowTime = now

    -- Cancel any running show
    self:CancelShow()

    -- Start the new show
    self.currentShow = showName
    self.stepIndex = 1
    self.isRunning = true

    -- Execute the first step of the show
    local steps = self.shows[showName]
    if steps and #steps > 0 then
      coroutine.wrap(function()
        while self.isRunning do
          local step = steps[self.stepIndex]
          if not step then break end
          if not self:ExecuteStep(step) then
            self:CancelShow()
            break
          end
          self.stepIndex = self.stepIndex + 1
        end
        onComplete and onComplete()
      end)()
    else
      print("|cffFFAA00Show Engine|r No steps defined for show " .. showName)
      self:CancelShow()
    end

    return true
  end,

  CancelShow = function(self)
    self.isRunning = false
    self.currentShow = nil
    self.stepIndex = 1
    print("|cffFFAA00Show Engine|r Show cancelled")
  end,
}

NativeEmoteHandler = {
  -- Handle native emotes
}

ShowScheduler = {
  -- Schedule a show with cooldown management
  ScheduleShow = function(self, showName, onComplete)
    -- Check global show cooldown
    local canRun, remaining = ShowEngine:CheckShowCooldown()
    if not canRun then
      print("|cffFFAA00Scheduler|r Show cooldown active, " .. remaining .. "s remaining")
      return false, "cooldown_active"
    end

    -- Schedule the show
    coroutine.wrap(function()
      coroutine.yield(remaining)
      self:StartShow(showName, onComplete)
    end)()

    return true
  end,

  CheckShowCooldown = function(self)
    local showCd = ShowEngine.COOLDOWN or 5 -- 5s minimum between show types
    local last = ShowEngine.cooldowns["LAST_SHOW"] or 0
    local now = time()
    if (now - last) < showCd then
      return false, showCd - (now - last)
    end
    return true, 0
  end,
}

function ShowEngine:Initialize()
  self.lastEmoteTime = 0
  self.lastShowTime = 0
  self.showThrottle = 10 -- Default throttle time between shows
end
