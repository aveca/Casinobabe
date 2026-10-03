-- Casinobabe.lua  -  KUND-PANEL v4 (kompakt)
-- ----------------------------------------------------------------------------
-- Kompaktare layout (far plats i spelet), mindre center-popup, "online"-koll
-- via /who, samt How to play + Games guide-rutor.
-- Bets/sprak/cashback postas som synliga raid-kommandon; saldo kommer tillbaka
-- via addon-kanalen. Spelaren maste vara med i CASINOTS RAID.
-- ============================================================================

local ADDON  = "Casinobabe"
local PREFIX = "CBABE"
local MEDIA  = "Interface\\AddOns\\Casinobabe\\media\\background"

-- ===== forward =====
local panel, langMenu
local dealerPanel  -- dealer UI
local UpdateDisplay, SetStatus, SetConnected, RequestState, PlaceBet
local CollectCashback, SelectGame, SelectAmount, FlashResult, HandleMessage
local PostPublic, ToggleBlockTrades, OpenLanguageMenu, ShowDiscord
local PopupStart, PopupActivate, PopupResolve, PopupHide, PopupConfigure
local DoRoll, CheckOnline, SetOnlineStatus, ShowInfo, OpenPanel

-- Dealer forward declarations
local DealerInit, DealerToggle, DealerStatus, DealerAdvertise
local DealerInvite, DealerGame, DealerStake, DealerRoll, DealerRecord
local DealerResolve, DealerPayout, DealerClose, DealerReset, DealerLog
local DealerOnWhisper, DealerOnTradeShow, DealerOnTradeAccept, DealerOnTradeClose
local DealerOnSystemMsg, DealerUpdateUI, DealerAuditLog
local DealerGetSession, DealerCreateSession, DealerSetState
local DealerComputePayout, DealerValidateTransition

-- Utility forward declarations
local shortName

-- ===== spel =====
local GAMES = {
  { key="normal",    name="Normal",    pay="59+ x2",   icon="INV_Misc_Coin_01",               roll="self" },
  { key="high",      name="High Risk", pay="76+ x3",   icon="Spell_Fire_Fire",                roll="self" },
  { key="blackjack", name="Blackjack", pay="~100",     icon="INV_Misc_Ticket_Tarot_Stack_01", roll="bj"   },
  { key="roulette",  name="Roulette",  pay="R/B x2",   icon="INV_Misc_Gear_01",               roll="self" },
  { key="dice",      name="Dice",      pay="2d6",      icon="DICE_DRAW",                     roll="dice" },
  { key="lucky7",    name="Lucky 7",   pay="end7 x7",  icon="INV_Misc_Coin_07",               roll="self" },
}
local QUICK = { 10, 25, 50, 100, 250, 500, 750 }
local MAX_BET, MIN_BET = 1000, 1   -- casinots insatsgranser (1g - 1000g)

-- ============================================================================
-- DEALER MODE - Central Game Rules (Single Source of Truth)
-- ============================================================================
local DEALER_NAME = "Casinobae"
local isDealerMode = false
local dealerEnabled = false  -- manual toggle via /cb dealer on
local autoAttractRunning = false  -- auto-dealer attract state

-- ============================================================================
-- DEALER COOLDOWN MANAGER - Single canonical cooldown system
-- ============================================================================
local DealerCooldownManager = {
  cooldowns = {},
  DEFAULTS = {
    SHOW = 120,
    ADVERTISE = 120,
    REACTION = 5,
    JOIN = 10,
    SPAM_WINDOW = 60,
    SPAM_MAX = 5,
    SPAM_IGNORE = 300,
  },
  Check = function(self, key)
    local cd = self.DEFAULTS[key] or 0
    local last = self.cooldowns[key] or 0
    return (time() - last) < cd, cd - (time() - last)
  end,
  Set = function(self, key, customDuration)
    self.cooldowns[key] = time()
    if customDuration then
      self.DEFAULTS[key] = customDuration
    end
  end,
  Remaining = function(self, key)
    local cd = self.DEFAULTS[key] or 0
    local last = self.cooldowns[key] or 0
    local rem = cd - (time() - last)
    return rem > 0 and rem or 0
  end,
  Clear = function(self, key)
    self.cooldowns[key] = nil
  end,
  GetAll = function(self)
    local result = {}
    for k, v in pairs(self.DEFAULTS) do
      result[k] = self:Remaining(k)
    end
    return result
  end,
}

-- ============================================================================
-- DEALER CONNECTION STATE MACHINE
-- ============================================================================
local DEALER_CONN_STATES = {
  OFF = "OFF",
  STARTING = "STARTING",
  CONNECTING = "CONNECTING", 
  CONNECTED = "CONNECTED",
  READY = "READY",
  COOLDOWN = "COOLDOWN",
  ERROR = "ERROR",
}
local dealerConnState = DEALER_CONN_STATES.OFF
local dealerConnReason = ""
local dealerConnLastMsg = 0

local function DealerSetConnState(state, reason)
  local old = dealerConnState
  dealerConnState = state
  dealerConnReason = reason or ""
  dealerConnLastMsg = time()
  -- Diagnostics
  print(string.format("|cffFFD700Casinobabe|r [DEALER] STATE %s -> %s%s", old, state, reason and (" (" .. reason .. ")") or ""))
  -- Update UI
  if panel and panel.dealerPanel then
    local dp = panel.dealerPanel
    if dp.connStatus then
      local color = (state == DEALER_CONN_STATES.CONNECTED or state == DEALER_CONN_STATES.READY) and C.green or 
                    (state == DEALER_CONN_STATES.CONNECTING) and C.gold or C.mute
      dp.connStatus:SetText(state)
      dp.connStatus:SetTextColor(color[1], color[2], color[3])
    end
    if dp.connReason and reason then
      dp.connReason:SetText(reason)
    end
  end
end

local function DealerGetConnState()
  return dealerConnState, dealerConnReason
end

-- ============================================================================
-- DEALER DIAGNOSTICS
-- ============================================================================
local function DealerDiag(event, details)
  local msg = string.format("|cffFFD700Casinobabe|r [DIAG] %s", event)
  if details then msg = msg .. " " .. details end
  print(msg)
end

-- Capital cities for auto-attract
local CAPITAL_CITIES = {
  ["Stormwind City"] = true,
  ["Orgrimmar"] = true,
  ["Ironforge"] = true,
  ["Thunder Bluff"] = true,
  ["Undercity"] = true,
  ["Dalaran"] = true,
  ["Shattrath City"] = true,
  ["Capital City"] = true,  -- fallback for localized clients
}

local function IsInCapital()
  local zone = GetRealZoneText() or ""
  return CAPITAL_CITIES[zone] == true
end

-- NOTE: shortName/MakeBorder are defined early on purpose. Lua binds locals
-- positionally, so dealer code further down (IsDealerCharacter, whisper and
-- trade/roll handlers, dealer panel) must see them. They were previously
-- declared near the file end, which made every earlier call hit a nil global.
local function shortName(full) if not full then return nil end return full:match("^([^%-]+)") or full end
local function MakeBorder(f,t)
  t=t or 2
  local function line() local x=f:CreateTexture(nil,"OVERLAY"); x:SetColorTexture(0,0,0,0); return x end
  local b={top=line(),bot=line(),left=line(),right=line()}
  b.top:SetPoint("TOPLEFT"); b.top:SetPoint("TOPRIGHT"); b.top:SetHeight(t)
  b.bot:SetPoint("BOTTOMLEFT"); b.bot:SetPoint("BOTTOMRIGHT"); b.bot:SetHeight(t)
  b.left:SetPoint("TOPLEFT"); b.left:SetPoint("BOTTOMLEFT"); b.left:SetWidth(t)
  b.right:SetPoint("TOPRIGHT"); b.right:SetPoint("BOTTOMRIGHT"); b.right:SetWidth(t)
  function b:SetColor(r,g,bl,a) for _,x in pairs({self.top,self.bot,self.left,self.right}) do x:SetColorTexture(r,g,bl,a or 1) end end
  return b
end

-- GameRules: Single source of truth for ALL game logic (player + dealer)
-- Do NOT duplicate rules - both modes consume this
local GameRules = {
  normal = {
    key = "normal",
    name = "Normal",
    rollMin = 1,
    rollMax = 100,
    stakeMin = 1,
    stakeMax = 1000,
    -- returns multiplier (0 = loss, >0 = win multiplier)
    resolve = function(roll)
      if roll == 100 then return 3 end      -- jackpot x3
      if roll >= 59 then return 2 end       -- win x2
      return 0                              -- loss
    end,
    tieResolver = nil,  -- no ties in single-roll
    description = "Roll 1-100. 59-99 = x2, 100 = x3 jackpot.",
  },
  high = {
    key = "high",
    name = "High Risk",
    rollMin = 1,
    rollMax = 100,
    stakeMin = 1,
    stakeMax = 1000,
    resolve = function(roll)
      if roll == 100 then return 4 end      -- jackpot x4
      if roll >= 76 then return 3 end       -- win x3
      return 0                              -- loss
    end,
    tieResolver = nil,
    description = "Roll 1-100. 76-99 = x3, 100 = x4 jackpot. (1-75 lose)",
  },
  lucky7 = {
    key = "lucky7",
    name = "Lucky 7",
    rollMin = 1,
    rollMax = 100,
    stakeMin = 1,
    stakeMax = 1000,
    resolve = function(roll)
      if (roll % 10) == 7 then return 7 end -- ends in 7 = x7
      return 0
    end,
    tieResolver = nil,
    description = "Roll 1-100. Roll ending in 7 (7,17,27...97) = x7!",
  },
  roulette = {
    key = "roulette",
    name = "Roulette",
    rollMin = 1,
    rollMax = 100,
    stakeMin = 1,
    stakeMax = 1000,
    colors = { "red", "green", "black" },
    -- color: "red"|"green"|"black" (lowercase)
    resolve = function(roll, color)
      local c = (color or ""):lower()
      if c == "red" then
        return (roll >= 1 and roll <= 45) and 2 or 0
      elseif c == "green" then
        return (roll >= 46 and roll <= 55) and 5 or 0
      elseif c == "black" then
        return (roll >= 56 and roll <= 100) and 2 or 0
      end
      return 0
    end,
    tieResolver = nil,
    description = "Pick color, then /roll 1-100. Red 1-45 = x2, Green 46-55 = x5, Black 56-100 = x2.",
  },
  blackjack = {
    key = "blackjack",
    name = "Blackjack",
    rollMin = 1,
    rollMax = 100,
    stakeMin = 1,
    stakeMax = 1000,
    -- Dealer doesn't roll; house rolls. Dealer just tracks player total.
    -- Player hits until stand or bust (>100). House then plays.
    -- For dealer mode: we track player's rolls, they say "stand", then house rolls.
    resolve = function(playerTotal, houseTotal)
      -- Returns: 1 = player wins (x2), 2 = push/tie (stake returned), 0 = loss
      if playerTotal > 100 then return 0 end           -- player bust
      if houseTotal > 100 then return 1 end            -- house bust
      if playerTotal > houseTotal then return 1 end    -- player higher
      if playerTotal == houseTotal then return 2 end   -- push
      return 0                                         -- house higher
    end,
    tieResolver = function() return 2 end,  -- push = stake returned
    description = "Get closest to 100 without busting. Hit to draw, Stand to stop. Beat the house.",
  },
  dice = {
    key = "dice",
    name = "Dice",
    rollMin = 2,
    rollMax = 12,  -- 2d6 sum
    stakeMin = 1,
    stakeMax = 1000,
    choices = { "over", "under", "seven" },
    -- choice: "over"|"under"|"seven"
    -- houseRolls: {die1, die2}
    resolve = function(choice, houseRolls)
      local total = (houseRolls[1] or 0) + (houseRolls[2] or 0)
      local c = (choice or ""):lower()
      if c == "over" then
        return (total >= 8 and total <= 12) and 2 or 0
      elseif c == "under" then
        return (total >= 2 and total <= 6) and 2 or 0
      elseif c == "seven" then
        return (total == 7) and 4 or 0
      end
      return 0
    end,
    tieResolver = nil,
    description = "House rolls 2d6. Over (8-12) = x2, Under (2-6) = x2, exactly 7 = x4.",
  },
}

-- ============================================================================
-- CASINOSOUND - Soundboard for dealer feedback (local only)
-- ============================================================================
local CasinoSound = {
  -- SoundKit IDs verified for Classic 20505
  -- Only LOCAL - players cannot hear these
  SOUNDS = {
    -- UI / General
    WELCOME        = 8960, -- READY_CHECK
    TRADE_OPEN     = 5274, -- AUCTION_WINDOW_OPEN
    TRADE_CLOSE    = 5275, -- AUCTION_WINDOW_CLOSE
    CLICK          = 856,  -- GS_TITLE_OPTION_OK
    ERROR          = 857,  -- GS_TITLE_OPTION_EXIT
    
    -- Roll / Dice
    ROLL_START     = 43917, -- UI_BONUS_LOOT_ROLL_START
    ROLL_END       = 43918, -- UI_BONUS_LOOT_ROLL_END
    DICE_SHAKE     = 33338, -- UI_BONUS_LOOT_ROLL_START (alternative)
    
    -- Game specific
    ROULETTE_SPIN  = 59351, -- UI_GARRISON_MISSION_COMPLETE_ENCOUNTER
    BLACKJACK_HIT  = 882,   -- TALENT_SCREEN_OPEN
    BLACKJACK_STAND= 857,   -- GS_TITLE_OPTION_EXIT
    CARD_FLIP      = 1201,  -- UI_IG_STORE_PAGE_NAV_BUTTON
    
    -- Win/Loss
    WIN_SMALL      = 32941, -- UI_IG_STORE_PURCHASE_DELIVERED
    WIN_BIG        = 888,   -- LEVEL_UP
    JACKPOT        = 51402, -- UI_EPICLOOT_TOAST
    LOSS           = 857,   -- GS_TITLE_OPTION_EXIT (negative)
    NEAR_MISS      = 73279, -- UI_BONUS_EVENT_SYSTEM_VIGNETTES
    
    -- Show / Drama
    DRAMA_PAUSE    = 8959,  -- RAID_WARNING (attention)
    CTA            = 8959,  -- RAID_WARNING
    CURTAIN        = 856,   -- GS_TITLE_OPTION_OK
    SUSPENSE       = 73279, -- UI_BONUS_EVENT_SYSTEM_VIGNETTES
    
    -- Trade / Money
    MONEY          = 32941, -- UI_IG_STORE_PURCHASE_DELIVERED
    COIN           = 33338, -- UI_BONUS_LOOT_ROLL_START
    
    -- Farewell
    GOODBYE        = 5275,  -- AUCTION_WINDOW_CLOSE
    CURTAIN_CLOSE  = 857,   -- GS_TITLE_OPTION_EXIT
  },
  
  Play = function(self, key)
    local id = self.SOUNDS[key]
    if id and PlaySound then
      PlaySound(id)
    end
  end,
  
  -- Play multiple sounds in sequence
  PlaySequence = function(self, keys, delays)
    for i, key in ipairs(keys) do
      local delay = delays[i] or 0
      if delay > 0 then
        C_Timer.After(delay, function() self:Play(key) end)
      else
        self:Play(key)
      end
    end
  end,
}

-- ============================================================================
-- CASINOEMOTE - Emote helpers (physical + text)
-- ============================================================================
local CasinoEmote = {
  -- Physical emotes (require hardware event - must be triggered via macro/button click)
  PHYSICAL = {
    WAVE    = "WAVE",
    BOW     = "BOW",
    CHEER   = "CHEER",
    CLAP    = "CLAP",
    POINT   = "POINT",
    DANCE   = "DANCE",
    SALUTE  = "SALUTE",
    LAUGH   = "LAUGH",
    GASP    = "GASP",
    SHRUG   = "SHRUG",
    FLEX    = "FLEX",
    CACKLE  = "CACKLE",
    VIOLIN  = "VIOLIN",
    NO      = "NO",
    YES     = "YES",
    READY   = "READY",
    THANK   = "THANK",
    WELCOME = "WELCOME",
    GOODBYE = "BYE",
    CONFUSED= "CONFUSED",
    RUDE    = "RUDE",
    ROAR    = "ROAR",
    KISS    = "KISS",
    CRY     = "CRY",
  },
  
  -- Text emotes (automated via SendChatMessage "EMOTE" channel)
  TEXT = {
    -- Atmosphere
    ADJUSTS_COAT    = "ajuste son manteau.",
    LOOKS_CROWD     = "regarde la foule.",
    FLIPS_COIN      = "fait tourner une piÃ¨ce entre ses doigts.",
    WATCHES_DICE    = "observe les dÃ©s avec attention.",
    PLACES_CHIP     = "pose un jeton sur la table.",
    SMILES          = "sourrit mystÃ©rieusement.",
    NODS            = "incline la tÃªte.",
    APPLAUDS        = "applaudit lentement.",
    STUDIES_RESULT  = "examine le rÃ©sultat avec attention.",
    LEANS_BACK      = "se penche en arriÃ¨re, dÃ©tendu.",
    TAPS_TABLE      = "tape doucement sur la table.",
    CHECKS_WATCH    = "vÃ©rifie une montre imaginaire.",
    SHUFFLES_CARDS  = "mÃ©lange des cartes invisibles.",
    LIGHTS_CIGAR    = "allume un cigare imaginaire.",
    POLISHES_GLASS  = "essuie un verre.",
    
    -- Welcome
    WELCOMES_PLAYER = "accueille le nouveau venu.",
    OPENS_DOORS     = "ouvre grand les portes du casino.",
    
    -- Game moments
    DEALS_CARDS     = "distribue les cartes.",
    SPINS_WHEEL     = "fait tourner la roulette.",
    ROLLS_DICE      = "lance les dÃ©s.",
    REVEALS_CARD    = "retourne une carte.",
    CALLS_NUMBER    = "annonce le numÃ©ro.",
    
    -- Reactions
    IMPRESSED       = "semble impressionnÃ©.",
    UNIMPRESSED     = "reste de marbre.",
    SURPRISED       = "semble surpris.",
    SYMPATHETIC     = "fait un geste de sympathie.",
    RESPECTFUL      = "salue le joueur avec respect.",
    
    -- Outro
    CLOSES_TABLE    = "ferme la table pour la nuit.",
    TIPS_HAT        = "touche son chapeau en signe d'adieu.",
    WALKS_AWAY      = "s'Ã©loigne dans la nuit.",
  },
  
  -- Contextual emote suggestions for dealer buttons
  CONTEXTUAL = {
    WELCOME = { "WAVE", "BOW", "WELCOME" },
    BIG_WIN = { "CHEER", "CLAP", "CHEER" },
    JACKPOT = { "CHEER", "CLAP", "GASP", "CHEER" },
    WIN     = { "CLAP", "NODS", "SMILES" },
    LOSS    = { "SHRUG", "SYMPATHETIC", "VIOLIN" },
    NEAR_MISS = { "GASP", "TAPS_TABLE", "UNIMPRESSED" },
    OUTRO   = { "BOW", "WAVE", "GOODBYE", "TIPS_HAT" },
    DRAMA   = { "GASP", "STUDIES_RESULT", "LEANS_BACK" },
    TRADE   = { "CHECKS_WATCH", "POLISHES_GLASS", "NODS" },
  },
  
  -- Play physical emote (must be called from secure context - button click)
  PlayPhysical = function(self, token)
    local emote = self.PHYSICAL[token]
    if emote and DoEmote then
      DoEmote(emote)
    end
  end,
  
  -- Play text emote (automated)
  PlayText = function(self, key)
    local text = self.TEXT[key]
    if text and SendChatMessage then
      SendChatMessage(text, "EMOTE")
    end
  end,
  
  -- Play contextual emote (text + optional physical)
  PlayContext = function(self, context, includePhysical)
    local list = self.CONTEXTUAL[context]
    if not list then return end
    
    -- Play random text emote from context
    local textKey = list[math.random(#list)]
    self:PlayText(textKey)
    
    -- Suggest physical emote (dealer must click button)
    if includePhysical and list[1] then
      return list[1]
    end
  end,
  
  -- Get suggested physical emote for context
  GetSuggestedPhysical = function(self, context)
    local list = self.CONTEXTUAL[context]
    return list and list[1]
  end,
}

-- ============================================================================
-- CASINOSEQUENCE - Timed sequence engine
-- ============================================================================
local CasinoSequence = {
  active = {},
  counter = 0,
  
  -- Create a new sequence
  -- steps = { {delay=0, fn=function() ... end, cond=function() return true end}, ... }
  Create = function(self, steps, name)
    self.counter = self.counter + 1
    local id = name and (name .. "_" .. self.counter) or ("seq_" .. self.counter)
    local seq = {
      id = id,
      steps = steps,
      current = 1,
      cancelled = false,
      paused = false,
    }
    self.active[id] = seq
    self:Run(id)
    return id
  end,
  
  Run = function(self, id)
    local seq = self.active[id]
    if not seq or seq.cancelled or seq.paused then return end
    
    local step = seq.steps[seq.current]
    if not step then
      self.active[id] = nil
      if step and step.onComplete then step.onComplete() end
      return
    end
    
    local delay = step.delay or 0
    local cond = step.cond
    
    local function execute()
      if seq.cancelled or seq.paused then return end
      if cond and not cond() then
        -- Condition failed, skip or cancel
        if step.onFail then step.onFail() end
        seq.current = seq.current + 1
        self:Run(id)
        return
      end
      
      if step.fn then step.fn() end
      seq.current = seq.current + 1
      self:Run(id)
    end
    
    if delay > 0 then
      C_Timer.After(delay, execute)
    else
      execute()
    end
  end,
  
  Cancel = function(self, id)
    if self.active[id] then
      self.active[id].cancelled = true
      self.active[id] = nil
    end
  end,
  
  Pause = function(self, id)
    if self.active[id] then self.active[id].paused = true end
  end,
  
  Resume = function(self, id)
    if self.active[id] then
      self.active[id].paused = false
      self:Run(id)
    end
  end,
  
  CancelAll = function(self)
    for id, _ in pairs(self.active) do
      self.active[id].cancelled = true
    end
    self.active = {}
  end,
  
  IsRunning = function(self, id)
    return self.active[id] and not self.active[id].cancelled
  end,
}

-- ============================================================================
-- CASINOVARIANTS - Variant system for messages/emotes/reactions
-- ============================================================================
local CasinoVariants = {
  -- Welcome messages
  WELCOME = {
    "Welcome to Casinobae Casino. The table is open.",
    "Step right up. The house is feeling generous today.",
    "Welcome. Care to test your luck?",
    "Ah, a new face. The table has been waiting.",
    "Come in, come in. The games are about to begin.",
    "Welcome to Casinobae. Where fortune favors the bold.",
    "Step up. The cards have been waiting for you.",
    "Welcome to Casinobae. Where the house always wins... eventually.",
  },
  
  -- Returning player
  WELCOME_BACK = {
    "Welcome back. The table remembers.",
    "Ah, a returning player. Feeling lucky today?",
    "Welcome home. Your seat is still warm.",
    "Back again? Let's see if fortune favors you twice.",
  },
  
  -- Game selection
  GAME_PROMPT = {
    "Choose your game. The table offers many paths.",
    "What will it be? Normal, High Risk, Blackjack, Roulette, Dice, or Lucky 7?",
    "Pick your poison. Each game has its own... personality.",
    "The games are laid out. Which calls to you?",
    "Choose wisely. The dice remember every roll.",
  },
  
  -- Game specific
  GAME_NORMAL = {
    "Normal. Simple. Elegant. 59+ doubles, 100 triples.",
    "Normal it is. The classic choice. 59 to 99 pays double, 100 pays triple.",
    "Ah, Normal. Where dreams are made... and broken at 58.",
  },
  GAME_HIGH = {
    "High Risk. Bold. 76+ triples, 100 quadruples. 1-75... the house keeps it all.",
    "High Risk. For those who don't believe in 'safe'.",
    "High Risk. The house loves this one. 76 to win, 100 to crush it.",
  },
  GAME_LUCKY7 = {
    "Lucky 7. The only game where the number 7 is your best friend. Ends in 7? Seven times your money.",
    "Lucky 7. Superstition made profitable. 7, 17, 27... 97.",
    "Lucky 7. Seven is the number. Are you feeling it?",
  },
  GAME_ROULETTE = {
    "Roulette. Red 1-45 doubles. Green 46-55 pays five times. Black 56-100 doubles. Choose your color.",
    "Roulette. The wheel doesn't lie. Red, Green, or Black?",
    "Roulette. Where the ball lands, fortune follows.",
  },
  GAME_BLACKJACK = {
    "Blackjack. Get close to 100 without busting. Hit or Stand. Beat the house.",
    "Blackjack. The only game where you decide your fate. Hit... or Stand?",
    "Blackjack. The house plays by rules. You play by instinct.",
  },
  GAME_DICE = {
    "Dice. House rolls 2d6. Over 8-12 doubles. Under 2-6 doubles. Exactly 7? Quadruples.",
    "Dice. Two cubes. Infinite possibilities. Over, Under, or the Lucky 7?",
    "Dice. The oldest game. The house rolls, you choose.",
  },
  
  -- Stake
  STAKE_PROMPT = {
    "How much are you putting on the table?",
    "Stake? The house accepts 1g to 1000g.",
    "How brave are we feeling today?",
    "Name your price. The table accepts gold.",
    "Lay it down. The table is waiting.",
  },
  
  -- Trade
  TRADE_READY = {
    "Trade window open. The amount is set.",
    "The trade is ready. Verify the amount.",
    "Gold on the table. The house is watching.",
  },
  TRADE_VERIFIED = {
    "Perfect. The stake is confirmed.",
    "Amount verified. The game begins.",
    "Gold received. Let's play.",
  },
  TRADE_MISMATCH = {
    "That's not the agreed amount. Try again.",
    "The gold doesn't match. The house doesn't negotiate.",
    "Incorrect amount. The trade will not proceed.",
  },
  
  -- Roll
  ROLL_PROMPT = {
    "Roll the dice. /roll 1-100.",
    "Your turn. Let the dice decide.",
    "Roll. Fate is waiting.",
    "The dice are ready. Your roll.",
  },
  
  -- Roll reactions
  ROLL_NEAR_MISS = {
    "So close... {roll}. One number away.",
    "{roll}... The house almost paid.",
    "A whisper from fortune. {roll}. So near.",
    "Heartbreaking. {roll}. Just one short.",
  },
  ROLL_WIN = {
    "Excellent. {roll} pays {mult}x.",
    "Well played. {roll} wins {payout}g.",
    "The table pays. {roll} wins {mult}x stake.",
  },
  ROLL_BIG_WIN = {
    "Magnificent! {roll} pays {mult}x! {payout}g!",
    "The house groans. {roll} pays {payout}g!",
    "A beautiful roll. {roll}. {mult}x multiplier!",
  },
  ROLL_JACKPOT = {
    "ðŸŽ° JACKPOT! {roll}! The house pays {payout}g!",
    "ðŸŽ° {roll}! JACKPOT! {mult}x! The house bleeds {payout}g!",
    "ðŸŽ° THE HOUSE HAS A PROBLEM. {roll}! JACKPOT! {payout}g!",
  },
  ROLL_LOSS = {
    "The house takes this one. {roll} loses.",
    "Not this time. {roll}. The house keeps the stake.",
    "Fortune frowned. {roll}. Better luck next round.",
  },
  ROLL_HIGH_LOSS = {
    "High Risk demands high rolls. {roll} wasn't enough.",
    "The risk was high. The reward... not today. {roll}.",
    "76 was the line. {roll} fell short.",
  },
  
  -- Game resolution
  RESOLUTION_WIN = {
    "The table has spoken. Victory.",
    "The house pays. Well played.",
    "A win for the player. The house accepts it.",
  },
  RESOLUTION_LOSS = {
    "The house collects. Better luck next time.",
    "The stake is ours. Fortune favors the house today.",
    "A loss. The table is patient.",
  },
  RESOLUTION_PUSH = {
    "A push. The stake returns. No winner, no loser.",
    "Even Steven. The stake comes back. Play again?",
    "Tie goes to... nobody. Stake returned.",
  },
  
  -- Payout
  PAYOUT_READY = {
    "The payout is ready. {amount}g.",
    "The house pays {amount}g. Come collect.",
    "{amount}g waiting. Trade when ready.",
  },
  PAYOUT_DONE = {
    "Paid in full. Pleasure doing business.",
    "The gold is yours. Well earned.",
    "Transaction complete. The house honors its debts.",
  },
  
  -- Outro
  OUTRO = {
    "Pleasure doing business. The table will be waiting.",
    "Come back anytime. The house is patient.",
    "Well played. The doors remain open.",
    "Until next time. The house never closes.",
    "Take your winnings. Leave the luck for next time.",
  },
  
  -- Capital show
  SHOW_INTRO = {
    "/me se tourne lentement vers la foule.",
    "/me ajuste son manteau et regarde l'horizon.",
    "/me fait tourner une piÃ¨ce entre ses doigts.",
    "/me pose une main sur la table, prÃªt Ã  commencer.",
  },
  SHOW_GAMES = {
    "ðŸŽ° NORMAL â€” 59+ x2, 100 x3",
    "ðŸ”¥ HIGH RISK â€” 76+ x3, 100 x4",
    "ðŸƒ BLACKJACK â€” Beat the house",
    "ðŸ”´ ROULETTE â€” Red/Black x2, Green x5",
    "ðŸŽ² DICE â€” Over/Under x2, 7 x4",
    "ðŸ€ LUCKY 7 â€” Ends in 7 = x7",
  },
  SHOW_CTA = {
    "Pick your poison. /w Casinobae JOIN",
    "The table is open. /w Casinobae JOIN",
    "Step right up. /w Casinobae JOIN",
    "Your move. /w Casinobae JOIN",
  },
  SHOW_OUTRO = {
    "/me incline la tÃªte avec respect.",
    "/me range les jetons lentement.",
    "/me Ã©teint les lanternes de la table.",
  },
  
  -- Reactions
  REACTION_NEAR_MISS = {
    "So close... {roll}. The house almost paid.",
    "{roll}... One number. One. Single. Number.",
    "A whisper from fortune. {roll}. So near, yet so far.",
  },
  REACTION_WIN = {
    "Well played. {roll} wins {payout}g.",
    "The table pays. {roll} â€” {mult}x.",
    "Excellent roll. {payout}g to the player.",
  },
  REACTION_BIG_WIN = {
    "Magnificent! {roll} pays {mult}x! {payout}g!",
    "The house groans. {roll} wins {payout}g!",
    "A beautiful roll. {roll}. {mult}x multiplier!",
  },
  REACTION_JACKPOT = {
    "ðŸŽ° JACKPOT! {roll}! The house pays {payout}g!",
    "ðŸŽ° {roll}! JACKPOT! {mult}x! The house bleeds {payout}g!",
    "ðŸŽ° THE HOUSE HAS A PROBLEM. {roll}! JACKPOT! {payout}g!",
  },
  REACTION_LOSS = {
    "The house takes this one. {roll}.",
    "Not this time. {roll}. The house keeps the stake.",
    "Fortune frowned. {roll}. Better luck next round.",
  },
  REACTION_NEAR_MISS = {
    "So close... {roll}. The house almost paid.",
    "{roll}... One number. One. Single. Number.",
    "A whisper from fortune. {roll}. So near, yet so far.",
  },
  REACTION_WIN = {
    "Well played. {roll} wins {payout}g.",
    "The table pays. {roll} â€” {mult}x.",
    "Excellent roll. {payout}g to the player.",
  },
  REACTION_BIG_WIN = {
    "Magnificent! {roll} pays {mult}x! {payout}g!",
    "The house groans. {roll} wins {payout}g!",
    "A beautiful roll. {roll}. {mult}x multiplier!",
  },
  REACTION_JACKPOT = {
    "ðŸŽ° JACKPOT! {roll}! The house pays {payout}g!",
    "ðŸŽ° {roll}! JACKPOT! {mult}x! The house bleeds {payout}g!",
    "ðŸŽ° THE HOUSE HAS A PROBLEM. {roll}! JACKPOT! {payout}g!",
  },
  REACTION_LOSS = {
    "The house takes this one. {roll}.",
    "Not this time. {roll}. The house keeps the stake.",
    "Fortune frowned. {roll}. Better luck next round.",
  },
  
  -- Get random variant
  Get = function(self, category, vars)
    local list = self[category]
    if not list or #list == 0 then return nil end
    local msg = list[math.random(#list)]
    if vars then
      for k, v in pairs(vars) do
        msg = msg:gsub("{" .. k .. "}", tostring(v))
      end
    end
    return msg
  end,
  
  -- Get multiple sequential variants
  GetSequence = function(self, category, count)
    local list = self[category]
    if not list or #list == 0 then return {} end
    local result = {}
    local indices = {}
    for i = 1, #list do indices[i] = i end
    -- Shuffle
    for i = #indices, 2, -1 do
      local j = math.random(i)
      indices[i], indices[j] = indices[j], indices[i]
    end
    for i = 1, math.min(count or #list, #list) do
      table.insert(result, list[indices[i]])
    end
    return result
  end,
}

-- ============================================================================
-- CASINOREACTIONENGINE - Context-aware reactions
-- ============================================================================
local CasinoReactionEngine = {
  cooldowns = {},
  COOLDOWNS = {
    SAY = 3,
    YELL = 30,
    EMOTE = 2,
    REACTION = 5,
    -- SHOW cooldown now managed by DealerCooldownManager
  },
  
  -- Check if action is on cooldown
  OnCooldown = function(self, action)
    local cd = self.COOLDOWNS[action] or 0
    local last = self.cooldowns[action] or 0
    return (time() - last) < cd
  end,
  
  SetCooldown = function(self, action)
    self.cooldowns[action] = time()
  end,
  
  -- Main reaction dispatcher
  React = function(self, event, context)
    context = context or {}
    
    if event == "PLAYER_JOIN" then
      return self:ReactPlayerJoin(context)
    elseif event == "PLAYER_RETURN" then
      return self:ReactPlayerReturn(context)
    elseif event == "PLAYER_GROUPED" then
      return self:ReactPlayerGrouped(context)
    elseif event == "TRADE_OPEN" then
      return self:ReactTradeOpen(context)
    elseif event == "TRADE_VERIFIED" then
      return self:ReactTradeVerified(context)
    elseif event == "TRADE_MISMATCH" then
      return self:ReactTradeMismatch(context)
    elseif event == "GAME_SELECTED" then
      return self:ReactGameSelected(context)
    elseif event == "ROLL_START" then
      return self:ReactRollStart(context)
    elseif event == "ROLL_RECEIVED" then
      return self:ReactRollReceived(context)
    elseif event == "NEAR_MISS" then
      return self:ReactNearMiss(context)
    elseif event == "WIN" then
      return self:ReactWin(context)
    elseif event == "BIG_WIN" then
      return self:ReactBigWin(context)
    elseif event == "JACKPOT" then
      return self:ReactJackpot(context)
    elseif event == "LOSS" then
      return self:ReactLoss(context)
    elseif event == "PAYOUT_READY" then
      return self:ReactPayoutReady(context)
    elseif event == "PAYOUT_DONE" then
      return self:ReactPayoutDone(context)
    elseif event == "PLAYER_LEAVE" then
      return self:ReactPlayerLeave(context)
    elseif event == "TIMEOUT" then
      return self:ReactTimeout(context)
    elseif event == "CAPITAL_ENTER" then
      return self:ReactCapitalEnter(context)
    elseif event == "CAPITAL_SHOW" then
      return self:ReactCapitalShow(context)
    end
    
    return nil
  end,
  
  -- Individual reaction handlers
  ReactPlayerJoin = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("WELCOME")
    return {
      { channel = "WHISPER", target = ctx.player, msg = CasinoVariants:Get("WELCOME") },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("WELCOME") },
      { sound = "WELCOME" },
      { physical = physical },
    }
  end,
  
  ReactPlayerReturn = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "WHISPER", target = ctx.player, msg = CasinoVariants:Get("WELCOME_BACK") },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("WELCOMES_PLAYER") },
    }
  end,
  
  ReactPlayerGrouped = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "SAY", msg = "Seat confirmed. Welcome to the table." },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("NODS") },
      { sound = "TRADE_OPEN" },
    }
  end,
  
  ReactTradeOpen = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "SAY", msg = CasinoVariants:Get("TRADE_READY") },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("CHECKS_WATCH") },
      { sound = "TRADE_OPEN" },
    }
  end,
  
  ReactTradeVerified = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("TRADE")
    return {
      { channel = "SAY", msg = CasinoVariants:Get("TRADE_VERIFIED") },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("NODS") },
      { sound = "TRADE_CLOSE" },
      { physical = physical },
    }
  end,
  
  ReactTradeMismatch = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "SAY", msg = CasinoVariants:Get("TRADE_MISMATCH") },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("UNIMPRESSED") },
      { sound = "ERROR" },
    }
  end,
  
  ReactGameSelected = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local key = "GAME_" .. ctx.game:upper()
    local physical = CasinoEmote:GetSuggestedPhysical("DRAMA")
    return {
      { channel = "SAY", msg = CasinoVariants:Get(key, { game = ctx.game }) },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("DRAMA") },
      { sound = "ROLL_START" },
      { physical = physical },
    }
  end,
  
  ReactRollStart = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "WHISPER", target = ctx.player, msg = CasinoVariants:Get("ROLL_PROMPT") },
      { channel = "SAY", msg = "The dice are ready. Roll when ready." },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("ROLLS_DICE") },
      { sound = "ROLL_START" },
    }
  end,
  
  ReactRollReceived = function(self, ctx)
    -- Just log, wait for resolve
    return nil
  end,
  
  ReactNearMiss = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("NEAR_MISS")
    return {
      { channel = "SAY", msg = CasinoVariants:Get("REACTION_NEAR_MISS", { roll = ctx.roll }) },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("NEAR_MISS") },
      { sound = "NEAR_MISS" },
      { physical = physical },
      { fx = "NEAR_MISS" },
    }
  end,
  
  ReactWin = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("WIN")
    return {
      { channel = "SAY", msg = CasinoVariants:Get("REACTION_WIN", { roll = ctx.roll, payout = ctx.payout, mult = ctx.mult }) },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("WIN") },
      { sound = "WIN_SMALL" },
      { physical = physical },
      { fx = "WIN" },
    }
  end,
  
  ReactBigWin = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("BIG_WIN")
    return {
      { channel = "SAY", msg = CasinoVariants:Get("REACTION_BIG_WIN", { roll = ctx.roll, payout = ctx.payout, mult = ctx.mult }) },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("WIN") },
      { sound = "WIN_BIG" },
      { physical = physical },
      { fx = "BIG_WIN" },
    }
  end,
  
  ReactJackpot = function(self, ctx)
    -- Jackpot bypasses normal cooldown for YELL
    local physical = CasinoEmote:GetSuggestedPhysical("JACKPOT")
    return {
      { channel = "YELL", msg = CasinoVariants:Get("REACTION_JACKPOT", { roll = ctx.roll, payout = ctx.payout, mult = ctx.mult }) },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("JACKPOT") },
      { sound = "JACKPOT" },
      { physical = physical },
      { fx = "JACKPOT" },
    }
  end,
  
  ReactLoss = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("LOSS")
    local key = ctx.game == "high" and "ROLL_HIGH_LOSS" or "ROLL_LOSS"
    return {
      { channel = "SAY", msg = CasinoVariants:Get(key, { roll = ctx.roll }) },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("LOSS") },
      { sound = "LOSS" },
      { physical = physical },
      { fx = "LOSS" },
    }
  end,
  
  ReactPayoutReady = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("TRADE")
    return {
      { channel = "WHISPER", target = ctx.player, msg = CasinoVariants:Get("PAYOUT_READY", { amount = ctx.amount }) },
      { channel = "SAY", msg = ("Payout ready. {amount}g waiting."):gsub("{amount}", ctx.amount) },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("PLACES_CHIP") },
      { sound = "MONEY" },
      { physical = physical },
    }
  end,
  
  ReactPayoutDone = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    local physical = CasinoEmote:GetSuggestedPhysical("OUTRO")
    return {
      { channel = "WHISPER", target = ctx.player, msg = CasinoVariants:Get("PAYOUT_DONE") },
      { channel = "SAY", msg = "Paid in full. Pleasure doing business." },
      { channel = "EMOTE", msg = CasinoEmote:PlayContext("OUTRO") },
      { sound = "GOODBYE" },
      { physical = physical },
    }
  end,
  
  ReactPlayerLeave = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "SAY", msg = "Leaving so soon? The table will be waiting." },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("WALKS_AWAY") },
    }
  end,
  
  ReactTimeout = function(self, ctx)
    if self:OnCooldown("REACTION") then return nil end
    self:SetCooldown("REACTION")
    return {
      { channel = "WHISPER", target = ctx.player, msg = "Still there? ðŸ‘€ The table is waiting." },
      { channel = "EMOTE", msg = CasinoEmote:PlayText("CHECKS_WATCH") },
    }
  end,
  
  ReactCapitalEnter = function(self, ctx)
    -- Silent - just UI notification
    return {
      { ui = "CAPITAL_DETECTED", zone = ctx.zone },
    }
  end,
  
  ReactCapitalShow = function(self, ctx)
    if self:OnCooldown("SHOW") then return nil end
    self:SetCooldown("SHOW")
    return {
      { show = true, variant = ctx.variant or "DEFAULT" },
    }
  end,
}

-- Helper to execute reaction actions
function CasinoReactionEngine:Execute(actions, ctx)
  if not actions then return end
  for _, action in ipairs(actions) do
    if action.channel and action.msg then
      local target = action.target
      SendChatMessage(action.msg, action.channel, nil, target)
    elseif action.channel == "EMOTE" and action.msg then
      SendChatMessage(action.msg, "EMOTE")
    elseif action.sound then
      CasinoSound:Play(action.sound)
    elseif action.physical then
      -- Queue physical emote suggestion for dealer UI
      ctx.suggestedEmote = action.physical
    elseif action.fx then
      ctx.suggestedFx = action.fx
    elseif action.show then
      ctx.triggerShow = action.variant or true
    elseif action.ui then
      ctx.uiEvent = action
    end
  end
end

-- Helper to get contextual reaction
function CasinoReactionEngine:Trigger(event, ctx)
  local actions = self:React(event, ctx)
  if actions then
    self:Execute(actions, ctx)
  end
end

-- ============================================================================
-- CASINOANNOUNCER - Capital Show System
-- ============================================================================
local CasinoAnnouncer = {
  currentShow = nil,
  showCooldown = 0,
  SHOW_COOLDOWN = 120, -- 2 minutes between shows
  
  -- Show variants
  VARIANTS = {
    DEFAULT = {
      name = "Standard Show",
      weight = 40,
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("CURTAIN"); CasinoEmote:PlayText("ADJUSTS_COAT") end },
        { delay = 1.5, fn = function() SendChatMessage(CasinoVariants:Get("SHOW_INTRO"), "EMOTE") end },
        { delay = 3,   fn = function() CasinoSound:Play("DRAMA_PAUSE") end },
        { delay = 3.5, fn = function() SendChatMessage("ðŸŽ° The table is OPEN.", "SAY") end },
        { delay = 5,   fn = function() CasinoEmote:PlayText("FLIPS_COIN") end },
        { delay = 6,   fn = function() SendChatMessage("Step right up...", "SAY") end },
        { delay = 8,   fn = function() CasinoSound:Play("CTA"); SendChatMessage("ðŸŽ° CASINOBAE CASINO IS OPEN ðŸŽ°", "YELL") end },
        { delay = 10,  fn = function() CasinoEmote:PlayText("POINTS_CROWD") end },
        -- Games reveal
        { delay = 12,  fn = function() SendChatMessage("ðŸŽ° NORMAL â€” 59+ x2, 100 x3", "SAY") end },
        { delay = 13.5, fn = function() SendChatMessage("ðŸ”¥ HIGH RISK â€” 76+ x3, 100 x4", "SAY") end },
        { delay = 15,  fn = function() SendChatMessage("ðŸƒ BLACKJACK â€” Beat the house", "SAY") end },
        { delay = 16.5, fn = function() SendChatMessage("ðŸ”´ ROULETTE â€” Red/Black x2, Green x5", "SAY") end },
        { delay = 18,  fn = function() SendChatMessage("ðŸŽ² DICE â€” Over/Under x2, 7 x4", "SAY") end },
        { delay = 19.5, fn = function() SendChatMessage("ðŸ€ LUCKY 7 â€” Ends in 7 = x7", "SAY") end },
        { delay = 21,  fn = function() CasinoSound:Play("SUSPENSE") end },
        { delay = 22,  fn = function() SendChatMessage(CasinoVariants:Get("SHOW_CTA"), "SAY") end },
        { delay = 24,  fn = function() CasinoEmote:PlayText("SMILES"); CasinoSound:Play("CURTAIN") end },
      },
    },
    ELEGANT = {
      name = "Elegant Dealer",
      weight = 20,
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("CURTAIN"); CasinoEmote:PlayText("ADJUSTS_COAT") end },
        { delay = 2,   fn = function() SendChatMessage("/me incline la tÃªte avec Ã©lÃ©gance.", "EMOTE") end },
        { delay = 4,   fn = function() SendChatMessage("ðŸŽ° The house welcomes you.", "SAY") end },
        { delay = 6,   fn = function() CasinoEmote:PlayText("SMILES") end },
        { delay = 7,   fn = function() SendChatMessage("ðŸŽ° NORMAL â€” 59+ x2, 100 x3", "SAY") end },
        { delay = 8.5, fn = function() SendChatMessage("ðŸ”¥ HIGH RISK â€” 76+ x3, 100 x4", "SAY") end },
        { delay = 10,  fn = function() SendChatMessage("ðŸƒ BLACKJACK â€” Beat the house", "SAY") end },
        { delay = 11.5, fn = function() SendChatMessage("ðŸ”´ ROULETTE â€” Red/Black x2, Green x5", "SAY") end },
        { delay = 13,  fn = function() SendChatMessage("ðŸŽ² DICE â€” Over/Under x2, 7 x4", "SAY") end },
        { delay = 14.5, fn = function() SendChatMessage("ðŸ€ LUCKY 7 â€” Ends in 7 = x7", "SAY") end },
        { delay = 16,  fn = function() CasinoSound:Play("CTA"); SendChatMessage("The table is open. /w Casinobae JOIN", "SAY") end },
        { delay = 18,  fn = function() CasinoEmote:PlayText("BOW") end },
      },
    },
    CRAZY = {
      name = "Crazy Casino",
      weight = 15,
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("CTA"); CasinoEmote:PlayText("DANCE") end },
        { delay = 1,   fn = function() SendChatMessage("ðŸŽ°ðŸŽ°ðŸŽ° CASINOBAE IS OPEN! ðŸŽ°ðŸŽ°ðŸŽ°", "YELL") end },
        { delay = 2,   fn = function() CasinoEmote:PlayText("CHEER") end },
        { delay = 2.5, fn = function() SendChatMessage("ðŸŽ° NORMAL â€” 59+ x2, 100 x3!", "SAY") end },
        { delay = 3,   fn = function() SendChatMessage("ðŸ”¥ HIGH RISK â€” 76+ x3, 100 x4!", "SAY") end },
        { delay = 3.5, fn = function() SendChatMessage("ðŸƒ BLACKJACK â€” BEAT THE HOUSE!", "SAY") end },
        { delay = 4,   fn = function() SendChatMessage("ðŸ”´ ROULETTE â€” RED/BLACK x2, GREEN x5!", "SAY") end },
        { delay = 4.5, fn = function() SendChatMessage("ðŸŽ² DICE â€” OVER/UNDER x2, 7 x4!", "SAY") end },
        { delay = 5,   fn = function() SendChatMessage("ðŸ€ LUCKY 7 â€” ENDS IN 7 = x7!", "SAY") end },
        { delay = 5.5, fn = function() CasinoEmote:PlayText("CHEER") end },
        { delay = 6,   fn = function() SendChatMessage("JOIN NOW! /w Casinobae JOIN", "YELL") end },
        { delay = 7,   fn = function() CasinoEmote:PlayText("CHEER") end },
      },
    },
    LUCKY_NIGHT = {
      name = "Lucky Night",
      weight = 10,
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("CURTAIN"); CasinoEmote:PlayText("FLIPS_COIN") end },
        { delay = 2,   fn = function() SendChatMessage("ðŸ€ Tonight... the stars align.", "SAY") end },
        { delay = 3,   fn = function() SendChatMessage("ðŸ€ LUCKY 7 â€” Ends in 7 = x7", "SAY") end },
        { delay = 4.5, fn = function() SendChatMessage("ðŸŽ² DICE â€” 7 pays quadruple!", "SAY") end },
        { delay = 5.5, fn = function() SendChatMessage("ðŸŽ° NORMAL â€” 100 = JACKPOT x3", "SAY") end },
        { delay = 7,   fn = function() SendChatMessage("The stars favor the bold. /w Casinobae JOIN", "SAY") end },
        { delay = 8,   fn = function() CasinoEmote:PlayText("FLIPS_COIN") end },
      },
    },
    HIGH_RISK = {
      name = "High Risk",
      weight = 10,
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("DRAMA_PAUSE"); CasinoEmote:PlayText("GASP") end },
        { delay = 1,   fn = function() SendChatMessage("ðŸ”¥ HIGH RISK. 76+ x3. 100 x4.", "YELL") end },
        { delay = 2,   fn = function() SendChatMessage("1-75... the house keeps it ALL.", "SAY") end },
        { delay = 3.5, fn = function() SendChatMessage("Dare you? /w Casinobae JOIN", "SAY") end },
        { delay = 5,   fn = function() CasinoEmote:PlayText("GASP") end },
      },
    },
    MYSTERY = {
      name = "Mystery Dealer",
      weight = 5,
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("SUSPENSE"); CasinoEmote:PlayText("ADJUSTS_COAT") end },
        { delay = 2,   fn = function() SendChatMessage("...", "SAY") end },
        { delay = 3,   fn = function() SendChatMessage("The house has a surprise.", "SAY") end },
        { delay = 4,   fn = function() SendChatMessage("One game. One roll. Everything changes.", "SAY") end },
        { delay = 5,   fn = function() SendChatMessage("Dare to find out? /w Casinobae JOIN", "SAY") end },
      },
    },
    QUICK = {
      name = "Quick Ad",
      weight = 0, -- Manual only
      steps = {
        { delay = 0,   fn = function() CasinoSound:Play("CTA") end },
        { delay = 0.5, fn = function() SendChatMessage("ðŸŽ° Casinobae Casino OPEN â€” /w Casinobae JOIN â€” Games + /roll", "SAY") end },
      },
    },
  },
  
  -- Pick a random variant based on weights
  PickVariant = function(self)
    if not self.VARIANTS then return nil, nil end
    local total = 0
    for _, v in pairs(self.VARIANTS) do
      total = total + (v.weight or 0)
    end
    if total <= 0 then return nil, nil end
    local roll = math.random(total)
    local cumulative = 0
    for name, variant in pairs(self.VARIANTS) do
      cumulative = cumulative + (variant.weight or 0)
      if roll <= cumulative then
        return name, variant
      end
    end
    return "DEFAULT", self.VARIANTS.DEFAULT
  end,
  
  -- Start a show
  StartShow = function(self, variantName)
    -- Cooldown is now managed by DealerCooldownManager upstream
    -- This function assumes cooldown check has already passed
    
    -- Capital/zone check removed - attract works anywhere channels are available
    
    -- NOTE: keep PickVariant() as a bare call below. In Lua, `a, b = x or f()`
    -- keeps only f()'s FIRST value (compound expressions adjust to one value),
    -- which used to leave `variant` nil on every path ("No variant").
    -- Priority: manual selection first, else auto-pick from defined variants.
    local variant
    if variantName and self.VARIANTS[variantName] then
      variant = self.VARIANTS[variantName]
    else
      variantName, variant = self:PickVariant()
    end
    if not variant then return false, "No variant" end
    
    self.currentShow = {
      variant = variantName,
      variantData = variant,
      startTime = time(),
      step = 1,
    }
    self.showCooldown = time()
    -- dealerAdCooldown now managed by DealerCooldownManager
    
    -- Execute first step
    self:ExecuteStep(variant.steps[1])
    
    -- Schedule remaining steps
    for i = 2, #variant.steps do
      local step = variant.steps[i]
      C_Timer.After(step.delay, function()
        if self.currentShow and self.currentShow.variant == variantName then
          self:ExecuteStep(step)
        end
      end)
    end
    
    -- Mark show as complete after last step
    local lastStep = variant.steps[#variant.steps]
    C_Timer.After(lastStep.delay + 2, function()
      self.currentShow = nil
    end)
    
    DealerAuditLog(nil, nil, "CAPITAL_SHOW", variantName)
    return true, variantName
  end,
  
  ExecuteStep = function(self, step)
    if step.fn then step.fn() end
  end,
  
  -- Quick ad (manual trigger)
  QuickAd = function(self)
    local variant = self.VARIANTS.QUICK
    for i, step in ipairs(variant.steps) do
      C_Timer.After(step.delay, function() step.fn() end)
    end
    -- Quick ad doesn't trigger show cooldown
    DealerAuditLog(nil, nil, "QUICK_AD", "Quick advertisement")
    return true
  end,
  
  StopShow = function(self)
    self.currentShow = nil
    print("|cffFFD700Casinobabe|r Show stopped.")
  end,
  
  IsShowing = function(self)
    return self.currentShow ~= nil
  end,
  
  GetStatus = function(self)
    if self.currentShow then
      return "SHOWING: " .. self.currentShow.variant
    else
      local rem = DealerCooldownManager:Remaining("SHOW")
      if rem > 0 then
        return string.format("COOLDOWN: %ds", rem)
      else
        return "READY"
      end
    end
  end,
}

-- ============================================================================
-- CASINOSHOW - UI Control Center for Dealer
-- ============================================================================
local CasinoShow = {
  -- Show state
  isShowing = false,
  currentVariant = nil,
  suggestedEmote = nil,
  suggestedFx = nil,
  
  -- UI state
  showPanel = nil,
  
  -- Trigger capital show from UI
  StartShow = function(self, variantName)
    local success, variant = CasinoAnnouncer:StartShow(variantName)
    if success then
      self.isShowing = true
      self.currentVariant = variant
      print("|cffFFD700Casinobabe|r Show started: " .. variant)
      self:UpdateUI()
    else
      print("|cffFFD700Casinobabe|r " .. (variant or "Failed to start show"))
    end
    return success
  end,
  
  StartQuickAd = function(self)
    CasinoAnnouncer:QuickAd()
    print("|cffFFD700Casinobabe|r Quick ad sent!")
  end,
  
  StopShow = function(self)
    CasinoAnnouncer:StopShow()
    self.isShowing = false
    self.currentVariant = nil
    self:UpdateUI()
  end,
  
  PauseShow = function(self)
    -- Not implemented for sequences
  end,
  
  ResumeShow = function(self)
  end,
  
  -- Set suggested emote from reaction engine
  SetSuggestedEmote = function(self, emote)
    self.suggestedEmote = emote
    self:UpdateUI()
  end,
  
  ClearSuggestedEmote = function(self)
    self.suggestedEmote = nil
    self:UpdateUI()
  end,
  
  SetSuggestedFx = function(self, fx)
    self.suggestedFx = fx
    self:UpdateUI()
  end,
  
  ClearSuggestedFx = function(self)
    self.suggestedFx = nil
    self:UpdateUI()
  end,
  
  -- Get available show variants
  GetVariants = function(self)
    local variants = {}
    for name, variant in pairs(CasinoAnnouncer.VARIANTS) do
      if variant.weight and variant.weight > 0 then
        table.insert(variants, { name = name, label = variant.name })
      end
    end
    return variants
  end,
  
  -- Update UI elements
  UpdateUI = function(self)
    if not self.showPanel then return end
    local panel = self.showPanel
    
    -- Update show button
    if panel.showBtn then
      if self.isShowing then
        panel.showBtn.txt:SetText("SHOW: " .. (self.currentVariant or "RUNNING"))
        panel.showBtn.bg:SetColorTexture(0.7, 0.3, 0.3, 0.95)
      else
        panel.showBtn.txt:SetText("ðŸŽ° OPEN THE CASINO")
        panel.showBtn.bg:SetColorTexture(0.8, 0.5, 0.1, 0.95)
      end
    end
    
    -- Update status text
    if panel.statusText then
      if self.isShowing then
        panel.statusText:SetText("|cffFFD700SHOW RUNNING:|r " .. (self.currentVariant or "DEFAULT"))
        panel.statusText:SetTextColor(0.8, 0.5, 0.1, 1)
      else
        local status = CasinoAnnouncer:GetStatus()
        if status:find("COOLDOWN") then
          panel.statusText:SetText("|cffEB5E4F" .. status .. "|r")
          panel.statusText:SetTextColor(1, 0.3, 0.3, 1)
        else
          panel.statusText:SetText("|cff78EB96" .. status .. "|r")
          panel.statusText:SetTextColor(0.5, 1, 0.5, 1)
        end
      end
    end
    
    -- Update suggested emote
    if panel.emoteBtn then
      if self.suggestedEmote then
        panel.emoteBtn:Show()
        panel.emoteBtn.txt:SetText("ðŸŽ­ " .. self.suggestedEmote)
        panel.emoteBtn:SetScript("OnClick", function()
          CasinoEmote:PlayPhysical(self.suggestedEmote)
          CasinoShow:ClearSuggestedEmote()
        end)
      else
        panel.emoteBtn:Hide()
      end
    end
    
    -- Update suggested FX
    if panel.fxText then
      if self.suggestedFx then
        panel.fxText:SetText("|cffFFD700FX:|r " .. self.suggestedFx)
        panel.fxText:Show()
      else
        panel.fxText:Hide()
      end
    end
  end,
  
  -- Trigger reaction from dealer workflow
  TriggerReaction = function(self, event, context)
    context = context or {}
    context.suggestedEmote = nil
    context.suggestedFx = nil
    CasinoReactionEngine:Trigger(event, context)
    if context.suggestedEmote then self:SetSuggestedEmote(context.suggestedEmote) end
    if context.suggestedFx then self:SetSuggestedFx(context.suggestedFx) end
    if context.triggerShow then self:StartShow() end
  end,
  
  -- Quick ad
  QuickAd = function(self)
    CasinoAnnouncer:QuickAd()
  end,
  
  -- Get show status
  GetStatus = function(self)
    return CasinoAnnouncer:GetStatus()
  end,
}

-- ============================================================================
-- Helper: Get game rule by key
-- ============================================================================
local function GetGameRule(key)
  return GameRules[key:lower()]
end

-- Helper: Compute payout (stake * (multiplier - 1)) for wins, -stake for loss, 0 for push
local function ComputePayout(gameKey, stake, ...)
  local rule = GetGameRule(gameKey)
  if not rule then return nil, "ERR_INVALID_GAME" end
  local mult = rule.resolve(...)
  if not mult then return nil, "ERR_RESOLVE_FAILED" end
  if mult == 0 then return -stake end           -- loss
  if gameKey == "blackjack" then
    if mult == 1 then return stake end          -- blackjack win = x2 (net +stake)
    if mult == 2 then return 0 end              -- push = stake returned (net 0)
  end
  -- For other games: mult is the multiplier (2, 3, 4, 5, 7)
  -- Net payout = stake * (multiplier - 1)
  return stake * (mult - 1)
end

-- Helper: Validate stake amount
-- NOTE: gameKey may be nil when the stake is armed before any game is
-- selected (documented INVITED -> TRADE_PENDING flow). In that case validate
-- against the union of all games' bounds instead of crashing on nil:lower().
local function ValidateStake(gameKey, amount)
  if type(amount) ~= "number" then return false, "ERR_BAD_STAKE" end
  local lo, hi
  if gameKey then
    local rule = GetGameRule(gameKey)
    if not rule then return false, "ERR_INVALID_GAME" end
    lo, hi = rule.stakeMin, rule.stakeMax
  else
    for _, r in pairs(GameRules) do
      if r.stakeMin and r.stakeMax then
        if not lo or r.stakeMin < lo then lo = r.stakeMin end
        if not hi or r.stakeMax > hi then hi = r.stakeMax end
      end
    end
    if not lo then return false, "ERR_INVALID_GAME" end
  end
  if amount < lo or amount > hi then
    return false, "ERR_BAD_STAKE"
  end
  return true
end

-- Helper: Validate roll range
local function ValidateRoll(gameKey, roll)
  local rule = GetGameRule(gameKey)
  if not rule then return false, "ERR_INVALID_GAME" end
  if roll < rule.rollMin or roll > rule.rollMax then
    return false, "ERR_ROLL_OUT_OF_RANGE"
  end
  return true
end

-- Helper: Validate roulette color
local function ValidateRouletteColor(color)
  local rule = GetGameRule("roulette")
  if not rule then return false end
  local c = (color or ""):lower()
  for _, v in ipairs(rule.colors) do
    if v == c then return true end
  end
  return false
end

-- Helper: Validate dice choice
local function ValidateDiceChoice(choice)
  local rule = GetGameRule("dice")
  if not rule then return false end
  local c = (choice or ""):lower()
  for _, v in ipairs(rule.choices) do
    if v == c then return true end
  end
  return false
end

local LANGS = {
  { "English","english" }, { "Svenska","swedish" }, { "Deutsch","deutsch" },
  { "Francais","francais" }, { "Espanol","espanol" }, { "Italiano","italiano" },
  { "Portugues","portugues" }, { "Russian","russian" }, { "Ukrainian","ukrainian" },
  { "Bulgarian","bulgarian" }, { "Greek","greek" },
}

local function betCommand(gameKey, amt, color)
  if gameKey=="roulette" then return (color or "red").." "..amt end
  if gameKey=="blackjack" then return "bj "..amt end
  return gameKey.." "..amt
end

-- ===== how to play / games guide text (sprakberoende) =====
local HOWTO_BY_LANG = {
  english = "|cffFFD700How to play Casinobabe|r\n\n|cffF0C95A1.|r Join the casino's |cffFFFFFFraid group|r (you must be in it to play).\n|cffF0C95A2.|r Open this panel with the gold |cffFFFFFFC|r on your minimap (or /cb).\n|cffF0C95A3.|r |cffFFFFFFDeposit:|r toggle Block Trades OFF, trade the casino your gold - it lands on your balance.\n|cffF0C95A4.|r Pick a game, pick a bet amount, click |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r A pop-up shows in the middle - just follow it: press |cffFFFFFFROLL|r, or |cffFFFFFFHit/Stand|r, or pick |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Wins are added to your balance automatically; losses are already taken.\n|cffF0C95A7.|r Hit |cffFFFFFFCollect|r when the green cashback button lights up.\n|cffF0C95A8.|r |cffFFFFFFCash out:|r toggle Block Trades OFF and trade the casino - they send your balance back.\n\n|cff9AE6A0Tip:|r keep Block Trades ON while playing so nobody can scam-trade you.",
  swedish = "|cffFFD700Sa spelar du Casinobabe|r\n\n|cffF0C95A1.|r Ga med i casinots |cffFFFFFFraid-grupp|r (du maste vara med for att spela).\n|cffF0C95A2.|r Oppna panelen med guld-|cffFFFFFFC|r pa minimappen (eller /cb).\n|cffF0C95A3.|r |cffFFFFFFSatt in:|r stang av Block Trades, tradea casinot ditt guld - det hamnar pa ditt saldo.\n|cffF0C95A4.|r Valj ett spel, valj insats, klicka |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r En ruta dyker upp i mitten - folj den: tryck |cffFFFFFFROLL|r, eller |cffFFFFFFHit/Stand|r, eller valj |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Vinster laggs till pa saldot automatiskt; forluster ar redan dragna.\n|cffF0C95A7.|r Tryck |cffFFFFFFCollect|r nar den grona cashback-knappen lyser.\n|cffF0C95A8.|r |cffFFFFFFTa ut:|r stang av Block Trades och tradea casinot - de skickar tillbaka ditt saldo.\n\n|cff9AE6A0Tips:|r ha Block Trades PA medan du spelar sa ingen kan scam-tradea dig.",
  deutsch = "|cffFFD700So spielst du Casinobabe|r\n\n|cffF0C95A1.|r Tritt der |cffFFFFFFRaid-Gruppe|r des Casinos bei (nur dann kannst du spielen).\n|cffF0C95A2.|r Offne das Panel mit dem goldenen |cffFFFFFFC|r auf der Minikarte (oder /cb).\n|cffF0C95A3.|r |cffFFFFFFEinzahlen:|r Block Trades AUS, handle dem Casino dein Gold - es landet auf deinem Guthaben.\n|cffF0C95A4.|r Wahle ein Spiel, einen Einsatz, klicke |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Ein Fenster erscheint in der Mitte - folge ihm: druck |cffFFFFFFROLL|r, oder |cffFFFFFFHit/Stand|r, oder wahle |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Gewinne werden automatisch gutgeschrieben; Verluste sind bereits abgezogen.\n|cffF0C95A7.|r Druck |cffFFFFFFCollect|r wenn der grune Cashback-Knopf leuchtet.\n|cffF0C95A8.|r |cffFFFFFFAuszahlen:|r Block Trades AUS und handle dem Casino - sie senden dein Guthaben zuruck.\n\n|cff9AE6A0Tipp:|r lass Block Trades AN beim Spielen, damit dich niemand per Trade betrugt.",
  francais = "|cffFFD700Comment jouer a Casinobabe|r\n\n|cffF0C95A1.|r Rejoins le |cffFFFFFFgroupe raid|r du casino (obligatoire pour jouer).\n|cffF0C95A2.|r Ouvre ce panneau avec le |cffFFFFFFC|r dore sur ta minicarte (ou /cb).\n|cffF0C95A3.|r |cffFFFFFFDepot:|r desactive Block Trades, echange ton or au casino - il arrive sur ton solde.\n|cffF0C95A4.|r Choisis un jeu, une mise, clique |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Une fenetre apparait au centre - suis-la: appuie sur |cffFFFFFFROLL|r, ou |cffFFFFFFHit/Stand|r, ou choisis |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Les gains sont ajoutes automatiquement; les pertes sont deja prises.\n|cffF0C95A7.|r Appuie sur |cffFFFFFFCollect|r quand le bouton cashback vert s'allume.\n|cffF0C95A8.|r |cffFFFFFFRetrait:|r desactive Block Trades et echange avec le casino - il renvoie ton solde.\n\n|cff9AE6A0Astuce:|r garde Block Trades ACTIVE en jouant pour eviter les arnaques au trade.",
  espanol = "|cffFFD700Como jugar a Casinobabe|r\n\n|cffF0C95A1.|r Unete al |cffFFFFFFgrupo de banda|r del casino (necesario para jugar).\n|cffF0C95A2.|r Abre este panel con la |cffFFFFFFC|r dorada del minimapa (o /cb).\n|cffF0C95A3.|r |cffFFFFFFDeposito:|r desactiva Block Trades, intercambia tu oro al casino - va a tu saldo.\n|cffF0C95A4.|r Elige un juego, una apuesta, pulsa |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Aparece una ventana en el centro - siguela: pulsa |cffFFFFFFROLL|r, o |cffFFFFFFHit/Stand|r, o elige |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Las ganancias se anaden automaticamente; las perdidas ya estan descontadas.\n|cffF0C95A7.|r Pulsa |cffFFFFFFCollect|r cuando el boton verde de cashback se ilumine.\n|cffF0C95A8.|r |cffFFFFFFRetirar:|r desactiva Block Trades e intercambia con el casino - te devuelven tu saldo.\n\n|cff9AE6A0Consejo:|r manten Block Trades ACTIVADO al jugar para que nadie te estafe en el trade.",
  italiano = "|cffFFD700Come giocare a Casinobabe|r\n\n|cffF0C95A1.|r Unisciti al |cffFFFFFFgruppo raid|r del casino (necessario per giocare).\n|cffF0C95A2.|r Apri il pannello con la |cffFFFFFFC|r dorata sulla minimappa (o /cb).\n|cffF0C95A3.|r |cffFFFFFFDeposito:|r disattiva Block Trades, scambia il tuo oro al casino - finisce nel saldo.\n|cffF0C95A4.|r Scegli un gioco, una puntata, clicca |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Appare una finestra al centro - seguila: premi |cffFFFFFFROLL|r, o |cffFFFFFFHit/Stand|r, o scegli |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Le vincite sono aggiunte automaticamente; le perdite sono gia detratte.\n|cffF0C95A7.|r Premi |cffFFFFFFCollect|r quando il pulsante verde cashback si illumina.\n|cffF0C95A8.|r |cffFFFFFFPreleva:|r disattiva Block Trades e scambia col casino - ti rimandano il saldo.\n\n|cff9AE6A0Consiglio:|r tieni Block Trades ATTIVO mentre giochi cosi nessuno ti truffa con lo scambio.",
  portugues = "|cffFFD700Como jogar Casinobabe|r\n\n|cffF0C95A1.|r Entre no |cffFFFFFFgrupo de raide|r do casino (necessario para jogar).\n|cffF0C95A2.|r Abra o painel com o |cffFFFFFFC|r dourado no minimapa (ou /cb).\n|cffF0C95A3.|r |cffFFFFFFDeposito:|r desative Block Trades, troque seu ouro ao casino - vai para o seu saldo.\n|cffF0C95A4.|r Escolha um jogo, uma aposta, clique |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Uma janela aparece no centro - siga-a: aperte |cffFFFFFFROLL|r, ou |cffFFFFFFHit/Stand|r, ou escolha |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Ganhos sao somados automaticamente; perdas ja foram descontadas.\n|cffF0C95A7.|r Aperte |cffFFFFFFCollect|r quando o botao verde de cashback acender.\n|cffF0C95A8.|r |cffFFFFFFSacar:|r desative Block Trades e troque com o casino - eles devolvem seu saldo.\n\n|cff9AE6A0Dica:|r mantenha Block Trades LIGADO ao jogar para ninguem te golpear na troca.",
  russian = "|cffFFD700ÐšÐ°Ðº Ð¸Ð³Ñ€Ð°Ñ‚ÑŒ Ð² Casinobabe|r\n\n|cffF0C95A1.|r Ð’ÑÑ‚ÑƒÐ¿Ð¸ Ð² |cffFFFFFFÑ€ÐµÐ¹Ð´-Ð³Ñ€ÑƒÐ¿Ð¿Ñƒ|r ÐºÐ°Ð·Ð¸Ð½Ð¾ (Ð±ÐµÐ· ÑÑ‚Ð¾Ð³Ð¾ Ð¸Ð³Ñ€Ð°Ñ‚ÑŒ Ð½ÐµÐ»ÑŒÐ·Ñ).\n|cffF0C95A2.|r ÐžÑ‚ÐºÑ€Ð¾Ð¹ Ð¿Ð°Ð½ÐµÐ»ÑŒ Ð·Ð¾Ð»Ð¾Ñ‚Ð¾Ð¹ |cffFFFFFFC|r Ð½Ð° Ð¼Ð¸Ð½Ð¸ÐºÐ°Ñ€Ñ‚Ðµ (Ð¸Ð»Ð¸ /cb).\n|cffF0C95A3.|r |cffFFFFFFÐ”ÐµÐ¿Ð¾Ð·Ð¸Ñ‚:|r Ð²Ñ‹ÐºÐ»ÑŽÑ‡Ð¸ Block Trades, Ð¿ÐµÑ€ÐµÐ´Ð°Ð¹ ÐºÐ°Ð·Ð¸Ð½Ð¾ Ð·Ð¾Ð»Ð¾Ñ‚Ð¾ - Ð¾Ð½Ð¾ Ð¿Ð¾Ð¿Ð°Ð´Ñ‘Ñ‚ Ð½Ð° Ñ‚Ð²Ð¾Ð¹ Ð±Ð°Ð»Ð°Ð½Ñ.\n|cffF0C95A4.|r Ð’Ñ‹Ð±ÐµÑ€Ð¸ Ð¸Ð³Ñ€Ñƒ Ð¸ ÑÑ‚Ð°Ð²ÐºÑƒ, Ð½Ð°Ð¶Ð¼Ð¸ |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Ð’ Ñ†ÐµÐ½Ñ‚Ñ€Ðµ Ð¿Ð¾ÑÐ²Ð¸Ñ‚ÑÑ Ð¾ÐºÐ½Ð¾ - ÑÐ»ÐµÐ´ÑƒÐ¹ ÐµÐ¼Ñƒ: Ð½Ð°Ð¶Ð¼Ð¸ |cffFFFFFFROLL|r, Ð¸Ð»Ð¸ |cffFFFFFFHit/Stand|r, Ð¸Ð»Ð¸ Ð²Ñ‹Ð±ÐµÑ€Ð¸ |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Ð’Ñ‹Ð¸Ð³Ñ€Ñ‹ÑˆÐ¸ Ð´Ð¾Ð±Ð°Ð²Ð»ÑÑŽÑ‚ÑÑ Ð½Ð° Ð±Ð°Ð»Ð°Ð½Ñ Ð°Ð²Ñ‚Ð¾Ð¼Ð°Ñ‚Ð¸Ñ‡ÐµÑÐºÐ¸; Ð¿Ñ€Ð¾Ð¸Ð³Ñ€Ñ‹ÑˆÐ¸ ÑƒÐ¶Ðµ ÑÐ¿Ð¸ÑÐ°Ð½Ñ‹.\n|cffF0C95A7.|r ÐÐ°Ð¶Ð¼Ð¸ |cffFFFFFFCollect|r ÐºÐ¾Ð³Ð´Ð° Ð·Ð°Ð³Ð¾Ñ€Ð¸Ñ‚ÑÑ Ð·ÐµÐ»Ñ‘Ð½Ð°Ñ ÐºÐ½Ð¾Ð¿ÐºÐ° ÐºÑÑˆÐ±ÑÐºÐ°.\n|cffF0C95A8.|r |cffFFFFFFÐ’Ñ‹Ð²Ð¾Ð´:|r Ð²Ñ‹ÐºÐ»ÑŽÑ‡Ð¸ Block Trades Ð¸ Ð¾Ð±Ð¼ÐµÐ½ÑÐ¹ÑÑ Ñ ÐºÐ°Ð·Ð¸Ð½Ð¾ - Ñ‚ÐµÐ±Ðµ Ð²ÐµÑ€Ð½ÑƒÑ‚ Ð±Ð°Ð»Ð°Ð½Ñ.\n\n|cff9AE6A0Ð¡Ð¾Ð²ÐµÑ‚:|r Ð´ÐµÑ€Ð¶Ð¸ Block Trades Ð’ÐšÐ› Ð²Ð¾ Ð²Ñ€ÐµÐ¼Ñ Ð¸Ð³Ñ€Ñ‹, Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ñ‚ÐµÐ±Ñ Ð½Ðµ Ð¾Ð±Ð¼Ð°Ð½ÑƒÐ»Ð¸ Ñ‡ÐµÑ€ÐµÐ· Ð¾Ð±Ð¼ÐµÐ½.",
  ukrainian = "|cffFFD700Ð¯Ðº Ð³Ñ€Ð°Ñ‚Ð¸ Ð² Casinobabe|r\n\n|cffF0C95A1.|r ÐŸÑ€Ð¸Ñ”Ð´Ð½Ð°Ð¹ÑÑ Ð´Ð¾ |cffFFFFFFÑ€ÐµÐ¹Ð´-Ð³Ñ€ÑƒÐ¿Ð¸|r ÐºÐ°Ð·Ð¸Ð½Ð¾ (Ð±ÐµÐ· Ñ†ÑŒÐ¾Ð³Ð¾ Ð³Ñ€Ð°Ñ‚Ð¸ Ð½Ðµ Ð¼Ð¾Ð¶Ð½Ð°).\n|cffF0C95A2.|r Ð’Ñ–Ð´ÐºÑ€Ð¸Ð¹ Ð¿Ð°Ð½ÐµÐ»ÑŒ Ð·Ð¾Ð»Ð¾Ñ‚Ð¾ÑŽ |cffFFFFFFC|r Ð½Ð° Ð¼Ñ–Ð½Ñ–ÐºÐ°Ñ€Ñ‚Ñ– (Ð°Ð±Ð¾ /cb).\n|cffF0C95A3.|r |cffFFFFFFÐ”ÐµÐ¿Ð¾Ð·Ð¸Ñ‚:|r Ð²Ð¸Ð¼ÐºÐ½Ð¸ Block Trades, Ð¿ÐµÑ€ÐµÐ´Ð°Ð¹ ÐºÐ°Ð·Ð¸Ð½Ð¾ Ð·Ð¾Ð»Ð¾Ñ‚Ð¾ - Ð²Ð¾Ð½Ð¾ Ð¿Ð¾Ñ‚Ñ€Ð°Ð¿Ð¸Ñ‚ÑŒ Ð½Ð° Ñ‚Ð²Ñ–Ð¹ Ð±Ð°Ð»Ð°Ð½Ñ.\n|cffF0C95A4.|r ÐžÐ±ÐµÑ€Ð¸ Ð³Ñ€Ñƒ Ñ‚Ð° ÑÑ‚Ð°Ð²ÐºÑƒ, Ð½Ð°Ñ‚Ð¸ÑÐ½Ð¸ |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Ð£ Ñ†ÐµÐ½Ñ‚Ñ€Ñ– Ð·'ÑÐ²Ð¸Ñ‚ÑŒÑÑ Ð²Ñ–ÐºÐ½Ð¾ - Ð´Ñ–Ð¹ Ð·Ð° Ð½Ð¸Ð¼: Ð½Ð°Ñ‚Ð¸ÑÐ½Ð¸ |cffFFFFFFROLL|r, Ð°Ð±Ð¾ |cffFFFFFFHit/Stand|r, Ð°Ð±Ð¾ Ð¾Ð±ÐµÑ€Ð¸ |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Ð’Ð¸Ð³Ñ€Ð°ÑˆÑ– Ð´Ð¾Ð´Ð°ÑŽÑ‚ÑŒÑÑ Ð½Ð° Ð±Ð°Ð»Ð°Ð½Ñ Ð°Ð²Ñ‚Ð¾Ð¼Ð°Ñ‚Ð¸Ñ‡Ð½Ð¾; Ð¿Ñ€Ð¾Ð³Ñ€Ð°ÑˆÑ– Ð²Ð¶Ðµ ÑÐ¿Ð¸ÑÐ°Ð½Ñ–.\n|cffF0C95A7.|r ÐÐ°Ñ‚Ð¸ÑÐ½Ð¸ |cffFFFFFFCollect|r ÐºÐ¾Ð»Ð¸ Ð·Ð°ÑÐ²Ñ–Ñ‚Ð¸Ñ‚ÑŒÑÑ Ð·ÐµÐ»ÐµÐ½Ð° ÐºÐ½Ð¾Ð¿ÐºÐ° ÐºÐµÑˆÐ±ÐµÐºÑƒ.\n|cffF0C95A8.|r |cffFFFFFFÐ’Ð¸Ð²ÐµÐ´ÐµÐ½Ð½Ñ:|r Ð²Ð¸Ð¼ÐºÐ½Ð¸ Block Trades Ñ‚Ð° Ð¾Ð±Ð¼Ñ–Ð½ÑÐ¹ÑÑ Ð· ÐºÐ°Ð·Ð¸Ð½Ð¾ - Ñ‚Ð¾Ð±Ñ– Ð¿Ð¾Ð²ÐµÑ€Ð½ÑƒÑ‚ÑŒ Ð±Ð°Ð»Ð°Ð½Ñ.\n\n|cff9AE6A0ÐŸÐ¾Ñ€Ð°Ð´Ð°:|r Ñ‚Ñ€Ð¸Ð¼Ð°Ð¹ Block Trades Ð£Ð’Ð†ÐœÐšÐÐ•ÐÐ˜Ðœ Ð¿Ñ–Ð´ Ñ‡Ð°Ñ Ð³Ñ€Ð¸, Ñ‰Ð¾Ð± Ñ‚ÐµÐ±Ðµ Ð½Ðµ Ð¾Ð±Ð´ÑƒÑ€Ð¸Ð»Ð¸ Ñ‡ÐµÑ€ÐµÐ· Ð¾Ð±Ð¼Ñ–Ð½.",
  bulgarian = "|cffFFD700ÐšÐ°Ðº ÑÐµ Ð¸Ð³Ñ€Ð°Ðµ Casinobabe|r\n\n|cffF0C95A1.|r Ð’Ð»ÐµÐ· Ð² |cffFFFFFFÑ€ÐµÐ¹Ð´ Ð³Ñ€ÑƒÐ¿Ð°Ñ‚Ð°|r Ð½Ð° ÐºÐ°Ð·Ð¸Ð½Ð¾Ñ‚Ð¾ (Ð·Ð°Ð´ÑŠÐ»Ð¶Ð¸Ñ‚ÐµÐ»Ð½Ð¾ Ð·Ð° Ð¸Ð³Ñ€Ð°).\n|cffF0C95A2.|r ÐžÑ‚Ð²Ð¾Ñ€Ð¸ Ð¿Ð°Ð½ÐµÐ»Ð° ÑÑŠÑ Ð·Ð»Ð°Ñ‚Ð½Ð¾Ñ‚Ð¾ |cffFFFFFFC|r Ð½Ð° Ð¼Ð¸Ð½Ð¸ÐºÐ°Ñ€Ñ‚Ð°Ñ‚Ð° (Ð¸Ð»Ð¸ /cb).\n|cffF0C95A3.|r |cffFFFFFFÐ”ÐµÐ¿Ð¾Ð·Ð¸Ñ‚:|r Ð¸Ð·ÐºÐ»ÑŽÑ‡Ð¸ Block Trades, Ñ€Ð°Ð·Ð¼ÐµÐ½Ð¸ Ð·Ð»Ð°Ñ‚Ð¾ Ñ ÐºÐ°Ð·Ð¸Ð½Ð¾Ñ‚Ð¾ - Ð²Ð»Ð¸Ð·Ð° Ð² Ð±Ð°Ð»Ð°Ð½ÑÐ° Ñ‚Ð¸.\n|cffF0C95A4.|r Ð˜Ð·Ð±ÐµÑ€Ð¸ Ð¸Ð³Ñ€Ð° Ð¸ Ð·Ð°Ð»Ð¾Ð³, Ð½Ð°Ñ‚Ð¸ÑÐ½Ð¸ |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Ð’ Ñ†ÐµÐ½Ñ‚ÑŠÑ€Ð° Ð¸Ð·Ð»Ð¸Ð·Ð° Ð¿Ñ€Ð¾Ð·Ð¾Ñ€ÐµÑ† - ÑÐ»ÐµÐ´Ð²Ð°Ð¹ Ð³Ð¾: Ð½Ð°Ñ‚Ð¸ÑÐ½Ð¸ |cffFFFFFFROLL|r, Ð¸Ð»Ð¸ |cffFFFFFFHit/Stand|r, Ð¸Ð»Ð¸ Ð¸Ð·Ð±ÐµÑ€Ð¸ |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r ÐŸÐµÑ‡Ð°Ð»Ð±Ð¸Ñ‚Ðµ ÑÐµ Ð´Ð¾Ð±Ð°Ð²ÑÑ‚ ÐºÑŠÐ¼ Ð±Ð°Ð»Ð°Ð½ÑÐ° Ð°Ð²Ñ‚Ð¾Ð¼Ð°Ñ‚Ð¸Ñ‡Ð½Ð¾; Ð·Ð°Ð³ÑƒÐ±Ð¸Ñ‚Ðµ Ð²ÐµÑ‡Ðµ ÑÐ° ÑƒÐ´ÑŠÑ€Ð¶Ð°Ð½Ð¸.\n|cffF0C95A7.|r ÐÐ°Ñ‚Ð¸ÑÐ½Ð¸ |cffFFFFFFCollect|r ÐºÐ¾Ð³Ð°Ñ‚Ð¾ Ð·ÐµÐ»ÐµÐ½Ð¸ÑÑ‚ ÐºÐµÑˆÐ±ÐµÐº Ð±ÑƒÑ‚Ð¾Ð½ ÑÐ²ÐµÑ‚Ð½Ðµ.\n|cffF0C95A8.|r |cffFFFFFFÐ¢ÐµÐ³Ð»ÐµÐ½Ðµ:|r Ð¸Ð·ÐºÐ»ÑŽÑ‡Ð¸ Block Trades Ð¸ Ñ€Ð°Ð·Ð¼ÐµÐ½Ð¸ Ñ ÐºÐ°Ð·Ð¸Ð½Ð¾Ñ‚Ð¾ - Ð²Ñ€ÑŠÑ‰Ð°Ñ‚ Ñ‚Ð¸ Ð±Ð°Ð»Ð°Ð½ÑÐ°.\n\n|cff9AE6A0Ð¡ÑŠÐ²ÐµÑ‚:|r Ð´Ñ€ÑŠÐ¶ Block Trades Ð’ÐšÐ› Ð´Ð¾ÐºÐ°Ñ‚Ð¾ Ð¸Ð³Ñ€Ð°ÐµÑˆ, Ð·Ð° Ð´Ð° Ð½Ðµ Ñ‚Ðµ Ð¸Ð·Ð¼Ð°Ð¼ÑÑ‚ Ð¿Ñ€Ð¸ Ñ€Ð°Ð·Ð¼ÑÐ½Ð°.",
  greek = "|cffFFD700Î Ï‰Ï‚ Î½Î± Ï€Î±Î¹Î¾ÎµÎ¹Ï‚ Casinobabe|r\n\n|cffF0C95A1.|r ÎœÏ€ÎµÏ‚ ÏƒÏ„Î¿ |cffFFFFFFraid group|r Ï„Î¿Ï… ÎºÎ±Î¶Î¹Î½Î¿ (Î±Ï€Î±ÏÎ±Î¹Ï„Î·Ï„Î¿ Î³Î¹Î± Î½Î± Ï€Î±Î¹Î¾ÎµÎ¹Ï‚).\n|cffF0C95A2.|r Î‘Î½Î¿Î¹Î¾Îµ Ï„Î¿ Ï€Î±Î½ÎµÎ» Î¼Îµ Ï„Î¿ Ï‡ÏÏ…ÏƒÎ¿ |cffFFFFFFC|r ÏƒÏ„Î¿Î½ Î¼Î¹Î½Î¹-Ï‡Î±ÏÏ„Î· (Î· /cb).\n|cffF0C95A3.|r |cffFFFFFFÎšÎ±Ï„Î±Î¸ÎµÏƒÎ·:|r ÎºÎ»ÎµÎ¹ÏƒÎµ Ï„Î¿ Block Trades, Î±Î½Ï„Î±Î»Î»Î±Î¾Îµ Ï‡ÏÏ…ÏƒÎ¿ Î¼Îµ Ï„Î¿ ÎºÎ±Î¶Î¹Î½Î¿ - Ï€Î±ÎµÎ¹ ÏƒÏ„Î¿ Ï…Ï€Î¿Î»Î¿Î¹Ï€Î¿ ÏƒÎ¿Ï….\n|cffF0C95A4.|r Î”Î¹Î±Î»ÎµÎ¾Îµ Ï€Î±Î¹Ï‡Î½Î¹Î´Î¹ ÎºÎ±Î¹ Ï€Î¿ÏƒÎ¿, Ï€Î±Ï„Î± |cffFFFFFFPLACE BET|r.\n|cffF0C95A5.|r Î•Î½Î± Ï€Î±ÏÎ±Î¸Ï…ÏÎ¿ ÎµÎ¼Ï†Î±Î½Î¹Î¶ÎµÏ„Î±Î¹ ÏƒÏ„Î¿ ÎºÎµÎ½Ï„ÏÎ¿ - Î±ÎºÎ¿Î»Î¿Ï…Î¸Î·ÏƒÎµ Ï„Î¿: Ï€Î±Ï„Î± |cffFFFFFFROLL|r, Î· |cffFFFFFFHit/Stand|r, Î· Î´Î¹Î±Î»ÎµÎ¾Îµ |cffFFFFFFOver/Under/Lucky 7|r.\n|cffF0C95A6.|r Î¤Î± ÎºÎµÏÎ´Î· Ï€ÏÎ¿ÏƒÏ„Î¹Î¸ÎµÎ½Ï„Î±Î¹ Î±Ï…Ï„Î¿Î¼Î±Ï„Î±; Î¿Î¹ Î±Ï€Ï‰Î»ÎµÎ¹ÎµÏ‚ ÎµÏ‡Î¿Ï…Î½ Î·Î´Î· Î±Ï†Î±Î¹ÏÎµÎ¸ÎµÎ¹.\n|cffF0C95A7.|r Î Î±Ï„Î± |cffFFFFFFCollect|r Î¿Ï„Î±Î½ Î±Î½Î±ÏˆÎµÎ¹ Ï„Î¿ Ï€ÏÎ±ÏƒÎ¹Î½Î¿ ÎºÎ¿Ï…Î¼Ï€Î¹ cashback.\n|cffF0C95A8.|r |cffFFFFFFÎ‘Î½Î±Î»Î·ÏˆÎ·:|r ÎºÎ»ÎµÎ¹ÏƒÎµ Ï„Î¿ Block Trades ÎºÎ±Î¹ Î±Î½Ï„Î±Î»Î»Î±Î¾Îµ Î¼Îµ Ï„Î¿ ÎºÎ±Î¶Î¹Î½Î¿ - ÏƒÎ¿Ï… ÎµÏ€Î¹ÏƒÏ„ÏÎµÏ†Î¿Ï…Î½ Ï„Î¿ Ï…Ï€Î¿Î»Î¿Î¹Ï€Î¿.\n\n|cff9AE6A0Î£Ï…Î¼Î²Î¿Ï…Î»Î·:|r ÎºÏÎ±Ï„Î± Ï„Î¿ Block Trades Î‘ÎÎŸÎ™Î§Î¤ÎŸ Î¿ÏƒÎ¿ Ï€Î±Î¹Î¶ÎµÎ¹Ï‚ Î³Î¹Î± Î½Î± Î¼Î· ÏƒÎµ ÎµÎ¾Î±Ï€Î±Ï„Î·ÏƒÎ¿Ï…Î½ ÏƒÏ„Î¿ trade.",
}
local GUIDE_BY_LANG = {
  english = "|cffFFD700Games guide|r   |cffB3ABA6(bets 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpot.  (1-75 lose)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Roll ENDING in 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  get closest to 100 without busting.  |cffFFFFFFHit|r to draw, |cffFFFFFFStand|r to stop.  Beat the house.\n\n|cffF0C95ADice|r  -  house rolls 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  exactly 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  pick a color, then /roll 1-100.  Red 1-45 = |cffFFFFFFx2|r,  Green 46-55 = |cffFFFFFFx5|r,  Black 56-100 = |cffFFFFFFx2|r.",
  swedish = "|cffFFD700Spelguide|r   |cffB3ABA6(insats 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpott.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpott.  (1-75 forlorar)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Rull som SLUTAR pa 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  kom sa nara 100 som mojligt utan att spricka.  |cffFFFFFFHit|r for kort, |cffFFFFFFStand|r for att stanna.  Sla huset.\n\n|cffF0C95ADice|r  -  huset rullar 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  exakt 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  valj farg, sen /roll 1-100.  Rod 1-45 = |cffFFFFFFx2|r,  Gron 46-55 = |cffFFFFFFx5|r,  Svart 56-100 = |cffFFFFFFx2|r.",
  deutsch = "|cffFFD700Spiele-Guide|r   |cffB3ABA6(Einsatz 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r Jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r Jackpot.  (1-75 verloren)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Wurf der auf 7 ENDET (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  komm so nah wie moglich an 100 ohne zu uberkaufen.  |cffFFFFFFHit|r ziehen, |cffFFFFFFStand|r stoppen.  Schlag das Haus.\n\n|cffF0C95ADice|r  -  Haus wurfelt 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  genau 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  wahle eine Farbe, dann /roll 1-100.  Rot 1-45 = |cffFFFFFFx2|r,  Grun 46-55 = |cffFFFFFFx5|r,  Schwarz 56-100 = |cffFFFFFFx2|r.",
  francais = "|cffFFD700Guide des jeux|r   |cffB3ABA6(mises 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpot.  (1-75 perdu)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Jet SE TERMINANT par 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  approche-toi de 100 sans depasser.  |cffFFFFFFHit|r pour tirer, |cffFFFFFFStand|r pour rester.  Bats la maison.\n\n|cffF0C95ADice|r  -  la maison lance 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  exactement 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  choisis une couleur, puis /roll 1-100.  Rouge 1-45 = |cffFFFFFFx2|r,  Vert 46-55 = |cffFFFFFFx5|r,  Noir 56-100 = |cffFFFFFFx2|r.",
  espanol = "|cffFFD700Guia de juegos|r   |cffB3ABA6(apuestas 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpot.  (1-75 pierdes)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Tirada que TERMINA en 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  acercate a 100 sin pasarte.  |cffFFFFFFHit|r para pedir, |cffFFFFFFStand|r para plantarte.  Gana a la banca.\n\n|cffF0C95ADice|r  -  la banca tira 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  exactamente 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  elige un color, luego /roll 1-100.  Rojo 1-45 = |cffFFFFFFx2|r,  Verde 46-55 = |cffFFFFFFx5|r,  Negro 56-100 = |cffFFFFFFx2|r.",
  italiano = "|cffFFD700Guida ai giochi|r   |cffB3ABA6(puntate 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpot.  (1-75 perdi)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Tiro che FINISCE con 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  avvicinati a 100 senza sballare.  |cffFFFFFFHit|r per pescare, |cffFFFFFFStand|r per fermarti.  Batti il banco.\n\n|cffF0C95ADice|r  -  il banco tira 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  esattamente 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  scegli un colore, poi /roll 1-100.  Rosso 1-45 = |cffFFFFFFx2|r,  Verde 46-55 = |cffFFFFFFx5|r,  Nero 56-100 = |cffFFFFFFx2|r.",
  portugues = "|cffFFD700Guia de jogos|r   |cffB3ABA6(apostas 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpot.  (1-75 perde)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Rolagem que TERMINA em 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  chegue perto de 100 sem estourar.  |cffFFFFFFHit|r para puxar, |cffFFFFFFStand|r para parar.  Venca a casa.\n\n|cffF0C95ADice|r  -  a casa rola 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  exatamente 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  escolha uma cor, depois /roll 1-100.  Vermelho 1-45 = |cffFFFFFFx2|r,  Verde 46-55 = |cffFFFFFFx5|r,  Preto 56-100 = |cffFFFFFFx2|r.",
  russian = "|cffFFD700Ð“Ð¸Ð´ Ð¿Ð¾ Ð¸Ð³Ñ€Ð°Ð¼|r   |cffB3ABA6(ÑÑ‚Ð°Ð²ÐºÐ¸ 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r Ð´Ð¶ÐµÐºÐ¿Ð¾Ñ‚.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r Ð´Ð¶ÐµÐºÐ¿Ð¾Ñ‚.  (1-75 Ð¿Ñ€Ð¾Ð¸Ð³Ñ€Ñ‹Ñˆ)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Ð‘Ñ€Ð¾ÑÐ¾Ðº, ÐžÐšÐÐÐ§Ð˜Ð’ÐÐ®Ð©Ð˜Ð™Ð¡Ð¯ Ð½Ð° 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  Ð½Ð°Ð±ÐµÑ€Ð¸ ÐºÐ°Ðº Ð¼Ð¾Ð¶Ð½Ð¾ Ð±Ð»Ð¸Ð¶Ðµ Ðº 100 Ð±ÐµÐ· Ð¿ÐµÑ€ÐµÐ±Ð¾Ñ€Ð°.  |cffFFFFFFHit|r - Ð²Ð·ÑÑ‚ÑŒ, |cffFFFFFFStand|r - Ð¾ÑÑ‚Ð°Ð½Ð¾Ð²Ð¸Ñ‚ÑŒÑÑ.  ÐžÐ±Ñ‹Ð³Ñ€Ð°Ð¹ ÐºÐ°Ð·Ð¸Ð½Ð¾.\n\n|cffF0C95ADice|r  -  ÐºÐ°Ð·Ð¸Ð½Ð¾ ÐºÐ¸Ð´Ð°ÐµÑ‚ 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  Ñ€Ð¾Ð²Ð½Ð¾ 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  Ð²Ñ‹Ð±ÐµÑ€Ð¸ Ñ†Ð²ÐµÑ‚, Ð·Ð°Ñ‚ÐµÐ¼ /roll 1-100.  ÐšÑ€Ð°ÑÐ½Ñ‹Ð¹ 1-45 = |cffFFFFFFx2|r,  Ð—ÐµÐ»Ñ‘Ð½Ñ‹Ð¹ 46-55 = |cffFFFFFFx5|r,  Ð§Ñ‘Ñ€Ð½Ñ‹Ð¹ 56-100 = |cffFFFFFFx2|r.",
  ukrainian = "|cffFFD700Ð“Ñ–Ð´ Ð¿Ð¾ Ñ–Ð³Ñ€Ð°Ñ…|r   |cffB3ABA6(ÑÑ‚Ð°Ð²ÐºÐ¸ 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r Ð´Ð¶ÐµÐºÐ¿Ð¾Ñ‚.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r Ð´Ð¶ÐµÐºÐ¿Ð¾Ñ‚.  (1-75 Ð¿Ñ€Ð¾Ð³Ñ€Ð°Ñˆ)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  ÐšÐ¸Ð´Ð¾Ðº, Ñ‰Ð¾ Ð—ÐÐšÐ†ÐÐ§Ð£Ð„Ð¢Ð¬Ð¡Ð¯ Ð½Ð° 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  Ð½Ð°Ð±ÐµÑ€Ð¸ ÑÐºÐ¾Ð¼Ð¾Ð³Ð° Ð±Ð»Ð¸Ð¶Ñ‡Ðµ Ð´Ð¾ 100 Ð±ÐµÐ· Ð¿ÐµÑ€ÐµÐ±Ð¾Ñ€Ñƒ.  |cffFFFFFFHit|r - Ð²Ð·ÑÑ‚Ð¸, |cffFFFFFFStand|r - Ð·ÑƒÐ¿Ð¸Ð½Ð¸Ñ‚Ð¸ÑÑ.  ÐžÐ±Ñ–Ð³Ñ€Ð°Ð¹ ÐºÐ°Ð·Ð¸Ð½Ð¾.\n\n|cffF0C95ADice|r  -  ÐºÐ°Ð·Ð¸Ð½Ð¾ ÐºÐ¸Ð´Ð°Ñ” 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  Ñ€Ñ–Ð²Ð½Ð¾ 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  Ð¾Ð±ÐµÑ€Ð¸ ÐºÐ¾Ð»Ñ–Ñ€, Ð¿Ð¾Ñ‚Ñ–Ð¼ /roll 1-100.  Ð§ÐµÑ€Ð²Ð¾Ð½Ð¸Ð¹ 1-45 = |cffFFFFFFx2|r,  Ð—ÐµÐ»ÐµÐ½Ð¸Ð¹ 46-55 = |cffFFFFFFx5|r,  Ð§Ð¾Ñ€Ð½Ð¸Ð¹ 56-100 = |cffFFFFFFx2|r.",
  bulgarian = "|cffFFD700Ð ÑŠÐºÐ¾Ð²Ð¾Ð´ÑÑ‚Ð²Ð¾ Ð·Ð° Ð¸Ð³Ñ€Ð¸Ñ‚Ðµ|r   |cffB3ABA6(Ð·Ð°Ð»Ð¾Ð·Ð¸ 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r Ð´Ð¶Ð°ÐºÐ¿Ð¾Ñ‚.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r Ð´Ð¶Ð°ÐºÐ¿Ð¾Ñ‚.  (1-75 Ð³ÑƒÐ±Ð¸Ñˆ)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Ð¥Ð²ÑŠÑ€Ð»ÑÐ½Ðµ, Ð—ÐÐ’ÐªÐ Ð¨Ð’ÐÐ©Ðž Ð½Ð° 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  Ð´Ð¾Ð±Ð»Ð¸Ð¶Ð¸ ÑÐµ Ð´Ð¾ 100 Ð±ÐµÐ· Ð´Ð° Ð½Ð°Ð´Ñ…Ð²ÑŠÑ€Ð»Ð¸Ñˆ.  |cffFFFFFFHit|r Ð·Ð° ÐºÐ°Ñ€Ñ‚Ð°, |cffFFFFFFStand|r Ð·Ð° ÑÐ¿Ð¸Ñ€Ð°Ð½Ðµ.  ÐŸÐ¾Ð±ÐµÐ´Ð¸ ÐºÐ°Ð·Ð¸Ð½Ð¾Ñ‚Ð¾.\n\n|cffF0C95ADice|r  -  ÐºÐ°Ð·Ð¸Ð½Ð¾Ñ‚Ð¾ Ñ…Ð²ÑŠÑ€Ð»Ñ 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  Ñ‚Ð¾Ñ‡Ð½Ð¾ 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  Ð¸Ð·Ð±ÐµÑ€Ð¸ Ñ†Ð²ÑÑ‚, Ð¿Ð¾ÑÐ»Ðµ /roll 1-100.  Ð§ÐµÑ€Ð²ÐµÐ½Ð¾ 1-45 = |cffFFFFFFx2|r,  Ð—ÐµÐ»ÐµÐ½Ð¾ 46-55 = |cffFFFFFFx5|r,  Ð§ÐµÑ€Ð½Ð¾ 56-100 = |cffFFFFFFx2|r.",
  greek = "|cffFFD700ÎŸÎ´Î·Î³Î¿Ï‚ Ï€Î±Î¹Ï‡Î½Î¹Î´Î¹Ï‰Î½|r   |cffB3ABA6(Ï€Î¿Î½Ï„Î± 1g - 1000g)|r\n\n|cffF0C95ANormal|r  -  /roll 1-100.  59-99 = |cffFFFFFFx2|r,  100 = |cffFFFFFFx3|r jackpot.\n\n|cffF0C95AHigh Risk|r  -  /roll 1-100.  76-99 = |cffFFFFFFx3|r,  100 = |cffFFFFFFx4|r jackpot.  (1-75 Ï‡Î±Î½ÎµÎ¹Ï‚)\n\n|cffF0C95ALucky 7|r  -  /roll 1-100.  Î–Î±ÏÎ¹Î± Ï€Î¿Ï… Î¤Î•Î›Î•Î™Î©ÎÎ•Î™ ÏƒÎµ 7 (7,17,27...97) = |cffFFFFFFx7|r!\n\n|cffF0C95ABlackjack|r  -  Ï€Î»Î·ÏƒÎ¹Î±ÏƒÎµ Ï„Î¿ 100 Ï‡Ï‰ÏÎ¹Ï‚ Î½Î± Ï„Î¿ Î¾ÎµÏ€ÎµÏÎ±ÏƒÎµÎ¹Ï‚.  |cffFFFFFFHit|r Î³Î¹Î± Ï‡Î±ÏÏ„Î¹, |cffFFFFFFStand|r Î³Î¹Î± ÏƒÏ„Î±Î¼Î±Ï„Î·Î¼Î±.  ÎÎ¹ÎºÎ± Ï„Î¿ ÎºÎ±Î¶Î¹Î½Î¿.\n\n|cffF0C95ADice|r  -  Ï„Î¿ ÎºÎ±Î¶Î¹Î½Î¿ ÏÎ¹Ï‡Î½ÎµÎ¹ 2d6.  Over (8-12) = |cffFFFFFFx2|r,  Under (2-6) = |cffFFFFFFx2|r,  Î±ÎºÏÎ¹Î²Ï‰Ï‚ 7 = |cffFFFFFFx4|r.\n\n|cffF0C95ARoulette|r  -  Î´Î¹Î±Î»ÎµÎ¾Îµ Ï‡ÏÏ‰Î¼Î±, Î¼ÎµÏ„Î± /roll 1-100.  ÎšÎ¿ÎºÎºÎ¹Î½Î¿ 1-45 = |cffFFFFFFx2|r,  Î ÏÎ±ÏƒÎ¹Î½Î¿ 46-55 = |cffFFFFFFx5|r,  ÎœÎ±Ï…ÏÎ¿ 56-100 = |cffFFFFFFx2|r.",
}
-- titlar pa info-rutan per sprak
local INFO_TITLES = {
  english = { how="How to play", guide="Games guide" },
  swedish = { how="Sa spelar du", guide="Spelguide" },
  deutsch = { how="Spielanleitung", guide="Spiele-Guide" },
  francais = { how="Comment jouer", guide="Guide des jeux" },
  espanol = { how="Como jugar", guide="Guia de juegos" },
  italiano = { how="Come giocare", guide="Guida ai giochi" },
  portugues = { how="Como jogar", guide="Guia de jogos" },
  russian = { how="ÐšÐ°Ðº Ð¸Ð³Ñ€Ð°Ñ‚ÑŒ", guide="Ð“Ð¸Ð´ Ð¿Ð¾ Ð¸Ð³Ñ€Ð°Ð¼" },
  ukrainian = { how="Ð¯Ðº Ð³Ñ€Ð°Ñ‚Ð¸", guide="Ð“Ñ–Ð´ Ð¿Ð¾ Ñ–Ð³Ñ€Ð°Ñ…" },
  bulgarian = { how="ÐšÐ°Ðº ÑÐµ Ð¸Ð³Ñ€Ð°Ðµ", guide="Ð ÑŠÐºÐ¾Ð²Ð¾Ð´ÑÑ‚Ð²Ð¾" },
  greek = { how="Î Ï‰Ï‚ Î½Î± Ï€Î±Î¹Î¾ÎµÎ¹Ï‚", guide="ÎŸÎ´Î·Î³Î¿Ï‚ Ï€Î±Î¹Ï‡Î½Î¹Î´Î¹Ï‰Î½" },
}
local function curLang() return (CasinobabeDB and CasinobabeDB.langCode) or "english" end
local function HowToText() return HOWTO_BY_LANG[curLang()] or HOWTO_BY_LANG.english end
local function GuideText() return GUIDE_BY_LANG[curLang()] or GUIDE_BY_LANG.english end
local function HowToTitle() return (INFO_TITLES[curLang()] or INFO_TITLES.english).how end
local function GuideTitle() return (INFO_TITLES[curLang()] or INFO_TITLES.english).guide end
-- ===== runtime =====
local selectedGame, selectedColor, selectedAmount
local connected=false
local pending=nil
local whoPending=false
local balDeltaToken=0
local cbDeltaToken=0
local cbIgnoreUntil=0
local lastWhoTime=0
local lastBetTime=0

-- ===== DEALER runtime =====
local dealerSessions = {}  -- [playerName] = session table
local dealerSessionCounter = 0
local dealerAdLastZone = nil
local dealerCurrentPlayer = nil  -- player currently being managed in UI
local dealerLog = {}  -- audit log entries
local DEALER_MAX_LOG = 200

-- Dealer State Machine states
local DEALER_STATES = {
  NEW = "NEW",
  CONTACTED = "CONTACTED",
  INVITED = "INVITED",
  TRADE_PENDING = "TRADE_PENDING",
  TRADE_VERIFIED = "TRADE_VERIFIED",
  STAKE_CONFIRMED = "STAKE_CONFIRMED",
  GROUPED = "GROUPED",
  GAME_SELECTED = "GAME_SELECTED",
  ROLLING = "ROLLING",
  RESOLVED = "RESOLVED",
  PAYOUT_PENDING = "PAYOUT_PENDING",
  PAID = "PAID",
  CLOSED = "CLOSED",
  ERROR = "ERROR",
  RECOVERY_REQUIRED = "RECOVERY_REQUIRED",
  CANCELLED = "CANCELLED",
}

-- Valid state transitions
local DEALER_TRANSITIONS = {
  [DEALER_STATES.NEW] = { DEALER_STATES.CONTACTED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.CONTACTED] = { DEALER_STATES.INVITED, DEALER_STATES.GAME_SELECTED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.INVITED] = { DEALER_STATES.TRADE_PENDING, DEALER_STATES.CANCELLED },
  [DEALER_STATES.TRADE_PENDING] = { DEALER_STATES.TRADE_VERIFIED, DEALER_STATES.CANCELLED, DEALER_STATES.ERROR },
  [DEALER_STATES.TRADE_VERIFIED] = { DEALER_STATES.STAKE_CONFIRMED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.STAKE_CONFIRMED] = { DEALER_STATES.GROUPED, DEALER_STATES.GAME_SELECTED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.GROUPED] = { DEALER_STATES.GAME_SELECTED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.GAME_SELECTED] = { DEALER_STATES.ROLLING, DEALER_STATES.CANCELLED },
  [DEALER_STATES.ROLLING] = { DEALER_STATES.RESOLVED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.RESOLVED] = { DEALER_STATES.PAYOUT_PENDING, DEALER_STATES.CANCELLED },
  [DEALER_STATES.PAYOUT_PENDING] = { DEALER_STATES.PAID, DEALER_STATES.CANCELLED, DEALER_STATES.ERROR },
  [DEALER_STATES.PAID] = { DEALER_STATES.CLOSED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.CLOSED] = {},
  [DEALER_STATES.ERROR] = { DEALER_STATES.RECOVERY_REQUIRED, DEALER_STATES.CANCELLED },
  [DEALER_STATES.RECOVERY_REQUIRED] = { DEALER_STATES.CANCELLED },
  [DEALER_STATES.CANCELLED] = {},
}

-- Dealer Error Codes
local DEALER_ERRORS = {
  ERR_NO_PLAYER = "ERR_NO_PLAYER",
  ERR_UNKNOWN_SESSION = "ERR_UNKNOWN_SESSION",
  ERR_DUPLICATE_SESSION = "ERR_DUPLICATE_SESSION",
  ERR_BAD_STAKE = "ERR_BAD_STAKE",
  ERR_WRONG_TRADE_TARGET = "ERR_WRONG_TRADE_TARGET",
  ERR_TRADE_MISMATCH = "ERR_TRADE_MISMATCH",
  ERR_TRADE_CANCELLED = "ERR_TRADE_CANCELLED",
  ERR_TRADE_TIMEOUT = "ERR_TRADE_TIMEOUT",
  ERR_NOT_GROUPED = "ERR_NOT_GROUPED",
  ERR_INVALID_GAME = "ERR_INVALID_GAME",
  ERR_ROLL_OUT_OF_RANGE = "ERR_ROLL_OUT_OF_RANGE",
  ERR_DUPLICATE_ROLL = "ERR_DUPLICATE_ROLL",
  ERR_INVALID_STATE = "ERR_INVALID_STATE",
  ERR_PAYOUT_TOO_EARLY = "ERR_PAYOUT_TOO_EARLY",
  ERR_DOUBLE_PAYOUT = "ERR_DOUBLE_PAYOUT",
  ERR_SESSION_CLOSED = "ERR_SESSION_CLOSED",
  ERR_RECOVERY_REQUIRED = "ERR_RECOVERY_REQUIRED",
}

-- Allowed capital cities for advertising (localized names)
local DEALER_CAPITALS = {
  Alliance = {
    enUS = "Stormwind", enGB = "Stormwind",
    frFR = "Hurlevent", deDE = "Sturmwind",
    esES = "Ventormenta", esMX = "Ventormenta",
    ruRU = "Ð¨Ñ‚Ð¾Ñ€Ð¼Ð³Ñ€Ð°Ð´", koKR = "ìŠ¤í†°ìœˆë“œ",
    zhCN = "æš´é£ŽåŸŽ", zhTW = "æš´é¢¨åŸŽ",
    ptBR = "Ventobravo", itIT = "Roccavento",
  },
  Horde = {
    enUS = "Orgrimmar", enGB = "Orgrimmar",
    frFR = "Orgrimmar", deDE = "Orgrimmar",
    esES = "Orgrimmar", esMX = "Orgrimmar",
    ruRU = "ÐžÑ€Ð³Ñ€Ð¸Ð¼Ð¼Ð°Ñ€", koKR = "ì˜¤ê·¸ë¦¬ë§ˆ",
    zhCN = "å¥¥æ ¼ç‘žçŽ›", zhTW = "å¥§æ ¼ç‘ª",
    ptBR = "Orgrimmar", itIT = "Orgrimmar",
  },
  Neutral = {
    Shattrath = {
      enUS = "Shattrath", enGB = "Shattrath",
      frFR = "Shattrath", deDE = "Shattrath",
      esES = "Shattrath", esMX = "Shattrath",
      ruRU = "Ð¨Ð°Ñ‚Ñ‚Ñ€Ð°Ñ‚", koKR = "ìƒ¤íŠ¸ë¼ìŠ¤",
      zhCN = "æ²™å¡”æ–¯", zhTW = "è–©å¡”æ–¯",
      ptBR = "Shattrath", itIT = "Shattrath",
    },
    Dalaran = {
      enUS = "Dalaran", enGB = "Dalaran",
      frFR = "Dalaran", deDE = "Dalaran",
      esES = "Dalaran", esMX = "Dalaran",
      ruRU = "Ð”Ð°Ð»Ð°Ñ€Ð°Ð½", koKR = "ë‹¬ë¼ëž€",
      zhCN = "è¾¾æ‹‰ç„¶", zhTW = "é”æ‹‰ç„¶",
      ptBR = "Dalaran", itIT = "Dalaran",
    },
  },
}

-- ===== farger =====
local C={ gold={0.96,0.80,0.35}, goldDk={0.83,0.69,0.22}, green={0.47,0.92,0.59},
          red={0.92,0.37,0.31}, light={0.93,0.91,0.89}, mute={0.70,0.67,0.65},
          gray={0.60,0.60,0.60} }
local function uc(t,a) return t[1],t[2],t[3],a or 1 end

-- ===== DB =====
-- Lokaliserad /who-fraga for online-kollen. VIKTIGT:
--  * /who matchar spelarens KLIENT-sprak (GetLocale), inte addon-spraket.
--  * Staden beror pa FACTION: Alliance -> Stormwind, Horde -> Orgrimmar.
--    (Man kan bara /who:a sin egen faction, sa casinots parkerade karaktar ar
--    samma faction som spelaren.)
-- Bygggs vid klick (CheckOnline) sa bade sprak och faction ar korrekta.
local STORMWIND_BY_LOCALE = {
  enUS="Stormwind", enGB="Stormwind",
  frFR="Hurlevent",
  deDE="Sturmwind",
  esES="Ventormenta", esMX="Ventormenta",
  ruRU="Ð¨Ñ‚Ð¾Ñ€Ð¼Ð³Ñ€Ð°Ð´",
  koKR="ìŠ¤í†°ìœˆë“œ",
  zhCN="æš´é£ŽåŸŽ",
  zhTW="æš´é¢¨åŸŽ",
}
local ORGRIMMAR_BY_LOCALE = {
  -- "Orgrimmar" ar ett egennamn som de flesta sprak behaller (en/fr/de/es/pt/it).
  -- Bara dessa skiljer sig; ovriga faller tillbaka till "Orgrimmar".
  ruRU="ÐžÑ€Ð³Ñ€Ð¸Ð¼Ð¼Ð°Ñ€",
  koKR="ì˜¤ê·¸ë¦¬ë§ˆ",
  zhCN="å¥¥æ ¼ç‘žçŽ›",
  zhTW="å¥§æ ¼ç‘ª",
}
local function LocalizedWhoQuery()
  local loc     = (GetLocale and GetLocale()) or "enUS"
  local faction = (UnitFactionGroup and UnitFactionGroup("player")) or "Alliance"
  local city
  if faction=="Horde" then city = ORGRIMMAR_BY_LOCALE[loc] or "Orgrimmar"
  else city = STORMWIND_BY_LOCALE[loc] or "Stormwind" end
  local mage = (LOCALIZED_CLASS_NAMES_MALE and LOCALIZED_CLASS_NAMES_MALE["MAGE"]) or "Mage"
  return "1 "..mage.." "..city.." C"
end

local function InitDB()
  CasinobabeDB=CasinobabeDB or {}
  CasinobabeDB.lastGame   = CasinobabeDB.lastGame   or "normal"
  CasinobabeDB.lastAmount = CasinobabeDB.lastAmount or 50
  CasinobabeDB.bal        = CasinobabeDB.bal        or 0
  CasinobabeDB.cashback   = CasinobabeDB.cashback   or 0
  CasinobabeDB.discord    = CasinobabeDB.discord    or "Casinobabetbc"
  if not CasinobabeDB.langCode then
    -- harled sprakkod fran ett ev. tidigare valt sprak-label, annars engelska
    CasinobabeDB.langCode = "english"
    if CasinobabeDB.langLabel then
      for _,l in ipairs(LANGS) do
        if l[1]==CasinobabeDB.langLabel then CasinobabeDB.langCode=l[2] break end
      end
    end
  end
  -- Online-kollens /who-fraga byggs vid klick (CheckOnline) sa sprak OCH faction
  -- ar korrekta (UnitFactionGroup finns inte sa tidigt som vid inladdning). Har
  -- rensar vi bara den gamla engelska defaulten sa den inte raknas som "egen".
  if (not CasinobabeDB.whoCustom)
     and (CasinobabeDB.whoQuery=="1 mage stormwind C") then
    CasinobabeDB.whoQuery=nil
  end
  if CasinobabeDB.blockTrades==nil then CasinobabeDB.blockTrades=false end
  if CasinobabeDB.fx==nil then CasinobabeDB.fx=true end
  CasinobabeDB.pos = CasinobabeDB.pos or nil
  CasinobabeDB.minimap = CasinobabeDB.minimap or { angle=210 }
  CasinobabeDB.stats = CasinobabeDB.stats or { games=0, wins=0, wagered=0, wonGold=0, lostGold=0, byGame={} }
  CasinobabeDB.history = CasinobabeDB.history or {}
  -- Dealer DB
  CasinobabeDB.dealer = CasinobabeDB.dealer or {}
  CasinobabeDB.dealer.enabled = CasinobabeDB.dealer.enabled or false
  CasinobabeDB.dealer.log = CasinobabeDB.dealer.log or {}
  CasinobabeDB.dealer.sessions = CasinobabeDB.dealer.sessions or {}
  
  -- Restore dealer log
  dealerLog = CasinobabeDB.dealer.log
  dealerEnabled = CasinobabeDB.dealer.enabled
  isDealerMode = dealerEnabled
  
  -- Auto-dealer mode persistence
  CasinobabeDB.autoDealer = CasinobabeDB.autoDealer or false
  autoAttractRunning = CasinobabeDB.autoDealer or false
  
  -- Restore sessions (only non-closed ones)
  if CasinobabeDB.dealer.sessions then
    for playerName, sessionData in pairs(CasinobabeDB.dealer.sessions) do
      if sessionData.state ~= DEALER_STATES.CLOSED and sessionData.state ~= DEALER_STATES.CANCELLED then
        -- Mark as recovery required since we can't verify trade state after reload
        sessionData.recoveryRequired = true
        sessionData.state = DEALER_STATES.RECOVERY_REQUIRED
        dealerSessions[playerName] = sessionData
      end
    end
  end
end

-- ============================================================================
-- DEALER HELPER FUNCTIONS
-- ============================================================================

-- Check if current character is the dealer (Casinobae)
local function IsDealerCharacter()
  local name = shortName(UnitName("player"))
  return name and name:lower() == DEALER_NAME:lower()
end

-- Auto-enable dealer mode if character is Casinobae
local function AutoDetectDealerMode()
  if IsDealerCharacter() and not dealerEnabled then
    dealerEnabled = true
    isDealerMode = true
    CasinobabeDB.dealer.enabled = true
    print("|cffFFD700Casinobabe|r Dealer mode AUTO-ENABLED for Casinobae.")
  end
end

-- Generate unique session ID
local function GenerateSessionId()
  dealerSessionCounter = dealerSessionCounter + 1
  return string.format("%s-%d-%d", DEALER_NAME, time(), dealerSessionCounter)
end

-- Generate unique round ID
local function GenerateRoundId(sessionId)
  return sessionId .. "-r" .. (session.roundCounter or 0) + 1
end

-- Dealer Audit Log
function DealerAuditLog(sessionId, player, event, details)
  local entry = string.format("[%s] %s %s %s", date("%H:%M"), sessionId or "-----", player or "-", event)
  if details then entry = entry .. " " .. details end
  table.insert(dealerLog, 1, entry)
  while #dealerLog > DEALER_MAX_LOG do table.remove(dealerLog) end
  -- Persist to SavedVariables
  CasinobabeDB.dealer.log = dealerLog
  -- Also print to chat for visibility
  print("|cffFFD700Casinobabe|r " .. entry)
end

-- Validate state transition
local function DealerValidateTransition(session, newState)
  local current = session.state
  local allowed = DEALER_TRANSITIONS[current]
  if not allowed then return false, "ERR_INVALID_STATE" end
  for _, v in ipairs(allowed) do
    if v == newState then return true end
  end
  return false, "ERR_INVALID_STATE"
end

-- Persist sessions to SavedVariables
local function DealerSaveSessions()
  CasinobabeDB.dealer.sessions = {}
  for playerName, session in pairs(dealerSessions) do
    if session.state ~= DEALER_STATES.CLOSED and session.state ~= DEALER_STATES.CANCELLED then
      CasinobabeDB.dealer.sessions[playerName] = session
    end
  end
end

-- Set session state with validation and logging
local function DealerSetState(session, newState, reason)
  local ok, err = DealerValidateTransition(session, newState)
  if not ok then
    DealerAuditLog(session.sessionId, session.player, "STATE_ERROR", "Invalid transition " .. session.state .. " -> " .. newState .. " (" .. err .. ")")
    return false, err
  end
  local oldState = session.state
  session.state = newState
  session.stateTime = time()
  session.lastTransition = oldState .. " -> " .. newState
  if reason then session.lastReason = reason end
  DealerAuditLog(session.sessionId, session.player, "STATE", oldState .. " -> " .. newState .. (reason and (" (" .. reason .. ")") or ""))
  DealerSaveSessions()
  DealerUpdateUI()
  return true
end

-- Create a new dealer session for a player
function DealerCreateSession(playerName)
  -- Check for existing active session
  if dealerSessions[playerName] then
    local existing = dealerSessions[playerName]
    if existing.state ~= DEALER_STATES.CLOSED and existing.state ~= DEALER_STATES.CANCELLED then
      return nil, DEALER_ERRORS.ERR_DUPLICATE_SESSION
    end
  end
  local sessionId = GenerateSessionId()
  local session = {
    sessionId = sessionId,
    player = playerName,
    state = DEALER_STATES.NEW,
    stateTime = time(),
    roundCounter = 0,
    stake = 0,
    game = nil,
    gameData = {},  -- color for roulette, choice for dice, etc.
    trade = {
      armed = false,
      expectedAmount = 0,
      target = nil,
      verified = false,
      completed = false,
    },
    group = {
      invited = false,
      grouped = false,
    },
    roll = nil,
    rollTime = nil,
    result = nil,
    multiplier = nil,
    payout = 0,
    payoutCompleted = false,
    error = nil,
    recoveryRequired = false,
  }
  dealerSessions[playerName] = session
  DealerSetState(session, DEALER_STATES.CONTACTED, "Player whispered JOIN")
  return session
end

-- Get session for player
function DealerGetSession(playerName)
  return dealerSessions[playerName]
end

-- Get all sessions for UI list
local function DealerGetAllSessions()
  return dealerSessions
end

-- Check if player has an active (non-closed) session
local function DealerHasActiveSession(playerName)
  local s = dealerSessions[playerName]
  return s and s.state ~= DEALER_STATES.CLOSED and s.state ~= DEALER_STATES.CANCELLED
end

-- Check if we can advertise in current zone
local function DealerCanAdvertiseHere()
  local zone = GetRealZoneText and GetRealZoneText() or GetZoneText and GetZoneText() or ""
  local subZone = GetSubZoneText and GetSubZoneText() or ""
  local faction = UnitFactionGroup and UnitFactionGroup("player") or "Alliance"
  local loc = GetLocale and GetLocale() or "enUS"
  
  -- Check faction capitals (exact match or zone contains capital name)
  local capitals = DEALER_CAPITALS[faction] or DEALER_CAPITALS.Alliance
  local capitalName = capitals[loc]
  if capitalName then
    if zone == capitalName or subZone == capitalName then
      return true
    end
    -- Also check if zone contains the capital name (e.g., "Stormwind City" contains "Stormwind")
    if zone:find(capitalName, 1, true) or subZone:find(capitalName, 1, true) then
      return true
    end
  end
  
  -- Check neutral capitals (each has its own locale table)
  for _, capitalData in pairs(DEALER_CAPITALS.Neutral) do
    local neutralName = capitalData[loc]
    if neutralName then
      if zone == neutralName or subZone == neutralName then
        return true
      end
      if zone:find(neutralName, 1, true) or subZone:find(neutralName, 1, true) then
        return true
      end
    end
  end
  
  return false
end

-- Advertise in say channel (legacy - use CasinoShow:StartShow for full show)
function DealerAdvertise()
  if not dealerEnabled then
    print("|cffFFD700Casinobabe|r Dealer mode not enabled. Use /cb dealer on")
    return false, "Dealer mode disabled"
  end
  
  -- Check unified cooldown
  local onCd, rem = DealerCooldownManager:Check("SHOW")
  if onCd then
    DealerDiag("ADVERTISE_BLOCKED", string.format("show cooldown %ds remaining", rem))
    print("|cffFFD700Casinobabe|r Cooldown " .. rem .. "s")
    return false, string.format("Cooldown %ds", rem)
  end
  
  -- Use the new Capital Show system
  local success, variant = CasinoShow:StartShow()
  if success then
    DealerCooldownManager:Set("SHOW")
    DealerDiag("ADVERTISE", string.format("show started: %s", variant))
    return true
  else
    DealerDiag("ADVERTISE_FAILED", string.format("%s", variant))
    return false, variant
  end
end

-- Quick advertise (legacy compatibility)
function DealerQuickAdvertise()
  if not dealerEnabled then
    print("|cffFFD700Casinobabe|r Dealer mode not enabled. Use /cb dealer on")
    return false, "Dealer mode disabled"
  end
  
  -- Quick ad doesn't trigger show cooldown (it's a quick message)
  CasinoShow:StartQuickAd()
  DealerDiag("QUICK_AD", "quick advertise sent")
  return true
end

-- Attract channel abstraction.
-- The show variants broadcast via SAY/YELL/EMOTE (range chat, no channel
-- membership required), so no GetChannelName()/join dance is needed today.
-- Returns the primary broadcast channel ("SAY"), or nil when chat output is
-- unavailable (SendChatMessage missing). Numbered channels (Trade/General/
-- LocalDefense) are NOT used by the show; if they ever are, resolve them via
-- GetChannelName() and treat id 0/nil as unavailable. Never bypass Blizzard
-- channel restrictions.
function GetAvailableAttractChannel()
  if SendChatMessage == nil then return nil end
  return "SAY"
end

-- Auto-dealer attract control (used by /cb auto and /cb auto stop).
-- Reuses existing attract entry points only: DealerAdvertise() to start
-- (same path as /cb dealer ad) and CasinoShow:StopShow() to stop.
-- Gated on channel availability, NOT on capital: attract runs anywhere a
-- usable channel exists. Sets autoAttractRunning only if the show starts.
function AutoStartAttract()
  if autoAttractRunning then return true end
  if GetAvailableAttractChannel() == nil then return false, "no channel" end
  
  -- Check unified cooldown
  local onCd, rem = DealerCooldownManager:Check("SHOW")
  if onCd then
    DealerDiag("AUTO_START_BLOCKED", string.format("show cooldown %ds remaining", rem))
    return false, string.format("cooldown %ds", rem)
  end
  
  local ok = DealerAdvertise()
  if not ok then return false, "show not started" end
  
  DealerCooldownManager:Set("SHOW")
  autoAttractRunning = true
  DealerDiag("AUTO_START", "attract started")
  return true
end

function AutoStopAttract()
  autoAttractRunning = false
  CasinoShow:StopShow()
  DealerDiag("AUTO_STOP", "attract stopped")
end

-- Whisper handler for players contacting dealer
function DealerOnWhisper(msg, sender)
  if not dealerEnabled then return end
  if not sender then return end
  sender = shortName(sender)
  if not sender then return end
  
  local lowerMsg = msg:lower()
  local isJoin = lowerMsg:find("join") or lowerMsg:find("play") or lowerMsg:find("casino")
  
  -- Diagnostics
  DealerDiag("WHISPER_RECEIVED", string.format("%s: %s", sender, msg))
  
  -- Check for existing session
  local session = DealerGetSession(sender)
  
  if session and session.state ~= DEALER_STATES.CLOSED and session.state ~= DEALER_STATES.CANCELLED then
    -- Player has active session - handle state-based whispers
    DealerDiag("PLAYER_PARSED", string.format("%s state=%s", sender, session.state))
    DealerHandleSessionWhisper(session, sender, msg, lowerMsg)
    return
  end
  
  -- No active session - check for JOIN
  if not isJoin then
    DealerDiag("PLAYER_REJECTED", string.format("%s - not a JOIN whisper", sender))
    -- Optionally send advertisement to unknown players
    if not DealerCooldownManager:Check("ADVERTISE") then
      DealerCooldownManager:Set("ADVERTISE")
      local reply = CasinoVariants:Get("WELCOME") .. " Whisper JOIN, PLAY, or CASINO to begin."
      SendChatMessage(reply, "WHISPER", nil, sender)
    end
    return
  end
  
  -- New JOIN whisper
  DealerDiag("PLAYER_ACCEPTED", string.format("%s - new session", sender))
  
  -- Autonomous core gate: per-player cooldown/anti-spam + single-session busy
  if not DealerCoreOnWhisperJoin(sender) then 
    DealerDiag("PLAYER_REJECTED", string.format("%s - core gate blocked", sender))
    return 
  end
  
  -- Create session
  local newSession, err = DealerCreateSession(sender)
  if not newSession then
    SendChatMessage("Error: " .. (err or "Unknown"), "WHISPER", nil, sender)
    DealerDiag("SESSION_FAILED", string.format("%s - %s", sender, err or "unknown"))
    return
  end
  
  DealerDiag("SESSION_CREATED", string.format("%s id=%s", sender, newSession.sessionId))
  
  -- Trigger reaction for new player
  CasinoReactionEngine:Trigger("PLAYER_JOIN", { player = sender })
  
  -- Reply with instructions (variant-based)
  local reply = CasinoVariants:Get("WELCOME") .. " Games: Normal, High Risk, Blackjack, Roulette, Dice, Lucky 7. Stakes 1g-1000g."
  SendChatMessage(reply, "WHISPER", nil, sender)
  DealerAuditLog(newSession.sessionId, sender, "WHISPER_REPLY", "Sent welcome instructions")
  
  -- Update UI
  DealerUpdateUI()
  DealerDiag("ACTIVE_SESSIONS", tostring(DealerCountActiveSessions()))
end

-- Handle whispers from players with existing sessions
function DealerHandleSessionWhisper(session, sender, msg, lowerMsg)
  local state = session.state
  
  -- Game selection from whisper (when in CONTACTED or GROUPED state)
  if (state == DEALER_STATES.CONTACTED or state == DEALER_STATES.GROUPED) then
    local gameKey = DealerParseGameFromWhisper(lowerMsg)
    if gameKey then
      DealerDiag("GAME_SELECTED_VIA_WHISPER", string.format("%s -> %s", sender, gameKey))
      local ok, err = DealerGame(sender, gameKey)
      if ok then
        SendChatMessage("Game set to " .. gameKey .. ". " .. CasinoVariants:Get("GAME_" .. gameKey:upper()), "WHISPER", nil, sender)
        DealerUpdateUI()
      else
        SendChatMessage("Could not set game: " .. (err or "unknown"), "WHISPER", nil, sender)
      end
      return
    end
  end
  
  -- Stake amount from whisper (when in STAKE_CONFIRMED or GROUPED)
  if state == DEALER_STATES.STAKE_CONFIRMED or state == DEALER_STATES.GROUPED then
    local amount = msg:match("^(%d+)g?$") or msg:match("^stake%s+(%d+)")
    if amount then
      amount = tonumber(amount)
      if amount and amount >= MIN_BET and amount <= MAX_BET then
        DealerDiag("STAKE_VIA_WHISPER", string.format("%s -> %dg", sender, amount))
        local ok, err = DealerStake(sender, amount)
        if ok then
          SendChatMessage("Stake set to " .. amount .. "g. Please open trade.", "WHISPER", nil, sender)
          DealerUpdateUI()
        else
          SendChatMessage("Could not set stake: " .. (err or "unknown"), "WHISPER", nil, sender)
        end
        return
      end
    end
  end
  
  -- Cancel/leave
  if lowerMsg:find("cancel") or lowerMsg:find("leave") or lowerMsg:find("quit") then
    DealerDiag("PLAYER_CANCEL", sender)
    DealerClose(sender)
    SendChatMessage("Session cancelled. Whisper JOIN to start over.", "WHISPER", nil, sender)
    DealerUpdateUI()
    return
  end
  
  -- Status request
  if lowerMsg:find("status") or lowerMsg:find("where") then
    local stateText = session.state
    if session.game then stateText = stateText .. " [" .. session.game .. "]" end
    if session.stake > 0 then stateText = stateText .. " " .. session.stake .. "g" end
    SendChatMessage("Your session: " .. stateText, "WHISPER", nil, sender)
    return
  end
  
  -- Unknown whisper for this state - send contextual help
  DealerDiag("WHISPER_UNHANDLED", string.format("%s state=%s msg=%s", sender, state, msg))
  local help = DealerGetWhisperHelp(state)
  if help then
    SendChatMessage(help, "WHISPER", nil, sender)
  end
end

-- Parse game key from whisper message
function DealerParseGameFromWhisper(lowerMsg)
  local games = { "normal", "high", "blackjack", "roulette", "dice", "lucky7", "lucky 7" }
  for _, g in ipairs(games) do
    if lowerMsg:find(g) then
      if g == "lucky 7" or g == "lucky7" then return "lucky7" end
      return g
    end
  end
  return nil
end

-- Get contextual help based on session state
function DealerGetWhisperHelp(state)
  if state == DEALER_STATES.CONTACTED or state == DEALER_STATES.GROUPED then
    return "Choose a game: Normal, High Risk, Blackjack, Roulette, Dice, Lucky 7. Or whisper CANCEL."
  elseif state == DEALER_STATES.STAKE_CONFIRMED or state == DEALER_STATES.TRADE_PENDING or state == DEALER_STATES.TRADE_VERIFIED then
    return "Stake is set. Please complete the trade. Whisper CANCEL to abort."
  elseif state == DEALER_STATES.GAME_SELECTED then
    return "Game selected. Roll using /roll 1-100. Whisper CANCEL to abort."
  elseif state == DEALER_STATES.ROLLING then
    return "Roll captured. Waiting for dealer to resolve. Whisper CANCEL to abort."
  elseif state == DEALER_STATES.RESOLVED or state == DEALER_STATES.PAYOUT_PENDING then
    return "Round resolved. Waiting for payout. Whisper CANCEL to abort."
  elseif state == DEALER_STATES.PAID then
    return "Payout complete. Whisper CANCEL to close session."
  end
  return nil
end

-- Count active sessions
function DealerCountActiveSessions()
  local c = 0
  for _, s in pairs(dealerSessions) do
    if s.state ~= DEALER_STATES.CLOSED and s.state ~= DEALER_STATES.CANCELLED then
      c = c + 1
    end
  end
  return c
end

-- Dealer Slash Command Handlers
function DealerToggle(onOff)
  if onOff == "on" then
    dealerEnabled = true
    isDealerMode = true
    CasinobabeDB.dealer.enabled = true
    print("|cffFFD700Casinobabe|r Dealer mode ENABLED")
    DealerAuditLog(nil, nil, "DEALER_ON", "Dealer mode enabled")
    
    -- Initialize dealer connection state
    if groupChannel() then
      DealerSetConnState(DEALER_CONN_STATES.CONNECTING, "raid/group detected")
      -- Send initial HELLO to establish connection
      SendControl("HELLO")
      DealerSetConnState(DEALER_CONN_STATES.CONNECTED, "handshake sent")
      DealerSetConnState(DEALER_CONN_STATES.READY, "ready for players")
    else
      DealerSetConnState(DEALER_CONN_STATES.CONNECTING, "no raid/group - waiting")
    end
    
    DealerUpdateUI()
    return true
  elseif onOff == "off" then
    dealerEnabled = false
    isDealerMode = false
    CasinobabeDB.dealer.enabled = false
    print("|cffFFD700Casinobabe|r Dealer mode DISABLED")
    DealerAuditLog(nil, nil, "DEALER_OFF", "Dealer mode disabled")
    DealerSetConnState(DEALER_CONN_STATES.OFF, "dealer disabled")
    AutoStopAttract()
    DealerUpdateUI()
    return true
  elseif onOff == "status" then
    local status = dealerEnabled and "|cff78EB96ENABLED|r" or "|cffEB5E4FDISABLED|r"
    local auto = IsDealerCharacter() and " (auto-detected Casinobae)" or ""
    print("|cffFFD700Casinobabe|r Dealer mode: " .. status .. auto)
    return true
  end
  return false
end

function DealerStatus()
  print("|cffFFD700Casinobabe|r === DEALER STATUS ===")
  print("  Mode: " .. (dealerEnabled and "|cff78EB96ON|r" or "|cffEB5E4FOFF|r"))
  print("  Character: " .. (UnitName("player") or "Unknown") .. (IsDealerCharacter() and " (Casinobae)" or ""))
  print("  Active sessions: " .. (function() local c=0 for _,s in pairs(dealerSessions) do if s.state~=DEALER_STATES.CLOSED and s.state~=DEALER_STATES.CANCELLED then c=c+1 end end return c end)())
  for name, session in pairs(dealerSessions) do
    if session.state ~= DEALER_STATES.CLOSED and session.state ~= DEALER_STATES.CANCELLED then
      print("  - " .. name .. ": " .. session.state .. (session.game and (" [" .. session.game .. "]") or "") .. (session.stake>0 and (" " .. session.stake .. "g") or ""))
    end
  end
end

-- Invite player to group
function DealerInvite(playerName)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state ~= DEALER_STATES.CONTACTED and session.state ~= DEALER_STATES.INVITED then
    return false, DEALER_ERRORS.ERR_INVALID_STATE
  end
  
  -- Check if player is online and not already grouped
  if UnitInRaid(playerName) or UnitInParty(playerName) then
    return false, "Player already in group/raid"
  end
  
  -- Try to invite (may fail if hardware event required)
  local ok = pcall(InviteUnit, playerName)
  if not ok then
    -- Invite failed - likely needs hardware event
    session.group.invited = false
    session.group.needsUserAction = true
    DealerSetState(session, DEALER_STATES.INVITED, "Invite pending - click to invite")
    DealerAuditLog(session.sessionId, playerName, "INVITE_NEEDS_ACTION", "InviteUnit requires hardware event")
    DealerDiag("INVITE_FAILED", string.format("%s - needs user action", playerName))
    return false, "USER_ACTION_REQUIRED"
  end
  
  session.group.invited = true
  session.group.needsUserAction = false
  DealerSetState(session, DEALER_STATES.INVITED, "Invited to group")
  DealerAuditLog(session.sessionId, playerName, "INVITE_SENT", "Group invitation sent")
  
  -- Trigger reaction
  CasinoReactionEngine:Trigger("PLAYER_GROUPED", { player = playerName })
  
  DealerDiag("INVITE_SENT", playerName)
  return true
end

-- Select game for player
function DealerGame(playerName, gameKey, extra)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state ~= DEALER_STATES.CONTACTED and session.state ~= DEALER_STATES.STAKE_CONFIRMED and session.state ~= DEALER_STATES.GROUPED and session.state ~= DEALER_STATES.GAME_SELECTED then
    return false, DEALER_ERRORS.ERR_INVALID_STATE
  end
  
  local rule = GetGameRule(gameKey)
  if not rule then return false, DEALER_ERRORS.ERR_INVALID_GAME end
  
  session.game = rule.key
  session.gameData = {}
  
  -- Store game-specific data
  if rule.key == "roulette" and extra then
    local color = extra:lower()
    if not ValidateRouletteColor(color) then return false, "Invalid color for roulette" end
    session.gameData.color = color
  elseif rule.key == "dice" and extra then
    local choice = extra:lower()
    if not ValidateDiceChoice(choice) then return false, "Invalid choice for dice" end
    session.gameData.choice = choice
  end
  
  session.roundCounter = (session.roundCounter or 0) + 1
  DealerSetState(session, DEALER_STATES.GAME_SELECTED, "Game: " .. rule.name .. (extra and (" " .. extra) or ""))
  DealerAuditLog(session.sessionId, playerName, "GAME_SELECTED", rule.name .. (extra and (" " .. extra) or ""))
  
  -- Trigger reaction
  CasinoReactionEngine:Trigger("GAME_SELECTED", { player = playerName, game = session.game, extra = extra })
  
  return true
end

-- Arm stake for trade verification
function DealerStake(playerName, amountStr)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state ~= DEALER_STATES.GAME_SELECTED and session.state ~= DEALER_STATES.STAKE_CONFIRMED and session.state ~= DEALER_STATES.TRADE_PENDING and session.state ~= DEALER_STATES.TRADE_VERIFIED and session.state ~= DEALER_STATES.INVITED then
    return false, DEALER_ERRORS.ERR_INVALID_STATE
  end

  -- Parse amount (e.g., "50g" or "50")
  local amount = tonumber(amountStr:match("(%d+%.?%d*)"))
  if not amount then return false, DEALER_ERRORS.ERR_BAD_STAKE end
  
  local ok, err = ValidateStake(session.game, amount)
  if not ok then return false, err end
  
  session.stake = amount
  session.trade.armed = true
  session.trade.expectedAmount = amount
  session.trade.target = playerName
  session.trade.verified = false
  session.trade.completed = false
  
  DealerSetState(session, DEALER_STATES.TRADE_PENDING, "Stake armed: " .. amount .. "g")
  DealerAuditLog(session.sessionId, playerName, "STAKE_ARMED", amount .. "g")
  
  -- Trigger reaction for stake armed
  CasinoReactionEngine:Trigger("TRADE_OPEN", { player = playerName, amount = amount, isPayout = false })
  
  -- Initiate trade (core marks ARMED / USER_ACTION_REQUIRED, same messages)
  DealerCoreRequestTrade(session)
  return true
end

-- Record a roll for a player
function DealerRecord(playerName, rollStr)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state ~= DEALER_STATES.ROLLING and session.state ~= DEALER_STATES.GAME_SELECTED then
    return false, DEALER_ERRORS.ERR_INVALID_STATE
  end
  
  local roll = tonumber(rollStr)
  if not roll then return false, "Invalid roll value" end
  
  local ok, err = ValidateRoll(session.game, roll)
  if not ok then return false, err end
  
  -- Check for duplicate roll
  if session.roll then return false, DEALER_ERRORS.ERR_DUPLICATE_ROLL end
  
  session.roll = roll
  session.rollTime = time()
  DealerSetState(session, DEALER_STATES.ROLLING, "Roll recorded: " .. roll)
  DealerAuditLog(session.sessionId, playerName, "ROLL", "Roll: " .. roll)
  
  -- Trigger reaction for roll received
  CasinoReactionEngine:Trigger("ROLL_RECEIVED", { player = playerName, roll = roll, game = session.game })
  
  return true
end

-- Resolve the game (calculate result)
function DealerResolve(playerName)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state ~= DEALER_STATES.ROLLING and session.state ~= DEALER_STATES.GAME_SELECTED then
    return false, DEALER_ERRORS.ERR_INVALID_STATE
  end
  if not session.roll then return false, "No roll recorded" end
  if not session.game then return false, DEALER_ERRORS.ERR_INVALID_GAME end
  if not session.stake or session.stake <= 0 then return false, DEALER_ERRORS.ERR_BAD_STAKE end
  
  local rule = GetGameRule(session.game)
  local mult, detail
  
  if session.game == "roulette" then
    local color = session.gameData.color or "red"
    mult = rule.resolve(session.roll, color)
    detail = "color=" .. color .. " roll=" .. session.roll
  elseif session.game == "dice" then
    local choice = session.gameData.choice or "over"
    local houseRolls = session.gameData.houseRolls or {1, 1}
    mult = rule.resolve(choice, houseRolls)
    detail = "choice=" .. choice .. " house=" .. (houseRolls[1]+houseRolls[2])
  elseif session.game == "blackjack" then
    -- For blackjack, roll is player's total, house total needed
    local playerTotal = session.roll
    local houseTotal = session.gameData.houseTotal or 0
    if houseTotal == 0 then return false, "House total not set" end
    mult = rule.resolve(playerTotal, houseTotal)
    detail = "player=" .. playerTotal .. " house=" .. houseTotal
  else
    mult = rule.resolve(session.roll)
    detail = "roll=" .. session.roll
  end
  
  session.multiplier = mult
  session.result = (mult and mult > 0) and "WIN" or "LOSS"
  if mult == 2 and session.game == "blackjack" then session.result = "PUSH" end
  
  local payout = ComputePayout(session.game, session.stake, 
    session.game == "roulette" and session.gameData.color or 
    session.game == "dice" and session.gameData.choice or 
    session.game == "blackjack" and {session.roll, session.gameData.houseTotal} or
    session.roll)
  
  session.payout = payout or 0
  
  DealerSetState(session, DEALER_STATES.RESOLVED, "Result: " .. session.result .. " (x" .. (mult or 0) .. ")")
  DealerAuditLog(session.sessionId, playerName, "RESOLVED", detail .. " -> " .. session.result .. " payout=" .. (payout or 0) .. "g")
  
  -- Trigger appropriate reaction based on result
  local ctx = { player = playerName, roll = session.roll, game = session.game, payout = session.payout, mult = mult }
  
  -- Check for near miss (one away from winning)
  local isNearMiss = false
  if session.result == "LOSS" and session.roll then
    if session.game == "normal" and session.roll == 58 then
      isNearMiss = true
    elseif session.game == "high" and session.roll == 75 then
      isNearMiss = true
    elseif session.game == "lucky7" and (session.roll % 10) == 6 or (session.roll % 10) == 8 then
      isNearMiss = true -- One away from ending in 7
    end
  end
  
  if session.result == "PUSH" then
    CasinoReactionEngine:Trigger("LOSS", ctx) -- Push treated as neutral
  elseif mult and mult >= 7 then
    CasinoReactionEngine:Trigger("JACKPOT", ctx)
  elseif mult and mult >= 4 then
    CasinoReactionEngine:Trigger("BIG_WIN", ctx)
  elseif session.result == "WIN" then
    CasinoReactionEngine:Trigger("WIN", ctx)
  elseif isNearMiss then
    ctx.isNearMiss = true
    CasinoReactionEngine:Trigger("NEAR_MISS", ctx)
  else
    CasinoReactionEngine:Trigger("LOSS", ctx)
  end
  
  return true
end

-- Arm payout for trade
function DealerPayout(playerName, amountStr)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state ~= DEALER_STATES.RESOLVED then
    return false, DEALER_ERRORS.ERR_PAYOUT_TOO_EARLY
  end
  if session.payoutCompleted then return false, DEALER_ERRORS.ERR_DOUBLE_PAYOUT end
  
  local expectedPayout = session.payout
  local amount = tonumber(amountStr and amountStr:match("(%d+%.?%d*)") or expectedPayout)
  if not amount then amount = expectedPayout end
  
  if amount ~= expectedPayout then
    print("|cffFFD700Casinobabe|r Warning: Payout amount " .. amount .. "g differs from calculated " .. expectedPayout .. "g")
  end
  
  session.trade.armed = true
  session.trade.expectedAmount = amount
  session.trade.target = playerName
  session.trade.verified = false
  session.trade.completed = false
  session.trade.isPayout = true
  
  DealerSetState(session, DEALER_STATES.PAYOUT_PENDING, "Payout armed: " .. amount .. "g")
  DealerAuditLog(session.sessionId, playerName, "PAYOUT_ARMED", amount .. "g")
  
  -- Trigger reaction for payout ready
  CasinoReactionEngine:Trigger("PAYOUT_READY", { player = playerName, amount = amount })
  
  -- Initiate trade for payout (core marks ARMED / USER_ACTION_REQUIRED, same messages)
  DealerCoreRequestTrade(session)
  return true
end

-- Close session
function DealerClose(playerName)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session.state == DEALER_STATES.CLOSED or session.state == DEALER_STATES.CANCELLED then
    return false, DEALER_ERRORS.ERR_SESSION_CLOSED
  end
  
  DealerSetState(session, DEALER_STATES.CLOSED, "Session closed by dealer")
  DealerAuditLog(session.sessionId, playerName, "CLOSED", "Final state: " .. session.state)
  
  -- Trigger reaction for player leave
  CasinoReactionEngine:Trigger("PLAYER_LEAVE", { player = playerName })
  
  if dealerCurrentPlayer == playerName then dealerCurrentPlayer = nil end
  DealerUpdateUI()
  return true
end

-- Reset session (force cancel)
function DealerReset(playerName)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  local session = DealerGetSession(playerName)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  
  DealerSetState(session, DEALER_STATES.CANCELLED, "Session reset by dealer")
  DealerAuditLog(session.sessionId, playerName, "RESET", "Forced cancel")
  if dealerCurrentPlayer == playerName then dealerCurrentPlayer = nil end
  dealerSessions[playerName] = nil
  DealerUpdateUI()
  return true
end

-- Show audit log
function DealerLog()
  print("|cffFFD700Casinobabe|r === DEALER AUDIT LOG (last 50) ===")
  for i = 1, math.min(50, #dealerLog) do
    print("  " .. dealerLog[i])
  end
  if #dealerLog == 0 then print("  (empty)") end
end

-- Dealer UI Update
function DealerUpdateUI()
  if not panel or not panel.dealerPanel then return end
  local dp = panel.dealerPanel
  
  -- Update mode button
  if dp.modeBtn then
    dp.modeBtn.txt:SetText(dealerEnabled and "Dealer: ON" or "Dealer: OFF")
    if dealerEnabled then
      dp.modeBtn.bg:SetColorTexture(0.35, 0.7, 0.35, 0.95)
      dp.modeBtn.txt:SetTextColor(0.1, 0.3, 0.1, 1)
    else
      dp.modeBtn.bg:SetColorTexture(0.20, 0.13, 0.04, 0.95)
      dp.modeBtn.txt:SetTextColor(uc(C.gold))
    end
  end
  
  -- Update connection status
  if dp.connStatus then
    local state, reason = DealerGetConnState()
    dp.connStatus:SetText(state)
    local color = (state == DEALER_CONN_STATES.CONNECTED or state == DEALER_CONN_STATES.READY) and C.green or 
                  (state == DEALER_CONN_STATES.CONNECTING) and C.gold or C.mute
    dp.connStatus:SetTextColor(color[1], color[2], color[3])
  end
  if dp.connReason then
    local _, reason = DealerGetConnState()
    dp.connReason:SetText(reason or "")
  end
  
  -- Update CasinoShow UI if panel exists
  if CasinoShow and CasinoShow.showPanel then
    CasinoShow:UpdateUI()
  end
  
  -- Update session list
  if dp.listContent then
    -- Clear existing buttons
    for _, child in ipairs({dp.listContent:GetChildren()}) do
      child:Hide()
      child:SetParent(nil)
    end
    
    local yOffset = 0
    local btnHeight = 22
    local btnGap = 2
    
    for playerName, session in pairs(dealerSessions) do
      if session.state ~= DEALER_STATES.CLOSED and session.state ~= DEALER_STATES.CANCELLED then
        local btn = CreateFrame("Button", nil, dp.listContent)
        btn:SetSize(dp.listContent:GetWidth() - 4, btnHeight)
        btn:SetPoint("TOPLEFT", dp.listContent, "TOPLEFT", 2, -yOffset)
        
        local bg = btn:CreateTexture(nil, "BACKGROUND")
        bg:SetAllPoints()
        bg:SetColorTexture(0.08, 0.06, 0.10, 0.9)
        
        local hl = btn:CreateTexture(nil, "HIGHLIGHT")
        hl:SetAllPoints()
        hl:SetColorTexture(1, 0.85, 0.3, 0.15)
        
        local border = MakeBorder(btn, 1)
        border:SetColor(0.2, 0.2, 0.2, 1)
        
        local stateColor = C.light
        if session.state == DEALER_STATES.TRADE_PENDING or session.state == DEALER_STATES.TRADE_VERIFIED then stateColor = C.gold
        elseif session.state == DEALER_STATES.STAKE_CONFIRMED or session.state == DEALER_STATES.GROUPED then stateColor = C.green
        elseif session.state == DEALER_STATES.ROLLING then stateColor = {0.5, 0.8, 1.0}
        elseif session.state == DEALER_STATES.RESOLVED then stateColor = C.green
        elseif session.state == DEALER_STATES.PAYOUT_PENDING then stateColor = C.gold
        elseif session.state == DEALER_STATES.PAID then stateColor = C.green
        elseif session.state == DEALER_STATES.ERROR then stateColor = C.red
        end
        
        local text = btn:CreateFontString(nil, "OVERLAY")
        text:SetFont("Fonts\\FRIZQT__.TTF", 9, "OUTLINE")
        text:SetPoint("LEFT", btn, "LEFT", 6, 0)
        local gameStr = session.game and (" [" .. session.game .. "]") or ""
        local stakeStr = session.stake > 0 and (" " .. session.stake .. "g") or ""
        text:SetText(string.format("%s  %s%s%s", playerName, session.state, gameStr, stakeStr))
        text:SetTextColor(stateColor[1], stateColor[2], stateColor[3])
        
        btn:SetScript("OnClick", function()
          dp.detail.currentPlayer = playerName
          dp.detail:Show()
          dp.listFrame:Hide()
          DealerUpdateUIDetail(dp, session)
        end)
        
        yOffset = yOffset + btnHeight + btnGap
      end
    end
    
    dp.listContent:SetHeight(math.max(yOffset, dp.listFrame:GetHeight()))
    dp.listFrame:Show()
  end
  
  -- Update detail panel
  if dp.detail and dp.detail.currentPlayer then
    local session = DealerGetSession(dp.detail.currentPlayer)
    if session then
      DealerUpdateUIDetail(dp, session)
    else
      dp.detail:Hide()
      dp.listFrame:Show()
    end
  end
end

function DealerUpdateUIDetail(dp, session)
  if not dp.detail then return end
  dp.detail.title:SetText("|cffFFD700" .. session.player .. "|r")
  dp.detail.stateText:SetText("State: |cffFFFFFF" .. session.state .. "|r")
  
  local gameStr = session.game and (session.game .. (session.gameData.color and (" - " .. session.gameData.color) or "") .. (session.gameData.choice and (" - " .. session.gameData.choice) or "")) or "â€”"
  dp.detail.gameText:SetText("Game: |cffFFFFFF" .. gameStr .. "|r")
  
  dp.detail.stakeText:SetText("Stake: |cffFFFFFF" .. (session.stake > 0 and (session.stake .. "g") or "â€”") .. "|r")
  
  local tradeStr = "â€”"
  if session.trade.armed then
    tradeStr = session.trade.isPayout and "PAYOUT: " or "STAKE: "
    tradeStr = tradeStr .. session.trade.expectedAmount .. "g "
    if session.trade.verified then tradeStr = tradeStr .. "|cff78EB96VERIFIED|r"
    elseif session.trade.completed then tradeStr = tradeStr .. "|cff78EB96COMPLETED|r"
    else tradeStr = tradeStr .. "|cffFFD700PENDING|r" end
  end
  dp.detail.tradeText:SetText("Trade: " .. tradeStr)
  
  local groupStr = "â€”"
  if session.group.invited then groupStr = "Invited"
  elseif session.group.grouped then groupStr = "Grouped" end
  dp.detail.groupText:SetText("Group: |cffFFFFFF" .. groupStr .. "|r")
  
  local rollStr = session.roll and (session.roll .. (session.rollTime and (" (" .. date("%H:%M:%S", session.rollTime) .. ")") or "")) or "â€”"
  dp.detail.rollText:SetText("Roll: |cffFFFFFF" .. rollStr .. "|r")
  
  local resultStr = "â€”"
  if session.result then
    local color = session.result == "WIN" and "78EB96" or (session.result == "PUSH" and "FFD700" or "EB5E4F")
    resultStr = "|cff" .. color .. session.result .. "|r"
    if session.multiplier then resultStr = resultStr .. " (x" .. session.multiplier .. ")" end
  end
  dp.detail.resultText:SetText("Result: " .. resultStr)
  
  local payoutStr = session.payout ~= 0 and (session.payout > 0 and ("+" .. session.payout .. "g") or (session.payout .. "g")) or "â€”"
  if session.payoutCompleted then payoutStr = payoutStr .. " |cff78EB96PAID|r" end
  dp.detail.payoutText:SetText("Payout: |cffFFFFFF" .. payoutStr .. "|r")
  
  dp.detail.errorText:SetText(session.error and ("Error: |cffEB5E4F" .. session.error .. "|r") or "")
  
  -- Enable/disable buttons based on state
  local s = session.state
  local needsInviteAction = session.group and session.group.needsUserAction
  local needsTradeAction = session.trade and session.trade.sub == "USER_ACTION_REQUIRED"
  dp.detail.btnInvite:SetEnabled(s == DEALER_STATES.CONTACTED or s == DEALER_STATES.INVITED or needsInviteAction)
  dp.detail.btnTrade:SetEnabled(s == DEALER_STATES.GAME_SELECTED or s == DEALER_STATES.STAKE_CONFIRMED or needsTradeAction)
  dp.detail.btnGroup:SetEnabled(s == DEALER_STATES.STAKE_CONFIRMED)
  dp.detail.btnGame:SetEnabled(s == DEALER_STATES.STAKE_CONFIRMED or s == DEALER_STATES.GROUPED)
  dp.detail.btnStart:SetEnabled(s == DEALER_STATES.GAME_SELECTED)
  dp.detail.btnResolve:SetEnabled(s == DEALER_STATES.ROLLING)
  dp.detail.btnPayout:SetEnabled(s == DEALER_STATES.RESOLVED and not session.payoutCompleted)
  dp.detail.btnClose:SetEnabled(s == DEALER_STATES.PAID)
  dp.detail.btnCancel:SetEnabled(s ~= DEALER_STATES.CLOSED and s ~= DEALER_STATES.CANCELLED)
end

-- Trade event handlers
function DealerOnTradeShow(targetName)
  if not dealerEnabled then return end
  targetName = shortName(targetName)
  if not targetName then return end
  
  local session = DealerGetSession(targetName)
  if not session then return end
  
  if session.trade.armed and session.trade.target == targetName then
    -- Trade window opened with correct player
    session.trade.sub = "OPEN"
    DealerAuditLog(session.sessionId, targetName, "TRADE_OPENED", session.trade.isPayout and "PAYOUT" or "STAKE")
  else
    -- Trade with unexpected player
    DealerAuditLog(session.sessionId or "-----", targetName, "TRADE_OPENED_UNEXPECTED", "No armed trade for this player")
  end
end

function DealerOnTradeAccept(targetName)
  if not dealerEnabled then return end
  targetName = shortName(targetName)
  if not targetName then return end
  
  local session = DealerGetSession(targetName)
  if not session or not session.trade.armed or session.trade.target ~= targetName then
    return
  end
  session.trade.sub = "VALIDATING"

  -- Verify trade money
  local targetMoney = GetTargetTradeMoney and GetTargetTradeMoney() or 0
  local playerMoney = GetPlayerTradeMoney and GetPlayerTradeMoney() or 0
  
  if session.trade.isPayout then
    -- We are paying out: check WE put in the right amount
    if playerMoney == session.trade.expectedAmount * 10000 then  -- copper
      session.trade.verified = true
      DealerAuditLog(session.sessionId, targetName, "PAYOUT_VERIFIED", "Amount: " .. session.trade.expectedAmount .. "g")
    else
      session.trade.verified = false
      DealerAuditLog(session.sessionId, targetName, "PAYOUT_MISMATCH", "Expected " .. session.trade.expectedAmount .. "g, got " .. (playerMoney/10000) .. "g")
    end
  else
    -- Player is paying stake: check THEY put in the right amount
    if targetMoney == session.trade.expectedAmount * 10000 then
      session.trade.verified = true
      DealerAuditLog(session.sessionId, targetName, "STAKE_VERIFIED", "Amount: " .. session.trade.expectedAmount .. "g")
    else
      session.trade.verified = false
      DealerAuditLog(session.sessionId, targetName, "STAKE_MISMATCH", "Expected " .. session.trade.expectedAmount .. "g, got " .. (targetMoney/10000) .. "g")
    end
  end
  
  if session.trade.verified then
    session.trade.sub = "ACCEPTED"
    if session.trade.isPayout then
      DealerSetState(session, DEALER_STATES.PAID, "Payout verified")
      -- Trigger reaction for payout verified
      CasinoReactionEngine:Trigger("TRADE_VERIFIED", { player = targetName, isPayout = true, amount = session.trade.expectedAmount })
    else
      DealerSetState(session, DEALER_STATES.TRADE_VERIFIED, "Stake verified")
      -- Trigger reaction for stake verified
      CasinoReactionEngine:Trigger("TRADE_VERIFIED", { player = targetName, isPayout = false, amount = session.trade.expectedAmount })
    end
  else
    session.trade.sub = "MISMATCH"
    DealerSetState(session, DEALER_STATES.ERROR, "Trade mismatch")
    -- Trigger reaction for trade mismatch
    CasinoReactionEngine:Trigger("TRADE_MISMATCH", { player = targetName })
  end
end

function DealerOnTradeClose(targetName, completed)
  if not dealerEnabled then return end
  targetName = shortName(targetName)
  if not targetName then return end
  
  local session = DealerGetSession(targetName)
  if not session or not session.trade.armed or session.trade.target ~= targetName then
    return
  end
  
  if completed and session.trade.verified then
    session.trade.completed = true
    session.trade.sub = "CONFIRMED"
    if session.trade.isPayout then
      session.payoutCompleted = true
      DealerSetState(session, DEALER_STATES.PAID, "Payout completed")
      DealerAuditLog(session.sessionId, targetName, "PAYOUT_COMPLETED", "Trade successful")
      -- Trigger reaction for payout completed
      CasinoReactionEngine:Trigger("PAYOUT_DONE", { player = targetName, amount = session.trade.expectedAmount })
    else
      DealerSetState(session, DEALER_STATES.STAKE_CONFIRMED, "Stake trade completed")
      DealerAuditLog(session.sessionId, targetName, "STAKE_COMPLETED", "Trade successful")
      -- Trigger reaction for stake trade completed
      CasinoReactionEngine:Trigger("TRADE_COMPLETED", { player = targetName, amount = session.trade.expectedAmount })
    end
  else
    DealerSetState(session, DEALER_STATES.ERROR, "Trade cancelled or failed")
    if session.trade then session.trade.sub = "CANCELLED" end
    DealerAuditLog(session.sessionId, targetName, "TRADE_CANCELLED", "Completed: " .. tostring(completed) .. ", Verified: " .. tostring(session.trade.verified))
    -- Trigger reaction for trade cancelled
    CasinoReactionEngine:Trigger("TRADE_CANCELLED", { player = targetName, completed = completed })
  end
end

-- ============================================================================
-- DEALER AUTONOMOUS CORE - Session/Player/Trade/Game/Payout orchestration
-- ============================================================================
-- Single active session. Every entry point below REUSES existing dealer logic
-- (DealerCreateSession/DealerSetState/DealerGame/DealerResolve/DealerPayout/
-- DealerClose/DealerReset/DealerAdvertise + GameRules validators). GameRules
-- stays the single source of truth; no game math is duplicated here.
-- Cycle: IDLE -> PLAYER -> BET -> GAME -> RESULT -> PAYOUT -> CLOSE -> IDLE.
-- Every failure path ends: ERROR -> cleanup -> IDLE. No session stays stuck.

-- ---- 1. SESSION MANAGER ----
-- Core vocabulary derived from the live dealer session (no parallel machine).
-- IDLE = no active (non-terminal) session.
local DEALER_CORE_STATE_OF = {
  NEW = "PLAYER_DETECTED",
  CONTACTED = "PLAYER_DETECTED",
  INVITED = "INVITED",
  TRADE_PENDING = "WAITING_TRADE",
  TRADE_VERIFIED = "WAITING_TRADE",
  STAKE_CONFIRMED = "WAITING_TRADE",
  GROUPED = "WAITING_TRADE",
  GAME_SELECTED = "GAME_READY",
  ROLLING = "GAME_RUNNING",
  RESOLVED = "RESULT_READY",
  PAYOUT_PENDING = "PAYOUT_PENDING",
  PAID = "PAYOUT_COMPLETED",
  CLOSED = "CLOSED",
  ERROR = "ERROR",
  RECOVERY_REQUIRED = "ERROR",
  CANCELLED = "CLOSED",
}

-- The one active session (nil when IDLE). Enforces single-session operation.
function DealerCoreActiveSession()
  for _, s in pairs(dealerSessions) do
    local st = s and s.state
    if st ~= DEALER_STATES.CLOSED and st ~= DEALER_STATES.CANCELLED then
      return s
    end
  end
  return nil
end

-- Snapshot of the autonomous core: IDLE or derived core/dealer state + trade sub-state.
function DealerCoreGetStatus()
  local s = DealerCoreActiveSession()
  if not s then return { core = "IDLE", dealer = nil, player = nil } end
  local core = DEALER_CORE_STATE_OF[s.state] or "ERROR"
  if core == "INVITED" then
    core = (s.group and s.group.grouped) and "WAITING_TRADE" or "WAITING_ACCEPT"
  end
  if core == "WAITING_TRADE" and s.trade and s.trade.sub == "OPEN" then
    core = "TRADE_RECEIVED"
  end
  return { core = core, dealer = s.state, player = s.player, sessionId = s.sessionId,
    game = s.game, stake = s.stake, trade = (s.trade and s.trade.sub) or "PRE_ARM" }
end

-- Guarded transition with double-transition (re-entrancy) protection.
function DealerCoreTransition(session, newState, reason)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  if session._transitioning then return false, DEALER_ERRORS.ERR_INVALID_STATE end
  session._transitioning = true
  local ok, err = DealerSetState(session, newState, reason)
  session._transitioning = false
  return ok, err
end

-- Create: refuses when another session is active (busy) or dealer off.
function DealerCoreCreateSession(playerName)
  if not dealerEnabled then return false, "Dealer mode disabled" end
  if not playerName then return false, DEALER_ERRORS.ERR_NO_PLAYER end
  if DealerCoreActiveSession() then return false, DEALER_ERRORS.ERR_DUPLICATE_SESSION end
  local session, err = DealerCreateSession(playerName)
  if not session then return false, err end
  session.createdAt = time()
  return session
end

-- True when the named player owns the active session.
function DealerCoreOwnsSession(playerName)
  local s = DealerCoreActiveSession()
  if s and s.player == playerName then return s end
  if s then return nil, DEALER_ERRORS.ERR_DUPLICATE_SESSION end
  return nil, DEALER_ERRORS.ERR_UNKNOWN_SESSION
end

-- Graceful close (PAID -> CLOSED enforced inside DealerClose).
function DealerCoreCloseSession(playerName)
  local s, err = DealerCoreOwnsSession(playerName)
  if not s then return false, err end
  return DealerClose(playerName)
end

-- Force recovery to IDLE from any state (works even with dealer toggled off;
-- DealerSetState carries no toggle gate). Clears trade arming + table entry.
function DealerCoreRecoverToIdle(playerName, reason)
  local s = (playerName and DealerGetSession(playerName)) or DealerCoreActiveSession()
  if not s then return true end
  if s.trade then s.trade.armed = false; s.trade.sub = nil; s.trade.verified = false end
  DealerAuditLog(s.sessionId, s.player, "CORE_RECOVER", reason or "recovered to IDLE")
  local st = s.state
  if st ~= DEALER_STATES.CLOSED and st ~= DEALER_STATES.CANCELLED then
    DealerSetState(s, DEALER_STATES.CANCELLED, reason or "recovered to IDLE")
  end
  dealerSessions[s.player] = nil
  DealerUpdateUI()
  return true
end

-- Abort: audit + recover. Player history (anti-spam memory) is intentionally kept.
function DealerCoreAbortSession(playerName, reason)
  local s = (playerName and DealerGetSession(playerName)) or DealerCoreActiveSession()
  if not s then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  DealerAuditLog(s.sessionId, s.player, "CORE_ABORT", reason or "aborted")
  return DealerCoreRecoverToIdle(s.player, reason or "aborted")
end

-- Alias kept for the required vocabulary (reset == abort + IDLE).
function DealerCoreResetSession(playerName, reason)
  return DealerCoreAbortSession(playerName, reason or "reset")
end

-- Per-state timeouts (seconds). RECOVERY_REQUIRED is excluded on purpose:
-- it needs a manual dealer decision after reload (see KNOWN_LIMITATIONS).
local DEALER_CORE_TIMEOUTS = {
  CONTACTED = 300, INVITED = 180, TRADE_PENDING = 180, TRADE_VERIFIED = 180,
  STAKE_CONFIRMED = 600, GROUPED = 600, GAME_SELECTED = 600, ROLLING = 600,
  RESOLVED = 600, PAYOUT_PENDING = 300, ERROR = 60,
}

-- Flag ERROR when the edge exists, else recover straight to IDLE. (ERROR is
-- only directly reachable from TRADE_PENDING/PAYOUT_PENDING per the table.)
function DealerCoreFlagErrorOrRecover(session, reason)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  local ok = DealerSetState(session, DEALER_STATES.ERROR, reason)
  if ok then
    DealerAuditLog(session.sessionId, session.player, "ERROR_FLAG", tostring(reason))
    if SendChatMessage then
      pcall(SendChatMessage, "Casino session hit an error (" .. tostring(reason) .. "). Whisper JOIN to start over.", "WHISPER", nil, session.player)
    end
    return true, "ERROR"
  end
  DealerCoreRecoverToIdle(session.player, reason)
  return true, "RECOVERED"
end

-- Two-phase timeout sweep (collect, then act). ERROR sessions past grace are
-- auto-recovered so nothing stays stuck. Returns number of sessions acted on.
function DealerCoreCheckTimeouts(now)
  now = now or time()
  local expired = {}
  for player, s in pairs(dealerSessions) do
    local st = s and s.state
    if st ~= DEALER_STATES.CLOSED and st ~= DEALER_STATES.CANCELLED then
      local limit = DEALER_CORE_TIMEOUTS[st]
      if limit and (now - (s.stateTime or now)) > limit then
        table.insert(expired, { player = player, session = s })
      end
    end
  end
  local acted = 0
  for _, e in ipairs(expired) do
    if e.session.state == DEALER_STATES.ERROR then
      DealerCoreRecoverToIdle(e.player, "ERROR grace expired")
    else
      DealerAuditLog(e.session.sessionId, e.player, "TIMEOUT", "state " .. tostring(e.session.state))
      DealerCoreFlagErrorOrRecover(e.session, "timeout in " .. tostring(e.session.state))
    end
    acted = acted + 1
  end
  return acted
end

-- ---- 2. PLAYER FLOW ----
-- Recent-player history + per-player cooldown/anti-spam. Memory survives aborts.
local dealerCorePlayers = {}
local DEALER_CORE_JOIN_COOLDOWN = 10
local DEALER_CORE_SPAM_WINDOW = 60
local DEALER_CORE_SPAM_MAX = 5
local DEALER_CORE_SPAM_IGNORE = 300

-- Gate for inbound JOIN whispers. Returns true to continue the existing
-- DealerOnWhisper flow, false when already answered (cooldown/spam/busy).
-- Reuses the existing whisper protocol (no new messages except busy-refusal).
function DealerCoreOnWhisperJoin(playerName)
  local now = time()
  local h = dealerCorePlayers[playerName]
  if not h then
    h = { lastSeen = 0, joins = 0, windowStart = now, cooldownUntil = 0 }
    dealerCorePlayers[playerName] = h
  end
  if now < (h.cooldownUntil or 0) then return false, "cooldown" end
  if now - (h.windowStart or 0) > DEALER_CORE_SPAM_WINDOW then
    h.windowStart = now; h.joins = 0
  end
  h.joins = (h.joins or 0) + 1
  h.lastSeen = now
  if h.joins > DEALER_CORE_SPAM_MAX then
    h.cooldownUntil = now + DEALER_CORE_SPAM_IGNORE
    DealerAuditLog(nil, playerName, "SPAM_IGNORE", "too many JOIN whispers")
    return false, "spam"
  end
  h.cooldownUntil = now + DEALER_CORE_JOIN_COOLDOWN
  local active = DealerCoreActiveSession()
  if active and active.player ~= playerName then
    SendChatMessage("The casino is currently serving another player. Please wait a moment.", "WHISPER", nil, playerName)
    DealerAuditLog(active.sessionId, playerName, "BUSY_REFUSED", "active session with " .. tostring(active.player))
    return false, "busy"
  end
  return true
end

-- Group leave/join tracking for the active session. Called from the existing
-- GROUP_ROSTER_UPDATE branch. Join sets grouped=true (audit); a later leave
-- moves the session to ERROR. Missing group APIs => no action (never false-positive).
function DealerCoreOnGroupUpdate()
  local s = DealerCoreActiveSession()
  if not s or not s.group then return end
  if UnitInParty == nil and UnitInRaid == nil then return end
  local inG = (UnitInParty and UnitInParty(s.player)) or (UnitInRaid and UnitInRaid(s.player))
  if inG then
    if not s.group.grouped then
      s.group.grouped = true
      DealerAuditLog(s.sessionId, s.player, "GROUP_JOINED", "player joined group")
    end
    return
  end
  if s.group.grouped then
    s.group.grouped = false
    local st = s.state
    if st ~= DEALER_STATES.CLOSED and st ~= DEALER_STATES.CANCELLED then
      DealerAuditLog(s.sessionId, s.player, "PLAYER_LEFT", "left group during " .. tostring(st))
      DealerCoreFlagErrorOrRecover(s, "player left group")
    end
  end
end

-- Manual entry for disconnects (no reliable name-based presence API exists;
-- grouped leaves are caught above, everything else by timeouts).
function DealerCoreOnPlayerOffline(playerName)
  local s = playerName and DealerGetSession(playerName) or DealerCoreActiveSession()
  if not s then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  local st = s.state
  if st == DEALER_STATES.CLOSED or st == DEALER_STATES.CANCELLED then return true end
  DealerAuditLog(s.sessionId, s.player, "PLAYER_OFFLINE", "state was " .. tostring(st))
  DealerCoreFlagErrorOrRecover(s, "player offline")
  return true
end

-- ---- 3. TRADE MANAGER ----
-- Trade window sub-states ride on session.trade.sub (nil = PRE_ARM):
-- ARMED -> OPEN -> VALIDATING -> ACCEPTED -> CONFIRMED,
-- plus MISMATCH / CANCELLED / USER_ACTION_REQUIRED.
-- The window itself can only be opened/confirmed by real user actions
-- (InitiateTrade/AcceptTrade need hardware events); the addon NEVER
-- auto-accepts. USER_ACTION_REQUIRED marks states waiting on the human side.

-- Arm + request the trade window. Preserves the exact legacy messages.
-- Returns false,"USER_ACTION_REQUIRED" out of range; the TRADE_SHOW event
-- (either side opening the window) is the legitimate resume signal.
function DealerCoreRequestTrade(session)
  if not session then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  session.trade.sub = "ARMED"
  if CheckInteractDistance and CheckInteractDistance(session.player, 2) then
    local ok = pcall(InitiateTrade, session.player)
    if not ok then
      -- InitiateTrade failed - likely needs hardware event
      session.trade.sub = "USER_ACTION_REQUIRED"
      DealerAuditLog(session.sessionId, session.player, "TRADE_NEEDS_ACTION", "InitiateTrade requires hardware event")
      DealerDiag("TRADE_FAILED", string.format("%s - needs user action", session.player))
      return false, "USER_ACTION_REQUIRED"
    end
    DealerAuditLog(session.sessionId, session.player,
      session.trade.isPayout and "PAYOUT_TRADE_INITIATED" or "TRADE_INITIATED", "Trade window opened")
    return true
  end
  session.trade.sub = "USER_ACTION_REQUIRED"
  if session.trade.isPayout then
    print("|cffFFD700Casinobabe|r Player not in trade range for payout.")
  else
    print("|cffFFD700Casinobabe|r Player not in trade range. Move closer or wait for them to trade you.")
  end
  DealerAuditLog(session.sessionId, session.player, "USER_ACTION_REQUIRED", "Out of trade range - waiting for player to open trade")
  return false, "USER_ACTION_REQUIRED"
end

-- Read-only trade snapshot for UI/status/watchdog.
function DealerCoreTradeStatus(playerName)
  local s = (playerName and DealerGetSession(playerName)) or DealerCoreActiveSession()
  if not s then return false, DEALER_ERRORS.ERR_UNKNOWN_SESSION end
  local t = s.trade or {}
  return true, { sub = t.sub or "PRE_ARM", armed = t.armed,
    expected = t.expectedAmount, verified = t.verified,
    completed = t.completed, target = t.target, isPayout = t.isPayout }
end

-- ---- 4. GAME ENGINE ADAPTER ----
-- Thin wrappers over existing logic. GameRules (validators + rule.resolve +
-- ComputePayout, consumed inside DealerGame/DealerResolve) stays the single
-- source of truth; no math is duplicated here.
function DealerCoreValidateBet(gameKey, amount)
  if type(amount) ~= "number" then
    amount = tonumber(amount)
    if not amount then return false, DEALER_ERRORS.ERR_BAD_STAKE end
  end
  return ValidateStake(gameKey, amount)
end

function DealerCoreStartGame(playerName, gameKey, extra)
  local s, err = DealerCoreOwnsSession(playerName)
  if not s then return false, err end
  return DealerGame(playerName, gameKey, extra)
end

function DealerCoreResolveGame(playerName)
  local s, err = DealerCoreOwnsSession(playerName)
  if not s then return false, err end
  return DealerResolve(playerName)
end

-- Read-only: payout/result/multiplier as computed by DealerResolve.
function DealerCoreCalculatePayout(playerName)
  local s, err = DealerCoreOwnsSession(playerName)
  if not s then return false, err end
  if s.state ~= DEALER_STATES.RESOLVED then return false, DEALER_ERRORS.ERR_PAYOUT_TOO_EARLY end
  return true, { payout = s.payout or 0, result = s.result, multiplier = s.multiplier }
end

-- ---- 5/6. WATCHDOG ----
-- Single OnUpdate accumulator (no per-session timers => nothing to duplicate).
-- Every 5s: timeout sweep + impossible-state integrity. All work pcall-guarded.
local dealerCoreWdAccum = 0
local dealerCoreWdBusy = false
local DEALER_CORE_WD_INTERVAL = 5

function DealerCoreCheckIntegrity()
  local fixed = 0
  for player, s in pairs(dealerSessions) do
    if s and DEALER_CORE_STATE_OF[s.state] == nil then
      DealerAuditLog(s.sessionId, player, "IMPOSSIBLE_STATE", "unknown state " .. tostring(s.state))
      DealerCoreRecoverToIdle(player, "impossible state")
      fixed = fixed + 1
    end
  end
  return fixed
end

function DealerCoreWatchdogTick(elapsed)
  dealerCoreWdAccum = (dealerCoreWdAccum or 0) + (elapsed or 0)
  if dealerCoreWdAccum < DEALER_CORE_WD_INTERVAL then return 0 end
  dealerCoreWdAccum = 0
  if dealerCoreWdBusy then return 0 end
  dealerCoreWdBusy = true
  local acted = 0
  local ok, n = pcall(DealerCoreCheckTimeouts)
  if ok then acted = acted + (n or 0) end
  local ok2, m = pcall(DealerCoreCheckIntegrity)
  if ok2 then acted = acted + (m or 0) end
  dealerCoreWdBusy = false
  return acted
end

local dealerCoreWdFrame = CreateFrame("Frame")
dealerCoreWdFrame:SetScript("OnUpdate", function(_, e)
  if dealerEnabled then pcall(DealerCoreWatchdogTick, e or 0) end
end)

-- System message handler for rolls (from other players)
function DealerOnSystemMsg(text)
  if not dealerEnabled then return end
  if not text then return end
  
  -- Parse roll messages from other players
  -- "Player rolls 42 (1-100)" or localized variants
  local who, roll = text:match("^(%S+) .-(%d+) %(1%-100%)")
  if not who or not roll then
    who, roll = text:match("^(.-) rolls (%d+) %(%d+%-%d+%)")
  end
  if not who or not roll then return end
  
  who = shortName(who)
  roll = tonumber(roll)
  if not who or not roll then return end
  
  local session = DealerGetSession(who)
  if not session then return end
  if session.state ~= DEALER_STATES.ROLLING and session.state ~= DEALER_STATES.GAME_SELECTED then return end
  if session.roll then return end  -- already recorded
  
  local ok, err = ValidateRoll(session.game, roll)
  if not ok then
    DealerAuditLog(session.sessionId, who, "ROLL_REJECTED", err .. " (roll=" .. roll .. ")")
    return
  end
  
  session.roll = roll
  session.rollTime = time()
  DealerSetState(session, DEALER_STATES.ROLLING, "Roll captured: " .. roll)
  DealerAuditLog(session.sessionId, who, "ROLL_CAPTURED", "Roll: " .. roll)
  
  -- Trigger reaction for roll start
  CasinoReactionEngine:Trigger("ROLL_START", { player = who, roll = roll, game = session.game })
end

-- ===== namn/kanaler =====
local myName=nil
function groupChannel()
  if IsInRaid and IsInRaid() then return "RAID" end
  if (GetNumRaidMembers and GetNumRaidMembers()>0) then return "RAID" end
  if (GetNumPartyMembers and GetNumPartyMembers()>0) then return "PARTY" end
  if IsInGroup and IsInGroup() then return "PARTY" end
  return nil
end

-- vem ar gruppens/raidens ledare (= casinot, om inget annat angetts)
local function groupLeaderName()
  if IsInRaid and IsInRaid() then
    local n=(GetNumGroupMembers and GetNumGroupMembers()) or (GetNumRaidMembers and GetNumRaidMembers()) or 0
    for i=1,n do
      local nm,rank=GetRaidRosterInfo(i)
      if nm and rank==2 then return shortName(nm) end
    end
  elseif groupChannel()=="PARTY" then
    if UnitIsGroupLeader and UnitIsGroupLeader("player") then return shortName(UnitName("player")) end
    local n=(GetNumSubgroupMembers and GetNumSubgroupMembers()) or (GetNumPartyMembers and GetNumPartyMembers()) or 0
    for i=1,n do
      if UnitIsGroupLeader and UnitIsGroupLeader("party"..i) then return shortName(UnitName("party"..i)) end
    end
  end
  return nil
end
local function SendControl(msg)
  local ch=groupChannel(); if not ch then return false end
  if C_ChatInfo and C_ChatInfo.SendAddonMessage then C_ChatInfo.SendAddonMessage(PREFIX,msg,ch)
  elseif SendAddonMessage then SendAddonMessage(PREFIX,msg,ch) else return false end
  return true
end
function PostPublic(text)
  local ch=groupChannel()
  if not ch then SetStatus("Join the casino's raid first.", C.gold); return false end
  SendChatMessage(text, ch); return true
end
function DoRoll() if RandomRoll then RandomRoll(1,100) end end
local function RegisterPrefix()
  if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then C_ChatInfo.RegisterAddonMessagePrefix(PREFIX)
  elseif RegisterAddonMessagePrefix then RegisterAddonMessagePrefix(PREFIX) end
end

-- ============================================================================
-- UI-HJALPARE
-- ============================================================================
local function SetFontSafe(fs,path,size,flags) if not fs:SetFont(path,size,flags or "") then fs:SetFont("Fonts\\FRIZQT__.TTF",size,flags or "") end end
local function MakeBacking(f,r,g,bl,a) local t=f:CreateTexture(nil,"BACKGROUND"); t:SetAllPoints(); t:SetColorTexture(r or 0,g or 0,bl or 0,a or 0.62); return t end
local function MakeText(f,size,flags,just) local fs=f:CreateFontString(nil,"OVERLAY"); fs:SetFont("Fonts\\FRIZQT__.TTF",size,flags or ""); fs:SetJustifyH(just or "LEFT"); return fs end

local function MakeChip(parent,w,h,label,sub,icon)
  local b=CreateFrame("Button", nil, parent); b:SetSize(w,h)
  b.bg=MakeBacking(b,0.07,0.05,0.09,0.82); b.border=MakeBorder(b,2); b.border:SetColor(0.27,0.21,0.16,1)
  local tx=0
  if icon then
    b.icon=b:CreateTexture(nil,"ARTWORK"); b.icon:SetSize(h-14,h-14)
    b.icon:SetPoint("LEFT",b,"LEFT",5,0)
    if icon=="DICE_DRAW" then
      -- rita en tarning sjalv (vissa klienter saknar dice-ikonen)
      b.icon:SetColorTexture(0.93,0.91,0.86,1)              -- tarningens yta
      local sz=h-14; local s=sz/2-4
      local function pip(dx,dy)
        local d=b:CreateTexture(nil,"OVERLAY"); d:SetSize(3,3)
        d:SetColorTexture(0.13,0.11,0.12,1); d:SetPoint("CENTER", b.icon, "CENTER", dx, dy)
      end
      pip(-s,s); pip(s,s); pip(0,0); pip(-s,-s); pip(s,-s)   -- 5-prickar
    else
      b.icon:SetTexture("Interface\\ICONS\\"..icon); b.icon:SetTexCoord(0.08,0.92,0.08,0.92)
    end
    tx=(h-14)+8
  end
  b.label=MakeText(b, sub and 10 or 11, "OUTLINE", icon and "LEFT" or "CENTER")
  b.label:SetText(label); b.label:SetTextColor(uc(C.light))
  if sub then
    if icon then b.label:SetPoint("LEFT",b,"LEFT",tx,5) else b.label:SetPoint("CENTER",b,"CENTER",0,5) end
    b.sub=MakeText(b,7.5,"", icon and "LEFT" or "CENTER"); b.sub:SetText(sub); b.sub:SetTextColor(uc(C.mute))
    if icon then b.sub:SetPoint("LEFT",b,"LEFT",tx,-6) else b.sub:SetPoint("CENTER",b,"CENTER",0,-7) end
  else b.label:SetPoint("CENTER") end
  local hl=b:CreateTexture(nil,"HIGHLIGHT"); hl:SetAllPoints(); hl:SetColorTexture(1,1,1,0.07)
  function b:SetSelected(on)
    if on then self.border:SetColor(uc(C.gold)); self.bg:SetColorTexture(0.18,0.09,0.05,0.9); self.label:SetTextColor(uc(C.gold))
    else self.border:SetColor(0.27,0.21,0.16,1); self.bg:SetColorTexture(0.07,0.05,0.09,0.82); self.label:SetTextColor(uc(C.light)) end
  end
  return b
end

local function MakeButton(parent,w,h,text,r,g,bl,a,txtColor)
  local b=CreateFrame("Button", nil, parent); b:SetSize(w,h)
  b.bg=MakeBacking(b,r,g,bl,a)
  -- glansig topp (sheen) + fasad kant ger upphojd, "riktig knapp"-kansla
  b.gloss=b:CreateTexture(nil,"ARTWORK"); b.gloss:SetPoint("TOPLEFT",1,-1); b.gloss:SetPoint("BOTTOMRIGHT",b,"TOPRIGHT",-1,-math.max(6,math.floor(h*0.52)))
  b.gloss:SetColorTexture(1,1,1,0.10); b.gloss:SetBlendMode("ADD")
  local topL=b:CreateTexture(nil,"OVERLAY"); topL:SetPoint("TOPLEFT",1,-1); topL:SetPoint("TOPRIGHT",-1,-1); topL:SetHeight(1); topL:SetColorTexture(1,0.96,0.85,0.22)
  local botL=b:CreateTexture(nil,"OVERLAY"); botL:SetPoint("BOTTOMLEFT",1,1); botL:SetPoint("BOTTOMRIGHT",-1,1); botL:SetHeight(1); botL:SetColorTexture(0,0,0,0.40)
  b.border=MakeBorder(b,2); b.border:SetColor(uc(C.goldDk))
  b.txt=MakeText(b,10,"OUTLINE","CENTER"); b.txt:SetText(text); b.txt:SetPoint("CENTER")
  if txtColor then b.txt:SetTextColor(uc(txtColor)) else b.txt:SetTextColor(uc(C.light)) end
  local hl=b:CreateTexture(nil,"HIGHLIGHT"); hl:SetAllPoints(); hl:SetColorTexture(1,0.9,0.6,0.13); hl:SetBlendMode("ADD")
  b:SetScript("OnMouseDown", function() b.txt:SetPoint("CENTER",0,-1); b.gloss:SetAlpha(0.04) end)
  b:SetScript("OnMouseUp", function() b.txt:SetPoint("CENTER",0,0); b.gloss:SetAlpha(1) end)
  return b
end

-- ============================================================================
-- FIRE FX
-- ============================================================================
local fxGlows={}
local fxSparks={}
local function BuildFX(f)
  -- eget lager OVANPA rutorna, annars doljs glowen bakom de halvtackande korten
  local layer=CreateFrame("Frame", nil, f); layer:SetAllPoints(); layer:SetFrameLevel((f:GetFrameLevel() or 1)+5)
  f.fxLayer=layer
  local function glow(x1,y1,x2,y2,r,g,b,baseA,ampA,speed,phase)
    local t=layer:CreateTexture(nil,"ARTWORK")
    t:SetPoint("TOPLEFT",f,"TOPLEFT",x1,y1); t:SetPoint("BOTTOMRIGHT",f,"TOPLEFT",x2,y2)
    t:SetColorTexture(r,g,b,baseA); t:SetBlendMode("ADD")
    fxGlows[#fxGlows+1]={tex=t,base=baseA,amp=ampA,speed=speed,phase=phase}
  end
  -- ELD vid de brinnande korten (vanster) -- rejalt starkare
  glow(0,-112,66,-300,  1.0,0.50,0.16, 0.13,0.20, 5.5,0.0)   -- het vansterkant
  glow(2,-120,150,-330, 1.0,0.34,0.10, 0.07,0.13, 8.0,1.7)   -- bredare sken
  glow(8,-150,120,-280, 1.0,0.16,0.05, 0.06,0.14, 6.7,3.1)   -- flammor
  -- COIN-glitter: stjarnor som blinkar dar guldmynten ar (hoger + center)
  local function spark(px,py,size,speed,phase)
    local t=layer:CreateTexture(nil,"OVERLAY")
    t:SetTexture("Interface\\Cooldown\\star4"); t:SetBlendMode("ADD")
    t:SetVertexColor(1.0,0.92,0.55); t:SetSize(size,size)
    t:SetPoint("CENTER",f,"TOPLEFT",px,py)
    fxSparks[#fxSparks+1]={tex=t,speed=speed,phase=phase,size=size}
  end
  spark(340,-126,16, 2.3,0.0)   -- stora myntet uppe till hoger
  spark(352,-186,12, 3.1,1.1)   -- hoger kant
  spark(332,-232,11, 2.7,2.3)   -- hoger nedre
  spark(286,-182,10, 3.5,0.6)   -- center-fallande mynt
  local acc=0
  layer:SetScript("OnUpdate", function(self,e)
    acc=acc+e; if acc<0.04 then return end; acc=0
    if not CasinobabeDB.fx then
      for _,gd in ipairs(fxGlows) do gd.tex:SetAlpha(0) end
      for _,sp in ipairs(fxSparks) do sp.tex:SetAlpha(0) end
      return
    end
    local now=GetTime()
    for _,gd in ipairs(fxGlows) do
      local n=(math.sin(now*gd.speed+gd.phase)*0.5+0.5); n=n*(0.7+0.3*math.random())
      gd.tex:SetAlpha(gd.base+gd.amp*n)
    end
    for _,sp in ipairs(fxSparks) do
      local n=(math.sin(now*sp.speed+sp.phase)*0.5+0.5)
      sp.tex:SetAlpha(0.12+0.88*n*n)                 -- skarp blink
      local s=sp.size*(0.65+0.55*n); sp.tex:SetSize(s,s)
    end
  end)
end

-- ============================================================================
-- CENTER POPUP (mindre; visas bara for spelarens egna spel)
-- ============================================================================
local popupGen=0
-- ===== ACTION-BAR: BET-knappen blir ROLL/HIT/STAND/OVER... pa plats =====
-- Resultat visas i statusraden (snabbt, ingen kvardrojande center-ruta).
local actBar
local resultFX
local centerInfo
local topBanner
local bannerGen=0
local bjTotalFX
local function RefreshCashbackSoon()
  -- Casinot skickar bara STATE vid saldoandring; en forlust andrar inget extra,
  -- sa cashbacken kan vaxa utan push. Vi drar darfor frisk STATE efter varje runda.
  if C_Timer and C_Timer.After then
    C_Timer.After(1.2, function() if groupChannel and groupChannel() then SendControl("HELLO") end end)
  end
end
local function ActHide()
  if actBar then
    for i=1,3 do actBar.btns[i]:Hide() end
    actBar.cancel:Hide(); actBar:Hide()
  end
  if centerInfo then centerInfo:Hide() end
  if panel then
    if panel.placeBtn then panel.placeBtn:Show() end
    if panel.amtBox then panel.amtBox:Show() end
  end
  if UpdateDisplay then UpdateDisplay() end
end
local function ActLayout(specs)
  for i=1,3 do actBar.btns[i]:Hide() end
  local n=#specs; if n==0 then return end
  local cancelW, gap = 20, 4
  local usable=actBar:GetWidth()-cancelW-gap
  local bw=(usable-(n-1)*gap)/n
  for i,sp in ipairs(specs) do
    local b=actBar.btns[i]; b:SetWidth(bw); b:ClearAllPoints()
    b:SetPoint("LEFT", actBar, "LEFT", (i-1)*(bw+gap), 0)
    b.txt:SetText(sp.label); b:SetScript("OnClick", sp.action); b:Show()
  end
end
local function ActShow(specs)
  if not actBar then return end
  if panel then
    if panel.placeBtn then panel.placeBtn:Hide() end
    if panel.amtBox then panel.amtBox:Hide() end
  end
  actBar:Show(); actBar.cancel:Show(); ActLayout(specs)
end
local function BuildActionBar(f, W, PAD)
  local awTot=W-PAD*2
  local bar=CreateFrame("Frame", nil, f)
  bar:SetSize(awTot,26); bar:SetPoint("TOPRIGHT", f, "TOPRIGHT", -PAD, -344)
  bar.btns={}
  for i=1,3 do
    local b=CreateFrame("Button", nil, bar); b:SetSize(60,26)
    MakeBacking(b, uc(C.gold,1)); b.border=MakeBorder(b,2); b.border:SetColor(uc(C.goldDk))
    b.txt=MakeText(b,12,"","CENTER"); b.txt:SetPoint("CENTER"); b.txt:SetTextColor(0.10,0.05,0.02,1)
    local hl=b:CreateTexture(nil,"HIGHLIGHT"); hl:SetAllPoints(); hl:SetColorTexture(1,1,1,0.16)
    bar.btns[i]=b; b:Hide()
  end
  local cancel=CreateFrame("Button", nil, bar); cancel:SetSize(18,26)
  cancel:SetPoint("RIGHT", bar, "RIGHT", 0, 0)
  MakeBacking(cancel,0.12,0.05,0.05,0.92); cancel.border=MakeBorder(cancel,2); cancel.border:SetColor(uc(C.red))
  local cxt=MakeText(cancel,13,"OUTLINE","CENTER"); cxt:SetText("x"); cxt:SetPoint("CENTER"); cxt:SetTextColor(uc(C.red))
  local chl=cancel:CreateTexture(nil,"HIGHLIGHT"); chl:SetAllPoints(); chl:SetColorTexture(1,0.3,0.3,0.20)
  cancel:SetScript("OnClick", function() PopupHide() end)
  bar.cancel=cancel; bar:Hide()
  actBar=bar
  return bar
end

-- ===== RESULTRUTA: poppar upp "WIN +25g", blinkar vid vinst, fadear ut snabbt =====
local function BuildResultFX(parent)
  local r=CreateFrame("Frame", nil, parent)
  r:SetSize(196,64); r:SetPoint("CENTER", parent, "TOP", 0, -152)
  r:SetFrameStrata("DIALOG"); r:EnableMouse(false)
  r.bg=MakeBacking(r,0.05,0.02,0.07,0.92); r.border=MakeBorder(r,2); r.border:SetColor(uc(C.gold))
  r.glow=r:CreateTexture(nil,"BACKGROUND")
  r.glow:SetPoint("CENTER"); r.glow:SetSize(250,120); r.glow:SetBlendMode("ADD")
  if not r.glow:SetTexture("Interface\\AddOns\\Casinobabe\\media\\glow.tga") then
    r.glow:SetTexture("Interface\\Cooldown\\star4")
  end
  r.glow:Hide()
  r.txt=MakeText(r,22,"OUTLINE","CENTER"); r.txt:SetPoint("CENTER",0,9)
  r.sub=MakeText(r,12,"OUTLINE","CENTER"); r.sub:SetPoint("TOP", r.txt, "BOTTOM", 0, -2); r.sub:SetTextColor(uc(C.light))
  r:Hide()
  r.t=0; r.win=false
  r:SetScript("OnUpdate", function(self, elapsed)
    self.t=self.t+(elapsed or 0)
    local t=self.t
    if t<0.15 then
      local k=t/0.15; self:SetScale(0.7+0.42*k); self:SetAlpha(k)
    elseif t<0.25 then
      local k=(t-0.15)/0.10; self:SetScale(1.12-0.12*k); self:SetAlpha(1)
    elseif t<0.55 then
      self:SetScale(1); self:SetAlpha(1)
      if self.win then self.glow:SetAlpha(0.35+0.45*math.abs(math.sin(t*16))) end
    elseif t<0.95 then
      self:SetScale(1); self:SetAlpha(1-(t-0.55)/0.40)
    else
      self:SetScale(1); self:Hide()
    end
  end)
  -- Stort center-nummer (blackjack-total, dice-resultat) - syns under rundan.
  centerInfo=MakeText(parent,20,"OUTLINE","CENTER")
  centerInfo:SetPoint("CENTER", parent, "TOP", 0, -132)
  centerInfo:Hide()
  return r
end
-- visar/doljer det stora center-numret under rundan
local function SetCenterInfo(text, color)
  if not centerInfo then return end
  if text and text~="" then
    centerInfo:SetText(text)
    centerInfo:SetTextColor(color and color[1] or C.gold[1], color and color[2] or C.gold[2], color and color[3] or C.gold[3])
    centerInfo:Show()
  else
    centerInfo:Hide()
  end
end
-- ===== TOPP-BANNER: stor, tydlig varning hogst upp (t.ex. "spel pagar redan") =====
local function BuildTopBanner(f, W, PAD)
  local b=CreateFrame("Frame", nil, f); b:SetSize(W-PAD*2, 56)
  b:SetPoint("TOP", f, "TOP", 0, -88); b:SetFrameStrata("DIALOG"); b:EnableMouse(true)
  b.bg=MakeBacking(b,0.20,0.04,0.05,0.97); b.border=MakeBorder(b,3); b.border:SetColor(uc(C.gold))
  b.title=MakeText(b,15,"OUTLINE","CENTER"); b.title:SetPoint("TOP",0,-8); b.title:SetTextColor(uc(C.gold))
  b.sub=MakeText(b,11,"","CENTER"); b.sub:SetPoint("TOP", b.title, "BOTTOM", 0, -3)
  b.sub:SetWidth(W-PAD*2-18); b.sub:SetTextColor(uc(C.light))
  b:SetScript("OnMouseDown", function() b:Hide() end)  -- tryck for att stanga
  b:Hide()
  topBanner=b
  return b
end
local function ShowTopBanner(title, sub, secs)
  if not topBanner then return end
  topBanner.title:SetText(title or "")
  topBanner.sub:SetText(sub or "")
  -- auto-hojd: vaxa bannern sa hela sub-texten far plats (ingen avhuggen rad).
  local sh=48
  if topBanner.sub.GetStringHeight then
    local h=topBanner.sub:GetStringHeight()
    if type(h)=="number" and h>10 then sh=h end
  end
  topBanner:SetHeight(38+sh)
  topBanner:Show()
  bannerGen=bannerGen+1; local g=bannerGen
  if secs and C_Timer and C_Timer.After then
    C_Timer.After(secs, function() if bannerGen==g and topBanner then topBanner:Hide() end end)
  end
end
local function HideTopBanner()
  bannerGen=bannerGen+1
  if topBanner then topBanner:Hide() end
end
-- Casinot nekade blackjack/dice (single-session: nagon annan spelar just nu).
local function BlackjackBusy()
  if pending and (pending.game=="blackjack" or pending.game=="dice") and not pending.started then
    pending=nil; ActHide()
  end
  ShowTopBanner("A GAME IS ALREADY IN PROGRESS",
    "Blackjack & Dice run one player at a time (the house rolls). Wait a few seconds, then place your bet again.", 9)
  if SetStatus then SetStatus("Table busy - try your blackjack/dice bet again in a few seconds.", C.gold) end
end
-- Casinot bekraftade aldrig betet (offline / inte i raiden / processades aldrig).
-- Avbryt rundan: ingen insats drogs, inget resultat, ingen statistik.
local function NoResponse()
  if not (pending and not pending.started) then return end
  pending=nil; ActHide()
  ShowTopBanner("CASINO DIDN'T RESPOND",
    "The casino may be offline or not in your raid. Nothing was bet - your balance is unchanged. Try again when they're online.", 10)
  if SetStatus then SetStatus("No response from the casino - are they online? Nothing was bet.", C.gold) end
end
local function ShowResult(won, amount)
  if not resultFX then return end
  if centerInfo then centerInfo:Hide() end   -- center-numret slacks, resultet tar over
  local r=resultFX
  r.win = won and true or false
  -- detaljrad: visa vad som rullades / blackjack-total / husets tarning
  local detail=""
  if pending then
    if pending.game=="blackjack" and pending.bjTotal and pending.bjTotal>0 then detail="with "..pending.bjTotal
    elseif pending.game=="dice" and pending.diceTotal then detail="house rolled "..pending.diceTotal
    elseif pending.lastRoll then detail="you rolled "..pending.lastRoll end
  end
  r.sub:SetText(detail)
  if won then
    r.txt:SetText(("WIN  +%dg"):format(amount or 0)); r.txt:SetTextColor(uc(C.green))
    r.border:SetColor(uc(C.green))
    r.glow:SetVertexColor(0.35,1,0.55); r.glow:SetAlpha(0.7); r.glow:Show()
  else
    r.txt:SetText(("-%dg"):format(amount or 0)); r.txt:SetTextColor(uc(C.red))
    r.border:SetColor(uc(C.red))
    r.glow:Hide()
  end
  r.t=0; r:SetScale(0.7); r:SetAlpha(0); r:Show()
end

-- (gammal halvfardig blackjack-total ersatt av centerInfo nedan)
-- gom action-baren (tillbaka till BET) efter X sek, om ingen ny runda startats
function PopupHideLater(secs)
  RefreshCashbackSoon()
  if not (C_Timer and C_Timer.After) then ActHide(); return end
  local g=popupGen
  C_Timer.After(secs, function() if popupGen==g then ActHide() end end)
end

local function LossCheck(secs)
  if not (C_Timer and C_Timer.After) then return end
  local p = pending
  if not p then return end
  C_Timer.After(secs, function()
    if pending == p and p.actionTaken then
      FlashResult(false, p.stake, p.game)
      RecordResult(p.game, p.stake, false, -p.stake)
      PopupResolve(false, p.stake); pending=nil
    end
  end)
end

function PopupConfigure(gameKey)
  if gameKey=="blackjack" then
    local function showBoth()
      SetStatus("Hit or stand?  (your cards are in chat)", C.light)
      ActShow({
        { label="HIT", action=function() if pending then pending.actionTaken=true end DoRoll() end },
        { label="STAND", action=function()
              if pending then pending.actionTaken=true end PostPublic("stand")
              if pending then SetCenterInfo("STAND  "..(pending.bjTotal or 0), C.gold) end
              SetStatus("Standing... good luck!", C.light); ActShow({}); LossCheck(9) end },
      })
    end
    SetCenterInfo("YOUR TOTAL:  0", C.gold)
    SetStatus("Hit to draw your first card!", C.light)
    ActShow({
      { label="HIT", action=function() if pending then pending.actionTaken=true end DoRoll(); showBoth() end },
    })
  elseif gameKey=="dice" then
    SetCenterInfo("DICE  -  pick your bet", C.gold)
    SetStatus("Over (8-12), Under (2-6) or Lucky 7 (x4)?", C.light)
    local function choose(word,label)
      if pending then pending.actionTaken=true; pending.diceChoice=label; pending.diceRolls={} end
      PostPublic(word)
      SetCenterInfo("House rolling...", C.light)
      SetStatus("Chose "..label..". House rolling...", C.light); ActShow({}); LossCheck(4)
    end
    ActShow({
      { label="OVER", action=function() choose("over","OVER") end },
      { label="UNDER", action=function() choose("under","UNDER") end },
      { label="LUCKY 7", action=function() choose("seven","LUCKY 7") end },
    })
  else
    -- single-roll (normal/high/lucky7/roulette): ROLL -> OnMyRoll avgor direkt
    SetStatus("Roll now!  -  press ROLL (/roll 1-100)", C.gold)
    ActShow({
      { label="ROLL  /roll", action=function()
          if pending then pending.actionTaken=true end DoRoll()
          SetStatus("Rolling...", C.light); ActShow({})
          local pp=pending
          if C_Timer and C_Timer.After then C_Timer.After(4, function()
            if pending==pp then PopupHide() end end) end
        end },
    })
  end
end

function PopupStart(gameKey,amt,color)
  if not panel then return end
  popupGen=popupGen+1   -- ny runda -> gamla auto-gom-timers blir ogiltiga
  local gname; for _,g in ipairs(GAMES) do if g.key==gameKey then gname=g.name end end
  if gameKey=="roulette" and color then gname=(gname or "").." "..color end
  SetStatus(string.format("%s: bet %dg placed, waiting for table...", (gname or "?"), amt), C.gold)
  ActShow({})   -- doljer BET-knappen tills bordet svarar
end
function PopupActivate(gameKey)
  if not panel then return end
  HideTopBanner()
  PopupConfigure(gameKey)
end
function PopupResolve(won,amount)
  if not panel then return end
  local roll=""
  if pending then
    if pending.game=="blackjack" and pending.bjTotal then roll="total "..pending.bjTotal.."  -  "
    elseif pending.lastRoll then roll="rolled "..pending.lastRoll.."  -  " end
  end
  if won then SetStatus(string.format("%sWIN!  +%dg", roll, amount or 0), C.green)
  else SetStatus(string.format("%sno win this time - good luck next!", roll), C.red) end
  ShowResult(won, amount or 0)
  PopupHideLater(0.8)
end
function PopupHide() pending=nil; ActHide() end

-- ============================================================================
-- INFO-RUTA (How to play / Games guide)
-- ============================================================================
function ShowInfo(title, body, kind)
  if not Casinobabe_Info then
    local fr=CreateFrame("Frame","Casinobabe_Info", UIParent, BackdropTemplateMixin and "BackdropTemplate" or nil)
    fr:SetSize(366,452); fr:SetPoint("CENTER"); fr:SetFrameStrata("FULLSCREEN_DIALOG")
    fr:SetMovable(true); fr:EnableMouse(true); fr:RegisterForDrag("LeftButton")
    fr:SetScript("OnDragStart", fr.StartMoving); fr:SetScript("OnDragStop", fr.StopMovingOrSizing)
    local b=fr:CreateTexture(nil,"BACKGROUND"); b:SetAllPoints(); b:SetColorTexture(0.04,0.02,0.06,0.98)
    if fr.SetBackdrop then fr:SetBackdrop({edgeFile="Interface\\DialogFrame\\UI-DialogBox-Border", edgeSize=18, insets={left=4,right=4,top=4,bottom=4}}); fr:SetBackdropBorderColor(1,0.95,0.8,1)
    else MakeBorder(fr,2):SetColor(uc(C.goldDk)) end
    fr.title=MakeText(fr,15,"OUTLINE","CENTER"); fr.title:SetPoint("TOP",0,-12); fr.title:SetTextColor(uc(C.gold))
    local cl=CreateFrame("Button", nil, fr); cl:SetSize(22,22); cl:SetPoint("TOPRIGHT",-8,-8)
    local clx=MakeText(cl,16,"OUTLINE","CENTER"); clx:SetText("X"); clx:SetPoint("CENTER"); clx:SetTextColor(uc(C.mute))
    cl:SetScript("OnClick", function() fr:Hide() end)
    fr.body=MakeText(fr,11,"","LEFT"); fr.body:SetPoint("TOPLEFT",16,-42); fr.body:SetPoint("BOTTOMRIGHT",-16,44)
    fr.body:SetJustifyV("TOP"); fr.body:SetJustifyH("LEFT"); fr.body:SetSpacing(2)
    -- Reset-knapp (visas bara pa Stats/History) - nollar ALLA varden med bekraftelse.
    fr.resetBtn=CreateFrame("Button", nil, fr)
    fr.resetBtn:SetSize(220,26); fr.resetBtn:SetPoint("BOTTOM",0,12)
    MakeBacking(fr.resetBtn,0.30,0.06,0.07,0.95)
    fr.resetBtn.border=MakeBorder(fr.resetBtn,2); fr.resetBtn.border:SetColor(uc(C.red))
    local rt=MakeText(fr.resetBtn,11,"OUTLINE","CENTER"); rt:SetText("Reset all stats & history")
    rt:SetPoint("CENTER"); rt:SetTextColor(uc(C.light))
    fr.resetBtn:SetScript("OnEnter", function(self) self.border:SetColor(uc(C.gold)) end)
    fr.resetBtn:SetScript("OnLeave", function(self) self.border:SetColor(uc(C.red)) end)
    fr.resetBtn:SetScript("OnClick", function() StaticPopup_Show("CASINOBABE_RESET_ALL") end)
    fr.resetBtn:Hide()
  end
  -- Valj typsnitt utifran innehallet VARJE gang (inte bara vid skapande):
  --  * ren ASCII (Stats/History/latinska sprak) -> FRIZQT som ALLTID renderas.
  --  * icke-ASCII (kyrilliska/grekiska) -> buntat Unicode-font (FRIZQT som fallback).
  -- Detta fixar "svart ruta": tidigare sattes Unicode-fontet en gang vid skapande,
  -- och om det inte hann ladda da blev texten osynlig for hela sessionen.
  local UNI="Interface\\AddOns\\Casinobabe\\media\\casino_uni.ttf"
  local FRIZ="Fonts\\FRIZQT__.TTF"
  local needUni=(title and title:find("[\128-\255]")) or (body and body:find("[\128-\255]"))
  if needUni then
    if not Casinobabe_Info.body:SetFont(UNI,13,"") then Casinobabe_Info.body:SetFont(FRIZ,13,"") end
    if not Casinobabe_Info.title:SetFont(UNI,15,"OUTLINE") then Casinobabe_Info.title:SetFont(FRIZ,15,"OUTLINE") end
  else
    Casinobabe_Info.body:SetFont(FRIZ,13,"")
    Casinobabe_Info.title:SetFont(FRIZ,15,"OUTLINE")
  end
  Casinobabe_Info._kind=kind
  if Casinobabe_Info.resetBtn then
    if kind=="stats" or kind=="history" then Casinobabe_Info.resetBtn:Show() else Casinobabe_Info.resetBtn:Hide() end
  end
  Casinobabe_Info.title:SetText(title)
  Casinobabe_Info.body:SetText(body)
  Casinobabe_Info:Show()
end

-- ============================================================================
-- ONLINE-KOLL (/who)
-- ============================================================================
function SetOnlineStatus(txt,color)
  if panel and panel.onlineBtn then panel.onlineBtn.txt:SetText(txt)
    panel.onlineBtn.txt:SetTextColor(color and color[1] or C.light[1], color and color[2] or C.light[2], color and color[3] or C.light[3]) end
end
function CheckOnline()
  local now=(GetTime and GetTime()) or 0
  if (now-lastWhoTime)<3 then return end   -- mjuk throttle (ingen fast sparr som kan fastna)
  lastWhoTime=now
  -- Bygg fragan har (vid klick) sa faction + klient-sprak ar korrekta. Egen
  -- fraga (via /cb who) anvands om satt, annars auto.
  local q=(CasinobabeDB.whoCustom and CasinobabeDB.whoQuery) or LocalizedWhoQuery()
  whoPending=true
  SetOnlineStatus("Opening /who list...", C.gold)
  -- visa Blizzards /who-LISTA: kor kommandot precis som om man skrev det i chatten
  if C_FriendList and C_FriendList.SetWhoToUi then C_FriendList.SetWhoToUi(true) end
  local eb=(ChatEdit_ChooseBoxForSend and ChatEdit_ChooseBoxForSend()) or (DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.editBox)
  if eb and ChatEdit_SendText then eb:SetText("/who "..q); ChatEdit_SendText(eb,0); if eb.SetText then eb:SetText("") end
  elseif C_FriendList and C_FriendList.SendWho then C_FriendList.SendWho(q)
  elseif SendWho then SendWho(q)
  else SetOnlineStatus("Type /who yourself", C.mute); whoPending=false; return end
  if C_Timer and C_Timer.After then C_Timer.After(5, function()
    if whoPending then whoPending=false
      SetOnlineStatus("Checked - see /who list", C.gold) end
  end) end
end

-- ============================================================================
-- PANEL
-- ============================================================================
local function CreatePanel()
  InitDB()
  myName=shortName(UnitName("player"))
  local W,H,PAD = 380, 432, 12

  local f=CreateFrame("Frame","Casinobabe_Panel", UIParent, BackdropTemplateMixin and "BackdropTemplate" or nil)
  f:SetSize(W,H); f:SetFrameStrata("HIGH"); f:SetClampedToScreen(true)
  f:SetMovable(true); f:EnableMouse(true); f:RegisterForDrag("LeftButton")
  f:SetScript("OnDragStart", f.StartMoving)
  f:SetScript("OnDragStop", function(self) self:StopMovingOrSizing()
    local p,_,rp,x,y=self:GetPoint(); CasinobabeDB.pos={p=p,rp=rp,x=x,y=y} end)
  if CasinobabeDB.pos then f:SetPoint(CasinobabeDB.pos.p, UIParent, CasinobabeDB.pos.rp, CasinobabeDB.pos.x, CasinobabeDB.pos.y) else f:SetPoint("CENTER") end

  -- bakgrund: beskuren till panel-aspekten (ingen ihoptryckning)
  local bg=f:CreateTexture(nil,"BACKGROUND")
  bg:SetPoint("TOPLEFT",4,-4); bg:SetPoint("BOTTOMRIGHT",-4,4)
  bg:SetTexture(MEDIA); bg:SetTexCoord(0,1,0,0.57)
  local scrim=f:CreateTexture(nil,"BORDER"); scrim:SetPoint("TOPLEFT",4,-4); scrim:SetPoint("BOTTOMRIGHT",-4,4)
  scrim:SetColorTexture(0.02,0.01,0.03,0.24)

  if f.SetBackdrop then f:SetBackdrop({edgeFile="Interface\\DialogFrame\\UI-DialogBox-Border", edgeSize=18, insets={left=4,right=4,top=4,bottom=4}}); f:SetBackdropBorderColor(1,0.95,0.8,1)
  else MakeBorder(f,2):SetColor(uc(C.goldDk)) end

  BuildFX(f)

  -- HEADER
  f.blockBtn=MakeButton(f,90,19,"Block Trades",0.10,0.07,0.10,0.8)
  f.blockBtn:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-8)
  f.blockBtn:SetScript("OnClick", function() ToggleBlockTrades() end)
  
  -- TAB BUTTONS (Player / Dealer)
  local tabW, tabH = 70, 20
  f.playerTab = MakeButton(f, tabW, tabH, "PLAYER", 0.12,0.09,0.05,0.9, C.gold)
  f.playerTab:SetPoint("TOPLEFT", f.blockBtn, "TOPRIGHT", 8, 0)
  f.playerTab.txt:SetFont("Fonts\\FRIZQT__.TTF", 10, "OUTLINE")
  f.dealerTab = MakeButton(f, tabW, tabH, "DEALER", 0.08,0.06,0.12,0.9, C.light)
  f.dealerTab:SetPoint("LEFT", f.playerTab, "RIGHT", 4, 0)
  f.dealerTab.txt:SetFont("Fonts\\FRIZQT__.TTF", 10, "OUTLINE")
  
  local title=MakeText(f,16,"OUTLINE","CENTER"); title:SetPoint("TOP",f,"TOP",0,-7); title:SetText("Casinobabe"); title:SetTextColor(uc(C.gold))
  local close=CreateFrame("Button", nil, f); close:SetSize(20,20); close:SetPoint("TOPRIGHT",f,"TOPRIGHT",-7,-7)
  local cx=MakeText(close,16,"OUTLINE","CENTER"); cx:SetText("X"); cx:SetPoint("CENTER"); cx:SetTextColor(uc(C.mute))
  close:SetScript("OnClick", function() f:Hide() end)
  -- Stats + History (uppe till hoger, vanster om X)
  f.histBtn=MakeButton(f,52,16,"History",0.08,0.06,0.12,0.9, C.light)
  f.histBtn:SetPoint("TOPRIGHT",f,"TOPRIGHT",-30,-9); f.histBtn.txt:SetFont("Fonts\\FRIZQT__.TTF",9,"")
  f.histBtn:SetScript("OnClick", function() ShowInfo("Recent games", BuildHistoryText(), "history") end)
  f.statsBtn=MakeButton(f,44,16,"Stats",0.12,0.09,0.05,0.9, C.gold)
  f.statsBtn:SetPoint("TOPRIGHT", f.histBtn, "TOPLEFT", -5, 0); f.statsBtn.txt:SetFont("Fonts\\FRIZQT__.TTF",9,"")
  f.statsBtn:SetScript("OnClick", function() ShowInfo("Your statistics", BuildStatsText(), "stats") end)

  -- DISCORD + Copy (precis efter "help")
  f.discordText=MakeText(f,10,"","LEFT"); f.discordText:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-30)
  f.discordText:SetText("Discord: |cff8ab4f8"..(CasinobabeDB.discord or "Casinobabetbc").."|r for help")
  f.copyBtn=MakeButton(f,42,16,"Copy",0.07,0.10,0.14,0.9, C.light)
  f.copyBtn:SetPoint("LEFT", f.discordText, "RIGHT", 6, 0)
  f.copyBtn:SetScript("OnClick", function() ShowDiscord() end)

  -- saldo + cashback
  local cardW,cardH=(W-PAD*2-8)/2, 40
  local balCard=CreateFrame("Frame", nil, f); balCard:SetSize(cardW,cardH); balCard:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-50)
  MakeBacking(balCard,0.05,0.03,0.07,0.78); MakeBorder(balCard,2):SetColor(uc(C.goldDk))
  local bl=MakeText(balCard,8,"","LEFT"); bl:SetPoint("TOPLEFT",7,-5); bl:SetText("YOUR BALANCE"); bl:SetTextColor(uc(C.mute))
  f.balValue=MakeText(balCard,16,"OUTLINE","LEFT"); f.balValue:SetPoint("BOTTOMLEFT",7,5); f.balValue:SetTextColor(uc(C.gold))
  f.balDelta=MakeText(balCard,11,"OUTLINE","LEFT"); f.balDelta:SetPoint("LEFT", f.balValue, "RIGHT", 6, 0); f.balDelta:SetText("")
  local cbCard=CreateFrame("Frame", nil, f); cbCard:SetSize(cardW,cardH); cbCard:SetPoint("TOPRIGHT",f,"TOPRIGHT",-PAD,-50)
  MakeBacking(cbCard,0.03,0.07,0.04,0.78); MakeBorder(cbCard,2):SetColor(0.22,0.5,0.3,1)
  local cl2=MakeText(cbCard,8,"","LEFT"); cl2:SetPoint("TOPLEFT",7,-5); cl2:SetText("CASHBACK READY"); cl2:SetTextColor(0.55,0.82,0.62,1)
  f.cbValue=MakeText(cbCard,16,"OUTLINE","LEFT"); f.cbValue:SetPoint("BOTTOMLEFT",7,5); f.cbValue:SetTextColor(uc(C.green))
  f.cbDelta=MakeText(cbCard,11,"OUTLINE","LEFT"); f.cbDelta:SetPoint("LEFT", f.cbValue, "RIGHT", 6, 0); f.cbDelta:SetText("")
  f.collectBtn=MakeButton(cbCard,50,17,"Collect", 0.83,0.69,0.22,1, {0.1,0.06,0.02}); f.collectBtn:SetPoint("BOTTOMRIGHT",-5,5)
  f.collectBtn.txt:SetFont("Fonts\\FRIZQT__.TTF",10,"")   -- inte fet
  f.collectBtn:SetScript("OnClick", function() CollectCashback() end)

  -- GLOW-halo bakom korten + hornornament som sticker ut (pa fx-lagret -> syns)
  if f.fxLayer then
    local function cardGlow(card, r,g,b)
      local t=f:CreateTexture(nil,"BACKGROUND")     -- bakom kortet: bara halon runtom syns
      t:SetPoint("TOPLEFT", card, "TOPLEFT", -7,7); t:SetPoint("BOTTOMRIGHT", card, "BOTTOMRIGHT", 7,-7)
      t:SetColorTexture(r,g,b,0.10); t:SetBlendMode("ADD")
      fxGlows[#fxGlows+1]={tex=t,base=0.10,amp=0.13,speed=2.0,phase=math.random()*3}
    end
    cardGlow(balCard, 1.0,0.80,0.30)
    cardGlow(cbCard,  0.35,0.95,0.50)
    local function gem(point,dx,dy,sz)
      local t=f.fxLayer:CreateTexture(nil,"OVERLAY"); t:SetTexture("Interface\\Cooldown\\star4")
      t:SetBlendMode("ADD"); t:SetVertexColor(1.0,0.86,0.45); t:SetSize(sz,sz)
      t:SetPoint("CENTER", f, point, dx, dy)
      fxGlows[#fxGlows+1]={tex=t,base=0.5,amp=0.4,speed=1.7,phase=math.random()*3}
    end
    gem("TOPLEFT",5,-5,10); gem("TOPRIGHT",-5,-5,10)
    gem("BOTTOMLEFT",5,5,10); gem("BOTTOMRIGHT",-5,5,10)
    -- mjuk glod bakom titeln
    local tg=f.fxLayer:CreateTexture(nil,"ARTWORK"); tg:SetBlendMode("ADD")
    tg:SetColorTexture(1.0,0.82,0.35,0); tg:SetSize(150,26); tg:SetPoint("TOP",f,"TOP",0,-4)
    fxGlows[#fxGlows+1]={tex=tg,base=0.06,amp=0.10,speed=1.8,phase=0.5}
  end

  -- ONLINE-koll: oppnar Blizzards /who-lista
  f.onlineBtn=MakeButton(f, W-PAD*2, 18, "Click: is Casinobabe online?  (opens /who list)", 0.06,0.05,0.09,0.85, C.gold)
  f.onlineBtn:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-94)
  f.onlineBtn.txt:SetFont("Fonts\\FRIZQT__.TTF",9,"OUTLINE")
  f.onlineBtn:SetScript("OnClick", function() CheckOnline() end)

  -- popup (overlay i konst-bandet)
  f.actionBar=BuildActionBar(f, W, PAD)
  resultFX=BuildResultFX(f)
  BuildTopBanner(f, W, PAD)

  -- PICK A GAME
  local pgl=MakeText(f,9,"OUTLINE"); pgl:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-164); pgl:SetText("PICK A GAME"); pgl:SetTextColor(uc(C.light))
  f.gameChips={}
  local tileW,tileH=(W-PAD*2-12)/3, 36
  for i,g in ipairs(GAMES) do
    local row,col=math.floor((i-1)/3),(i-1)%3
    local chip=MakeChip(f,tileW,tileH,g.name,g.pay,g.icon)
    chip:SetPoint("TOPLEFT",f,"TOPLEFT",PAD+col*(tileW+6),-178-row*(tileH+4))
    chip:SetScript("OnClick", function() SelectGame(g.key) end)
    f.gameChips[g.key]=chip
  end

  -- ROULETTE-farger under roulette-rutan (Red - Green - Black, green x5)
  f.colorRow=CreateFrame("Frame", nil, f); f.colorRow:SetSize(tileW,17); f.colorRow:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-258)
  f.colorChips={}
  local cols={ {k="red",t="Red",r=0.78,g=0.18,b=0.18}, {k="green",t="Green",r=0.15,g=0.55,b=0.25}, {k="black",t="Black",r=0.16,g=0.16,b=0.18} }
  local ccw=(tileW-2*4)/3
  for i,c in ipairs(cols) do
    local b=CreateFrame("Button", nil, f.colorRow); b:SetSize(ccw,17); b:SetPoint("TOPLEFT",f.colorRow,"TOPLEFT",(i-1)*(ccw+4),0)
    MakeBacking(b,c.r*0.55,c.g*0.55,c.b*0.55,0.92); b.border=MakeBorder(b,2); b.border:SetColor(0.3,0.3,0.3,1)
    local t=MakeText(b,8.5,"OUTLINE","CENTER"); t:SetText(c.t); t:SetPoint("CENTER",0,1); t:SetTextColor(uc(C.light))
    if c.k=="green" then local gx=MakeText(b,6,"OUTLINE","CENTER"); gx:SetText("x5"); gx:SetPoint("BOTTOM",0,1); gx:SetTextColor(uc(C.gold)) end
    b:SetScript("OnClick", function()
      selectedColor=c.k
      for _,cb in pairs(f.colorChips) do cb.border:SetColor(0.3,0.3,0.3,1) end
      b.border:SetColor(uc(C.gold)); UpdateDisplay() end)
    f.colorChips[c.k]=b
  end
  f.colorRow:Hide()

  -- CASINOBABE-ordmarke (mellan Pick a game och Bet amount)
  local wm=MakeText(f,18,"","CENTER"); SetFontSafe(wm,"Fonts\\MORPHEUS.TTF",18,"")
  wm:SetPoint("TOP",f,"TOP",0,-278); wm:SetText("Casinobabe"); wm:SetTextColor(uc(C.gold))
  local wl=f:CreateTexture(nil,"OVERLAY"); wl:SetColorTexture(uc(C.goldDk,0.8)); wl:SetSize(64,1); wl:SetPoint("RIGHT",wm,"LEFT",-8,1)
  local wr=f:CreateTexture(nil,"OVERLAY"); wr:SetColorTexture(uc(C.goldDk,0.8)); wr:SetSize(64,1); wr:SetPoint("LEFT",wm,"RIGHT",8,1)

  -- BET AMOUNT
  local bl3=MakeText(f,9,"OUTLINE"); bl3:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-302); bl3:SetText("BET AMOUNT"); bl3:SetTextColor(uc(C.light))
  f.amtChips={}
  local items={}; for _,v in ipairs(QUICK) do items[#items+1]={label=v.."g",val=v} end; items[#items+1]={label="Max",val="max"}
  local awTot=W-PAD*2; local aw=(awTot-7*4)/8
  for i,it in ipairs(items) do
    local chip=MakeChip(f,aw,24,it.label); chip.label:SetFont("Fonts\\FRIZQT__.TTF",9.5,"OUTLINE")
    chip:SetPoint("TOPLEFT",f,"TOPLEFT",PAD+(i-1)*(aw+4),-316)
    chip:SetScript("OnClick", function() SelectAmount(it.val) end)
    f.amtChips[tostring(it.val)]=chip
  end

  -- custom + PLACE BET
  f.amtBox=CreateFrame("EditBox", nil, f); f.amtBox:SetSize(awTot*0.40,26); f.amtBox:SetPoint("TOPLEFT",f,"TOPLEFT",PAD,-344)
  f.amtBox:SetAutoFocus(false); f.amtBox:SetFontObject("ChatFontNormal"); f.amtBox:SetTextInsets(8,8,0,0)
  MakeBacking(f.amtBox,0.07,0.05,0.08,0.85); MakeBorder(f.amtBox,2):SetColor(0.35,0.28,0.2,1)
  f.amtBox:SetScript("OnTextChanged", function(self) local v=tonumber((self:GetText() or ""):match("%d+%.?%d*")); if v and v>0 then SelectAmount(v,true) end end)
  f.amtBox:SetScript("OnEnterPressed", function(self) self:ClearFocus(); PlaceBet() end)
  f.amtBox:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
  local phs=MakeText(f.amtBox,10,"","LEFT"); phs:SetPoint("LEFT",8,0); phs:SetText("custom"); phs:SetTextColor(uc(C.mute)); f.amtPlaceholder=phs
  f.amtBox:SetScript("OnEditFocusGained", function() phs:Hide() end)
  f.amtBox:SetScript("OnEditFocusLost", function(self) if (self:GetText() or "")=="" then phs:Show() end end)

  f.placeBtn=CreateFrame("Button", nil, f); f.placeBtn:SetSize(awTot*0.56,26); f.placeBtn:SetPoint("TOPRIGHT",f,"TOPRIGHT",-PAD,-344)
  MakeBacking(f.placeBtn, uc(C.gold,1)); MakeBorder(f.placeBtn,2):SetColor(uc(C.goldDk))
  f.placeTxt=MakeText(f.placeBtn,12,"","CENTER"); f.placeTxt:SetText("PLACE BET"); f.placeTxt:SetPoint("CENTER"); f.placeTxt:SetTextColor(0.10,0.05,0.02,1)
  local pHl=f.placeBtn:CreateTexture(nil,"HIGHLIGHT"); pHl:SetAllPoints(); pHl:SetColorTexture(1,1,1,0.12)
  f.placeBtn:SetScript("OnClick", function() PlaceBet() end)

  -- UTILITY-rad: Roll | Language | How to play | Games guide
  local ubw=(W-PAD*2-3*4)/4
  f.rollBtn=MakeButton(f,ubw,20,"Roll",0.20,0.13,0.04,0.95, C.gold)
  f.rollBtn:SetPoint("BOTTOMLEFT",f,"BOTTOMLEFT",PAD,30)
  f.rollBtn:SetScript("OnClick", function() if DoRoll then DoRoll() end end)
  f.langBtn=MakeButton(f,ubw,20,(CasinobabeDB.langLabel or "Language"),0.08,0.06,0.12,0.85, C.light)
  f.langBtn:SetPoint("BOTTOMLEFT",f,"BOTTOMLEFT",PAD+ubw+4,30)
  f.langBtn:SetScript("OnClick", function() OpenLanguageMenu() end)
  f.howBtn=MakeButton(f,ubw,20,"How to play",0.08,0.06,0.12,0.85, C.light)
  f.howBtn:SetPoint("BOTTOMLEFT",f,"BOTTOMLEFT",PAD+2*(ubw+4),30)
  f.howBtn:SetScript("OnClick", function() ShowInfo(HowToTitle(), HowToText()) end)
  f.gamesBtn=MakeButton(f,ubw,20,"Games guide",0.08,0.06,0.12,0.85, C.light)
  f.gamesBtn:SetPoint("BOTTOMLEFT",f,"BOTTOMLEFT",PAD+3*(ubw+4),30)
  f.gamesBtn:SetScript("OnClick", function() ShowInfo(GuideTitle(), GuideText()) end)

  -- STATUS (tunn rad langst ner)
  local statBar=CreateFrame("Frame", nil, f); statBar:SetSize(W-PAD*2,16); statBar:SetPoint("BOTTOMLEFT",f,"BOTTOMLEFT",PAD,10)
  MakeBacking(statBar,0.04,0.03,0.05,0.85); MakeBorder(statBar,1):SetColor(0.25,0.2,0.15,1)
  f.statusText=MakeText(statBar,8.5,"","LEFT"); f.statusText:SetPoint("LEFT",6,0); f.statusText:SetText("Connecting..."); f.statusText:SetTextColor(uc(C.light))
  f.connDot=MakeText(statBar,8.5,"OUTLINE","RIGHT"); f.connDot:SetPoint("RIGHT",-6,0); f.connDot:SetText("offline"); f.connDot:SetTextColor(uc(C.mute))

  -- ============================================================================
  -- DEALER PANEL (hidden by default)
  -- ============================================================================
  local function CreateDealerPanel(parent)
    local dp = CreateFrame("Frame", nil, parent)
    dp:SetPoint("TOPLEFT", parent, "TOPLEFT", PAD, -120)
    dp:SetPoint("BOTTOMRIGHT", parent, "BOTTOMRIGHT", -PAD, 30)
    dp:Hide()
    
    -- Background
    local bg = dp:CreateTexture(nil, "BACKGROUND")
    bg:SetAllPoints()
    bg:SetColorTexture(0.04, 0.02, 0.06, 0.95)
    local border = MakeBorder(dp, 2)
    border:SetColor(uc(C.goldDk))
    
    -- Title
    dp.title = MakeText(dp, 14, "OUTLINE", "CENTER")
    dp.title:SetPoint("TOP", dp, "TOP", 0, -8)
    dp.title:SetText("|cffFFD700CASINOBAE â€” DEALER|r")
    dp.title:SetTextColor(uc(C.gold))
    
    -- Dealer mode toggle
    dp.modeBtn = MakeButton(dp, 100, 22, "Dealer: OFF", 0.20, 0.13, 0.04, 0.95, C.gold)
    dp.modeBtn:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -36)
    dp.modeBtn:SetScript("OnClick", function()
      DealerToggle(dealerEnabled and "off" or "on")
      DealerUpdateUI()
    end)
    
    -- Connection status
    dp.connStatus = MakeText(dp, 10, "OUTLINE", "LEFT")
    dp.connStatus:SetPoint("LEFT", dp.modeBtn, "RIGHT", 12, 0)
    dp.connStatus:SetText("OFF")
    dp.connStatus:SetTextColor(uc(C.mute))
    
    dp.connReason = MakeText(dp, 9, "", "LEFT")
    dp.connReason:SetPoint("LEFT", dp.connStatus, "RIGHT", 8, 0)
    dp.connReason:SetText("")
    dp.connReason:SetTextColor(uc(C.gray))
    
    -- Advertise button
    dp.adBtn = MakeButton(dp, 80, 22, "Advertise", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.adBtn:SetPoint("LEFT", dp.modeBtn, "RIGHT", 8, 0)
    dp.adBtn:SetScript("OnClick", function() DealerAdvertise() end)
    
    -- Quick Ad button
    dp.quickAdBtn = MakeButton(dp, 80, 22, "Quick Ad", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.quickAdBtn:SetPoint("LEFT", dp.adBtn, "RIGHT", 8, 0)
    dp.quickAdBtn:SetScript("OnClick", function() CasinoShow:StartQuickAd() end)
    
    -- CasinoShow Control Center
    dp.showBtn = MakeButton(dp, 120, 26, "ðŸŽ° OPEN THE CASINO", 0.8, 0.5, 0.1, 0.95, C.gold)
    dp.showBtn:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -64)
    dp.showBtn:SetScript("OnClick", function()
      if CasinoAnnouncer:IsShowing() then
        CasinoShow:StopShow()
      else
        CasinoShow:StartShow()
      end
    end)
    
    -- Show variant selector (custom dropdown for Classic compatibility)
    dp.variantDropdown = CreateFrame("Frame", nil, dp)
    dp.variantDropdown:SetSize(140, 22)
    dp.variantDropdown:SetPoint("LEFT", dp.showBtn, "RIGHT", 8, 0)
    local variantBg = dp.variantDropdown:CreateTexture(nil, "BACKGROUND")
    variantBg:SetAllPoints()
    variantBg:SetColorTexture(0.08, 0.06, 0.10, 0.95)
    local variantBorder = MakeBorder(dp.variantDropdown, 2)
    variantBorder:SetColor(0.3, 0.3, 0.3, 1)
    dp.variantDropdown.text = MakeText(dp.variantDropdown, 10, "OUTLINE", "LEFT")
    dp.variantDropdown.text:SetPoint("LEFT", dp.variantDropdown, "LEFT", 8, 0)
    dp.variantDropdown.text:SetText("Select Show")
    dp.variantDropdown.text:SetTextColor(uc(C.light))
    local variantArrow = dp.variantDropdown:CreateTexture(nil, "ARTWORK")
    variantArrow:SetSize(16, 16)
    variantArrow:SetPoint("RIGHT", dp.variantDropdown, "RIGHT", -6, 0)
    variantArrow:SetTexture("Interface\\ChatFrame\\ChatFrameExpandArrow")
    local variantDropdownOpen = false
    local variantDropdownMenu = nil
    
    local function ToggleVariantDropdown()
      if variantDropdownOpen then
        if variantDropdownMenu then
          variantDropdownMenu:Hide()
        end
        variantDropdownOpen = false
      else
        if not variantDropdownMenu then
          variantDropdownMenu = CreateFrame("Frame", nil, dp.variantDropdown)
          variantDropdownMenu:SetSize(140, 1)
          variantDropdownMenu:SetPoint("TOPLEFT", dp.variantDropdown, "BOTTOMLEFT", 0, -2)
          variantDropdownMenu:SetFrameLevel(dp.variantDropdown:GetFrameLevel() + 10)
          local menuBg = variantDropdownMenu:CreateTexture(nil, "BACKGROUND")
          menuBg:SetAllPoints()
          menuBg:SetColorTexture(0.06, 0.04, 0.08, 0.98)
          local menuBorder = MakeBorder(variantDropdownMenu, 2)
          menuBorder:SetColor(uc(C.goldDk))
          local variants = CasinoShow:GetVariants()
          local menuHeight = 0
          for _, v in ipairs(variants) do
            local item = CreateFrame("Button", nil, variantDropdownMenu)
            item:SetSize(136, 20)
            item:SetPoint("TOPLEFT", variantDropdownMenu, "TOPLEFT", 2, -menuHeight)
            local itemBg = item:CreateTexture(nil, "BACKGROUND")
            itemBg:SetAllPoints()
            itemBg:SetColorTexture(0.1, 0.08, 0.12, 0.95)
            local itemHl = item:CreateTexture(nil, "HIGHLIGHT")
            itemHl:SetAllPoints()
            itemHl:SetColorTexture(1, 0.85, 0.3, 0.15)
            local itemText = MakeText(item, 9, "", "LEFT")
            itemText:SetPoint("LEFT", item, "LEFT", 6, 0)
            itemText:SetText(v.label)
            itemText:SetTextColor(uc(C.light))
            item:SetScript("OnClick", function()
              CasinoShow:StartShow(v.name)
              if variantDropdownMenu then variantDropdownMenu:Hide() end
              variantDropdownOpen = false
            end)
            menuHeight = menuHeight + 20
          end
          variantDropdownMenu:SetHeight(menuHeight)
        end
        variantDropdownMenu:Show()
        variantDropdownOpen = true
      end
    end
    
    dp.variantDropdown:SetScript("OnMouseDown", ToggleVariantDropdown)
    dp.variantDropdown:SetScript("OnEnter", function(self)
      variantBorder:SetColor(uc(C.gold))
    end)
    dp.variantDropdown:SetScript("OnLeave", function(self)
      if not variantDropdownOpen then
        variantBorder:SetColor(0.3, 0.3, 0.3, 1)
      end
    end)
    
    -- Quick Ad button
    dp.quickAdBtn = MakeButton(dp, 100, 22, "Quick Ad", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.quickAdBtn:SetPoint("LEFT", dp.variantDropdown, "RIGHT", 8, 0)
    dp.quickAdBtn:SetScript("OnClick", function() CasinoShow:StartQuickAd() end)
    
    -- Stop Show button
    dp.stopBtn = MakeButton(dp, 80, 22, "â–  STOP", 0.12, 0.05, 0.05, 0.92, C.red)
    dp.stopBtn:SetPoint("LEFT", dp.quickAdBtn, "RIGHT", 8, 0)
    dp.stopBtn:SetScript("OnClick", function() CasinoShow:StopShow() end)
    
    -- Status display
    dp.statusText = MakeText(dp, 10, "OUTLINE", "LEFT")
    dp.statusText:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -100)
    dp.statusText:SetText("|cff78EB96READY|r")
    dp.statusText:SetTextColor(0.5, 1, 0.5, 1)
    
    -- Suggested emote button
    dp.emoteBtn = MakeButton(dp, 120, 22, "ðŸŽ­ EMOTE", 0.5, 0.2, 0.5, 0.85, C.gold)
    dp.emoteBtn:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -120)
    dp.emoteBtn:Hide()
    dp.emoteBtn:SetScript("OnClick", function()
      local emote = CasinoShow.suggestedEmote
      if emote then
        CasinoEmote:PlayPhysical(emote)
        CasinoShow:ClearSuggestedEmote()
      end
    end)
    
    -- Suggested FX display
    dp.fxText = MakeText(dp, 10, "OUTLINE", "LEFT")
    dp.fxText:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -145)
    dp.fxText:Hide()
    
    -- Session list header
    local listHeader = MakeText(dp, 10, "OUTLINE", "LEFT")
    listHeader:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -165)
    listHeader:SetText("ACTIVE SESSIONS")
    listHeader:SetTextColor(uc(C.light))
    
    -- Session list (scrollable)
    local listFrame = CreateFrame("ScrollFrame", nil, dp, "UIPanelScrollFrameTemplate")
    listFrame:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -88)
    listFrame:SetPoint("BOTTOMRIGHT", dp, "BOTTOMRIGHT", -28, 8)
    listFrame:SetFrameLevel(dp:GetFrameLevel() + 1)
    
    local listContent = CreateFrame("Frame", nil, listFrame)
    listContent:SetSize(W - PAD * 2 - 36, 1)
    listFrame:SetScrollChild(listContent)
    dp.listContent = listContent
    dp.listFrame = listFrame
    
    -- Detail panel (right side, shown when session selected)
    dp.detail = CreateFrame("Frame", nil, dp)
    dp.detail:SetPoint("TOPLEFT", dp, "TOPLEFT", 8, -88)
    dp.detail:SetPoint("BOTTOMRIGHT", dp, "BOTTOMRIGHT", -8, 8)
    dp.detail:Hide()
    
    local detailBg = dp.detail:CreateTexture(nil, "BACKGROUND")
    detailBg:SetAllPoints()
    detailBg:SetColorTexture(0.06, 0.04, 0.08, 0.98)
    local detailBorder = MakeBorder(dp.detail, 2)
    detailBorder:SetColor(uc(C.goldDk))
    
    dp.detail.title = MakeText(dp.detail, 13, "OUTLINE", "CENTER")
    dp.detail.title:SetPoint("TOP", dp.detail, "TOP", 0, -10)
    dp.detail.title:SetTextColor(uc(C.gold))
    
    dp.detail.stateText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.stateText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -36)
    dp.detail.stateText:SetTextColor(uc(C.light))
    
    dp.detail.gameText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.gameText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -54)
    dp.detail.gameText:SetTextColor(uc(C.light))
    
    dp.detail.stakeText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.stakeText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -72)
    dp.detail.stakeText:SetTextColor(uc(C.light))
    
    dp.detail.tradeText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.tradeText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -90)
    dp.detail.tradeText:SetTextColor(uc(C.light))
    
    dp.detail.groupText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.groupText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -108)
    dp.detail.groupText:SetTextColor(uc(C.light))
    
    dp.detail.rollText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.rollText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -126)
    dp.detail.rollText:SetTextColor(uc(C.light))
    
    dp.detail.resultText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.resultText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -144)
    dp.detail.resultText:SetTextColor(uc(C.light))
    
    dp.detail.payoutText = MakeText(dp.detail, 11, "", "LEFT")
    dp.detail.payoutText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -162)
    dp.detail.payoutText:SetTextColor(uc(C.light))
    
    dp.detail.errorText = MakeText(dp.detail, 10, "", "LEFT")
    dp.detail.errorText:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, -180)
    dp.detail.errorText:SetTextColor(uc(C.red))
    
    -- Action buttons
    local btnY = -210
    local btnW = 70
    local btnH = 22
    local btnGap = 6
    
    dp.detail.btnInvite = MakeButton(dp.detail, btnW, btnH, "INVITE", 0.20, 0.13, 0.04, 0.95, C.gold)
    dp.detail.btnInvite:SetPoint("TOPLEFT", dp.detail, "TOPLEFT", 12, btnY)
    dp.detail.btnInvite:SetScript("OnClick", function() if dp.detail.currentPlayer then DealerInvite(dp.detail.currentPlayer) end end)
    
    dp.detail.btnTrade = MakeButton(dp.detail, btnW, btnH, "ARM TRADE", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.detail.btnTrade:SetPoint("LEFT", dp.detail.btnInvite, "RIGHT", btnGap, 0)
    dp.detail.btnTrade:SetScript("OnClick", function() if dp.detail.currentPlayer then print("|cffFFD700Casinobabe|r Use /cb dealer stake " .. dp.detail.currentPlayer .. " <amount>") end end)
    
    dp.detail.btnVerify = MakeButton(dp.detail, btnW, btnH, "VERIFY", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.detail.btnVerify:SetPoint("LEFT", dp.detail.btnTrade, "RIGHT", btnGap, 0)
    dp.detail.btnVerify:SetScript("OnClick", function() if dp.detail.currentPlayer then print("|cffFFD700Casinobabe|r Trade verification is automatic") end end)
    
    dp.detail.btnGroup = MakeButton(dp.detail, btnW, btnH, "GROUP", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.detail.btnGroup:SetPoint("TOPLEFT", dp.detail.btnInvite, "BOTTOMLEFT", 0, -4)
    dp.detail.btnGroup:SetScript("OnClick", function() if dp.detail.currentPlayer then DealerInvite(dp.detail.currentPlayer) end end)
    
    dp.detail.btnGame = MakeButton(dp.detail, btnW, btnH, "GAME", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.detail.btnGame:SetPoint("LEFT", dp.detail.btnGroup, "RIGHT", btnGap, 0)
    dp.detail.btnGame:SetScript("OnClick", function() if dp.detail.currentPlayer then print("|cffFFD700Casinobabe|r Use /cb dealer game " .. dp.detail.currentPlayer .. " <game>") end end)
    
    dp.detail.btnStart = MakeButton(dp.detail, btnW, btnH, "START", 0.20, 0.13, 0.04, 0.95, C.gold)
    dp.detail.btnStart:SetPoint("LEFT", dp.detail.btnGame, "RIGHT", btnGap, 0)
    dp.detail.btnStart:SetScript("OnClick", function() if dp.detail.currentPlayer then print("|cffFFD700Casinobabe|r Waiting for player roll...") end end)
    
    dp.detail.btnResolve = MakeButton(dp.detail, btnW, btnH, "RESOLVE", 0.20, 0.13, 0.04, 0.95, C.gold)
    dp.detail.btnResolve:SetPoint("TOPLEFT", dp.detail.btnGroup, "BOTTOMLEFT", 0, -4)
    dp.detail.btnResolve:SetScript("OnClick", function() if dp.detail.currentPlayer then DealerResolve(dp.detail.currentPlayer) end end)
    
    dp.detail.btnPayout = MakeButton(dp.detail, btnW, btnH, "PAYOUT", 0.08, 0.06, 0.12, 0.85, C.light)
    dp.detail.btnPayout:SetPoint("LEFT", dp.detail.btnResolve, "RIGHT", btnGap, 0)
    dp.detail.btnPayout:SetScript("OnClick", function() if dp.detail.currentPlayer then DealerPayout(dp.detail.currentPlayer) end end)
    
    dp.detail.btnClose = MakeButton(dp.detail, btnW, btnH, "CLOSE", 0.12, 0.05, 0.05, 0.92, C.red)
    dp.detail.btnClose:SetPoint("LEFT", dp.detail.btnPayout, "RIGHT", btnGap, 0)
    dp.detail.btnClose:SetScript("OnClick", function() if dp.detail.currentPlayer then DealerClose(dp.detail.currentPlayer) end end)
    
    dp.detail.btnCancel = MakeButton(dp.detail, btnW, btnH, "CANCEL", 0.12, 0.05, 0.05, 0.92, C.red)
    dp.detail.btnCancel:SetPoint("TOPLEFT", dp.detail.btnResolve, "BOTTOMLEFT", 0, -4)
    dp.detail.btnCancel:SetScript("OnClick", function() if dp.detail.currentPlayer then DealerReset(dp.detail.currentPlayer) end end)
    
    dp.detail.currentPlayer = nil
    
    -- Assign to CasinoShow for UI updates
    CasinoShow.showPanel = dp
    
    return dp
  end
  
  f.dealerPanel = CreateDealerPanel(f)
  f.currentTab = "player"
  
  local function SwitchTab(tab)
    f.currentTab = tab
    if tab == "player" then
      f.playerTab.bg:SetColorTexture(0.12, 0.09, 0.05, 0.9)
      f.playerTab.txt:SetTextColor(uc(C.gold))
      f.playerTab.border:SetColor(uc(C.goldDk))
      f.dealerTab.bg:SetColorTexture(0.08, 0.06, 0.12, 0.85)
      f.dealerTab.txt:SetTextColor(uc(C.light))
      f.dealerTab.border:SetColor(0.3, 0.3, 0.3, 1)
      if f.dealerPanel then f.dealerPanel:Hide() end
      -- Show player content
      for _, v in pairs({f.blockBtn, f.balValue:GetParent(), f.cbValue:GetParent(), f.onlineBtn, f.gameChips, f.colorRow, f.amtBox, f.placeBtn, f.rollBtn, f.langBtn, f.howBtn, f.gamesBtn}) do
        if type(v) == "table" and v.Show then v:Show() end
      end
      if f.gameChips then for _, c in pairs(f.gameChips) do c:Show() end end
      if f.amtChips then for _, c in pairs(f.amtChips) do c:Show() end end
      if f.colorChips then for _, c in pairs(f.colorChips) do c:Show() end end
    else
      f.dealerTab.bg:SetColorTexture(0.12, 0.09, 0.05, 0.9)
      f.dealerTab.txt:SetTextColor(uc(C.gold))
      f.dealerTab.border:SetColor(uc(C.goldDk))
      f.playerTab.bg:SetColorTexture(0.08, 0.06, 0.12, 0.85)
      f.playerTab.txt:SetTextColor(uc(C.light))
      f.playerTab.border:SetColor(0.3, 0.3, 0.3, 1)
      -- Hide player content
      for _, v in pairs({f.blockBtn, f.balValue:GetParent(), f.cbValue:GetParent(), f.onlineBtn, f.gameChips, f.colorRow, f.amtBox, f.placeBtn, f.rollBtn, f.langBtn, f.howBtn, f.gamesBtn}) do
        if type(v) == "table" and v.Hide then v:Hide() end
      end
      if f.gameChips then for _, c in pairs(f.gameChips) do c:Hide() end end
      if f.amtChips then for _, c in pairs(f.amtChips) do c:Hide() end end
      if f.colorChips then for _, c in pairs(f.colorChips) do c:Hide() end end
      -- Show dealer panel
      if f.dealerPanel then f.dealerPanel:Show(); DealerUpdateUI() end
    end
  end
  
  f.playerTab:SetScript("OnClick", function() SwitchTab("player") end)
  f.dealerTab:SetScript("OnClick", function() SwitchTab("dealer") end)
  
  -- Initialize tab state
  SwitchTab("player")

  -- Escape stanger panelen (lagg till i UISpecialFrames, en gang).
  if UISpecialFrames then
    local found=false
    for _,n in ipairs(UISpecialFrames) do if n=="Casinobabe_Panel" then found=true break end end
    if not found then table.insert(UISpecialFrames, "Casinobabe_Panel") end
  end

  return f
end

-- ============================================================================
-- LOGIK
-- ============================================================================
function SelectGame(key)
  selectedGame=key; CasinobabeDB.lastGame=key
  for k,chip in pairs(panel.gameChips) do chip:SetSelected(k==key) end
  if key=="roulette" then panel.colorRow:Show() else panel.colorRow:Hide(); selectedColor=nil end
  UpdateDisplay()
end
function SelectAmount(val,fromBox)
  if val=="max" then val=math.min(math.floor(CasinobabeDB.bal or 0), MAX_BET) end
  if type(val)=="number" then
    val=math.floor(val)
    if val>MAX_BET then  -- kapa vid casinots tak (1000g), oavsett saldo
      val=MAX_BET
      if fromBox and panel then SetStatus(("Max bet is %dg."):format(MAX_BET), C.gold) end
    end
  end
  selectedAmount=val; if val and val~="max" then CasinobabeDB.lastAmount=val end
  for k,chip in pairs(panel.amtChips) do chip:SetSelected(tostring(k)==tostring(val)) end
  if not fromBox and panel.amtBox then panel.amtBox:SetText(""); panel.amtBox:ClearFocus(); panel.amtPlaceholder:Show() end
  UpdateDisplay()
end
function UpdateDisplay()
  if not panel then return end
  panel.balValue:SetText(math.floor(CasinobabeDB.bal or 0).."g")
  local cb=math.floor(CasinobabeDB.cashback or 0); panel.cbValue:SetText(cb.."g")
  if cb>0 then panel.collectBtn:Show() else panel.collectBtn:Hide() end
  local amt=selectedAmount or CasinobabeDB.lastAmount
  local gname; for _,g in ipairs(GAMES) do if g.key==selectedGame then gname=g.name end end
  if selectedGame and amt and amt~="max" then
    local extra=(selectedGame=="roulette" and selectedColor) and (" "..selectedColor) or ""
    panel.placeTxt:SetText("BET "..amt.."g  "..(gname or "")..extra)
  else panel.placeTxt:SetText("PLACE BET") end
  if panel.blockBtn then
    if CasinobabeDB.blockTrades then panel.blockBtn.txt:SetText("Trades OFF"); panel.blockBtn.txt:SetTextColor(uc(C.red)); panel.blockBtn.border:SetColor(uc(C.red))
    else panel.blockBtn.txt:SetText("Block Trades"); panel.blockBtn.txt:SetTextColor(uc(C.light)); panel.blockBtn.border:SetColor(uc(C.goldDk)) end
  end
end
function SetStatus(txt,color)
  if not panel then return end
  panel.statusText:SetText(txt or "")
  panel.statusText:SetTextColor(color and color[1] or C.light[1], color and color[2] or C.light[2], color and color[3] or C.light[3])
end
function SetConnected(on)
  connected=on; if not panel then return end
  if on then panel.connDot:SetText("connected"); panel.connDot:SetTextColor(uc(C.green)) else panel.connDot:SetText("offline"); panel.connDot:SetTextColor(uc(C.mute)) end
end
function FlashResult(won,amount,game)
  if won then SetStatus(("WIN! +%dg %s"):format(amount or 0, game or ""), C.green) else SetStatus(("Lost %dg on %s. GL next!"):format(amount or 0, game or ""), C.red) end
end

-- ============================================================================
-- STATISTIK + HISTORIK
-- ============================================================================
local GAME_LABEL={ normal="Normal", high="High Risk", blackjack="Blackjack", roulette="Roulette", dice="Dice", lucky7="Lucky 7" }
-- spara ett avgjort spel: won=true/false, net=vinst (positiv) eller -insats
function RecordResult(game, stake, won, net)
  if not (game and stake) then return end
  CasinobabeDB.stats = CasinobabeDB.stats or { byGame={} }
  CasinobabeDB.history = CasinobabeDB.history or {}
  local s=CasinobabeDB.stats; s.byGame=s.byGame or {}
  s.games=(s.games or 0)+1
  s.wins=(s.wins or 0)+(won and 1 or 0)
  s.wagered=(s.wagered or 0)+stake
  if won then s.wonGold=(s.wonGold or 0)+(net or 0) else s.lostGold=(s.lostGold or 0)+stake end
  local g=s.byGame[game] or {games=0,wins=0,wagered=0,won=0,lost=0}
  g.games=g.games+1; g.wins=g.wins+(won and 1 or 0); g.wagered=g.wagered+stake
  if won then g.won=g.won+(net or 0) else g.lost=g.lost+stake end
  s.byGame[game]=g
  table.insert(CasinobabeDB.history, 1, { game=game, won=won, net=(won and (net or 0) or -stake) })
  while #CasinobabeDB.history>20 do table.remove(CasinobabeDB.history) end
  if panel and panel.statsBtn then end -- (plats for ev. live-uppdatering)
end
local function signG(v) v=math.floor((v or 0)+0.5); if v>=0 then return "78EB96","+"..v.."g" else return "EB5E4F","-"..(-v).."g" end end
function BuildStatsText()
  local s=CasinobabeDB.stats or {}
  local games=s.games or 0
  if games==0 then return "No games played yet.\n\nPlay a few rounds and your stats show up here!" end
  local won=math.floor(s.wonGold or 0); local lost=math.floor(s.lostGold or 0); local net=won-lost
  local pct=games>0 and (s.wins or 0)/games*100 or 0
  local ncol,ntxt=signG(net)
  local L={}
  L[#L+1]=string.format("|cffFFD700OVERALL|r   |cffB3ABA6(%d games)|r", games)
  L[#L+1]=string.format("Win rate:   |cffFFFFFF%.0f%%|r   |cffB3ABA6(%d wins)|r", pct, s.wins or 0)
  L[#L+1]=string.format("Total won:  |cff78EB96+%dg|r", won)
  L[#L+1]=string.format("Total lost: |cffEB5E4F-%dg|r", lost)
  L[#L+1]=string.format("Net result: |cff%s%s|r", ncol, ntxt)
  L[#L+1]=string.format("Total wagered: |cffFFFFFF%dg|r", math.floor(s.wagered or 0))
  L[#L+1]=""
  L[#L+1]="|cffFFD700BY GAME MODE|r"
  for _,k in ipairs({"normal","high","blackjack","roulette","dice","lucky7"}) do
    local g=s.byGame and s.byGame[k]
    if g and g.games>0 then
      local gnet=(g.won or 0)-(g.lost or 0); local gpct=g.wins/g.games*100
      local gc,gt=signG(gnet)
      L[#L+1]=string.format("|cffFFFFFF%s|r  |cffB3ABA6%d played, %.0f%% win|r  net |cff%s%s|r",
        GAME_LABEL[k] or k, g.games, gpct, gc, gt)
    end
  end
  return table.concat(L,"\n")
end
function BuildHistoryText()
  local h=CasinobabeDB.history or {}
  if #h==0 then return "No games played yet.\n\nYour last 20 games will be listed here." end
  local L={"|cffB3ABA6Newest first - your last 20 games & cashbacks|r",""}
  for i=1,#h do
    local e=h[i]
    if e.cashback then
      L[#L+1]=string.format("|cffFFD700Cashback claimed|r  -  |cff78EB96+%dg|r", math.floor(e.amount or 0))
    else
      local lab=GAME_LABEL[e.game] or e.game
      if e.won then L[#L+1]=string.format("|cffFFFFFF%s|r  -  |cff78EB96WIN  +%dg|r", lab, math.floor(e.net))
      else L[#L+1]=string.format("|cffFFFFFF%s|r  -  |cffEB5E4Flost  -%dg|r", lab, math.floor(math.abs(e.net))) end
    end
  end
  return table.concat(L,"\n")
end

-- liten +Xg / -Xg bredvid saldo, slacks efter nagra sekunder
function SetBalDelta(delta)
  if not (panel and panel.balDelta) then return end
  delta=math.floor((delta or 0)+0.5)
  if delta>0 then panel.balDelta:SetText("+"..delta.."g"); panel.balDelta:SetTextColor(uc(C.green))
  elseif delta<0 then panel.balDelta:SetText("-"..(-delta).."g"); panel.balDelta:SetTextColor(uc(C.red))
  else panel.balDelta:SetText("") end
  balDeltaToken=balDeltaToken+1; local tk=balDeltaToken
  if C_Timer and C_Timer.After then C_Timer.After(5, function()
    if balDeltaToken==tk and panel and panel.balDelta then panel.balDelta:SetText("") end end) end
end
function SetCbDelta(delta)
  if not (panel and panel.cbDelta) then return end
  delta=math.floor((delta or 0)+0.5)
  if delta>0 then panel.cbDelta:SetText("+"..delta.."g"); panel.cbDelta:SetTextColor(uc(C.green)) else panel.cbDelta:SetText("") end
  cbDeltaToken=cbDeltaToken+1; local tk=cbDeltaToken
  if C_Timer and C_Timer.After then C_Timer.After(5, function()
    if cbDeltaToken==tk and panel and panel.cbDelta then panel.cbDelta:SetText("") end end) end
end

-- raknar ut resultatet av en rull direkt fran reglerna (0 = forlust, nil = okant spel)
-- NOW USES GameRules as single source of truth
local function rollOutcome(game, roll, color)
  local rule = GetGameRule(game)
  if not rule then return nil end
  if game == "roulette" then
    return rule.resolve(roll, color)
  else
    return rule.resolve(roll)
  end
end

-- nar VI sjalva rullar: visa siffran och (for enkla spel) avgor vinst/forlust direkt
function OnMyRoll(roll)
  if not (pending and pending.started) then return end
  if pending.game=="blackjack" then
    pending.bjTotal=(pending.bjTotal or 0)+roll
    if pending.bjTotal>100 then
      SetCenterInfo("BUST!  "..pending.bjTotal, C.red)
      SetBalDelta(-pending.stake); SetStatus(("BUST at %d! Lost %dg."):format(pending.bjTotal, pending.stake), C.red)
      RecordResult("blackjack", pending.stake, false, -pending.stake)
      ShowResult(false, pending.stake)
      pending=nil
      PopupHideLater(0.8)
    else
      SetCenterInfo("YOUR TOTAL:  "..pending.bjTotal, C.gold)
      SetStatus(("Rolled %d - Hit or Stand?"):format(roll), C.light)
    end
    return
  end
  -- enkla spel: rakna ut direkt
  local mult=rollOutcome(pending.game, roll, pending.color)
  if mult==nil then  -- dice: spelaren rullar inte; ignorera
    pending.lastRoll=roll; return
  end
  pending.lastRoll=roll
  -- vad rullades? for roulette visar vi FARGEN (annars siffran)
  local rolledStr
  if pending.game=="roulette" then
    local cname
    if roll<=47 then cname="RED" elseif roll<=52 then cname="GREEN" else cname="BLACK" end
    rolledStr=string.format("Rolled %s (%d)", cname, roll)
  else
    rolledStr=string.format("Rolled %d", roll)
  end
  if mult>0 then
    local net=pending.stake*(mult-1)
    SetBalDelta(net); SetStatus(("%s - WIN! +%dg on %s"):format(rolledStr, net, pending.game), C.green)
    RecordResult(pending.game, pending.stake, true, net)
    ShowResult(true, net)
  else
    SetBalDelta(-pending.stake); SetStatus(("%s - lost %dg on %s. GL next!"):format(rolledStr, pending.stake, pending.game), C.red)
    RecordResult(pending.game, pending.stake, false, -pending.stake)
    ShowResult(false, pending.stake)
  end
  pending=nil
  PopupHideLater(0.8)
end
-- Auto-anslutning. Forr skickades EN enda HELLO och sen gavs det upp - hade
-- spelaren inte hunnit in i casinots raid annu (eller tappades meddelandet)
-- stod saldo/cashback kvar pa 0 tills man reloadade eller oppnade panelen igen.
-- Nu forsoker vi flera ganger, och gruppandringar triggar ett nytt forsok.
local connTkn=0
function RequestState(tries)
  myName=myName or shortName(UnitName("player"))
  connTkn=connTkn+1
  local tkn=connTkn
  tries=tries or 0
  
  -- For dealer: connection is based on dealer state, not player handshake
  if dealerEnabled then
    -- Dealer connection handled separately via DealerSetConnState
    -- Just ensure we're registered for addon messages
    if not connected then
      SendControl("HELLO")
    end
    return
  end
  
  -- Player side: need raid/group to connect to casino
  if not groupChannel() then
    SetStatus("Join the casino's raid to connect.", C.gold); SetConnected(false); return
  end
  if tries==0 then SetStatus("Connecting...") end
  SendControl("HELLO")
  if C_Timer and C_Timer.After then
    C_Timer.After(4, function()
      if tkn~=connTkn then return end          -- ett nyare forsok har startat
      if connected then return end             -- vi fick svar, klart
      if tries < 5 then
        RequestState(tries+1)                  -- forsok igen (upp till ~20s)
      else
        SetStatus("No reply - are you in the casino's raid?", C.mute); SetConnected(false)
      end
    end)
  end
end
function PlaceBet()
  if not selectedGame then SetStatus("Pick a game first.", C.gold); return end
  if selectedGame=="roulette" and not selectedColor then SetStatus("Pick Red / Green / Black.", C.gold); return end
  local now=(GetTime and GetTime()) or 0
  if (now-lastBetTime)<1.0 then SetStatus("Easy! One bet per second.", C.gold); return end
  if pending then SetStatus("Finish the current round first.", C.gold); return end
  local amt=selectedAmount or CasinobabeDB.lastAmount
  if amt=="max" then amt=math.min(math.floor(CasinobabeDB.bal or 0), MAX_BET) end
  amt=math.floor(tonumber(amt) or 0)
  if amt<MIN_BET then SetStatus("Pick an amount.", C.gold); return end
  if amt>MAX_BET then amt=MAX_BET end   -- aldrig over casinots tak
  if amt>(CasinobabeDB.bal or 0) then SetStatus(("Not enough balance (%dg)."):format(math.floor(CasinobabeDB.bal or 0)), C.red); return end
  local text=betCommand(selectedGame, amt, selectedColor)
  if PostPublic(text) then
    lastBetTime=now
    HideTopBanner()
    SetStatus(("Bet sent (%dg on %s) - waiting for the casino to confirm..."):format(amt, selectedGame))
    pending={ stake=amt, game=selectedGame, color=selectedColor, balBefore=(CasinobabeDB.bal or 0), expectAfter=(CasinobabeDB.bal or 0)-amt, sawDeduction=false, started=false, actionTaken=false, bjTotal=0, lastRoll=nil }
    PopupStart(selectedGame, amt, selectedColor)
    local myGen=popupGen
    -- INGEN blind tvangsstart langre. ALLA spel kraver att casinot bekraftar betet
    -- (deduction-STATE) innan ROLL/HIT/OVER visas. Sa kan ingen "spela" mot ett
    -- offline-casino och fa fejk-resultat.
    -- Efter 2.5s utan bekraftelse: re-pinga casinot (HELLO) ifall en STATE tappades
    -- (online-casino vars svar missades -> casinot svarar med aktuell balans och
    -- spelet startar). Ar casinot offline kommer inget svar.
    if C_Timer and C_Timer.After then C_Timer.After(2.5, function()
      if popupGen==myGen and pending and not pending.started then
        if SendControl then SendControl("HELLO") end
      end end) end
    -- Efter 6s utan bekraftelse: casinot svarar inte (offline / inte i raiden /
    -- betet processades aldrig). Avbryt - INGEN insats drogs, inget fejk-resultat,
    -- ingen statistik. Spelarens saldo ar orort.
    if C_Timer and C_Timer.After then C_Timer.After(6, function()
      if popupGen==myGen and pending and not pending.started then
        NoResponse()
      end end) end
    -- 40s nodsettle om en runda aldrig loses
    if C_Timer and C_Timer.After then C_Timer.After(40, function()
      if popupGen==myGen and pending then
        local now=CasinobabeDB.bal or 0
        if now>pending.expectAfter+0.5 then
          local net=now-pending.balBefore; SetBalDelta(net)
          FlashResult(true, math.floor(net+0.5), pending.game)
          RecordResult(pending.game, pending.stake, true, net); ShowResult(true, math.floor(net+0.5))
        elseif pending.sawDeduction then
          SetBalDelta(-pending.stake); FlashResult(false, pending.stake, pending.game)
          RecordResult(pending.game, pending.stake, false, -pending.stake); ShowResult(false, pending.stake)
        end
        pending=nil; PopupHideLater(0.6)
      end end) end
  end
end
function CollectCashback()
  local amt=math.floor((CasinobabeDB.cashback or 0))
  if amt<=0 then SetStatus("No cashback to collect.", C.mute); return end
  -- Skicka hemlig kod via addon-kanalen (osynligt). Bara addonet kan skicka
  -- detta, sa vanliga spelare som skriver !cashback far ingen utbetalning.
  if SendControl("COLLECT:CB7K3X9Q2M") then
    SetStatus("Collecting cashback...")
    SetCbDelta(amt)                                   -- visa +Xg vid cashback
    -- logga i history: "Cashback claimed +Xg"
    CasinobabeDB.history = CasinobabeDB.history or {}
    table.insert(CasinobabeDB.history, 1, { cashback=true, amount=amt })
    while #CasinobabeDB.history>20 do table.remove(CasinobabeDB.history) end
    CasinobabeDB.cashback=0; UpdateDisplay()          -- nollstall direkt
    cbIgnoreUntil=((GetTime and GetTime()) or 0)+4    -- ignorera gammal cb-siffra ett tag
  else
    SetStatus("Join the casino's raid first.", C.gold)
  end
end
function ToggleBlockTrades()
  CasinobabeDB.blockTrades=not CasinobabeDB.blockTrades; UpdateDisplay()
  if CasinobabeDB.blockTrades then SetStatus("Trades blocked. Toggle off to deposit/cashout.", C.red); print("|cffFFD700Casinobabe|r trade requests will be auto-declined.")
  else SetStatus("Trades allowed.", C.light); print("|cffFFD700Casinobabe|r trades allowed again.") end
end

function HandleMessage(msg, sender)
  if not msg then return end
  local to=msg:match("|to=([^|]+)$")
  if to then if shortName(to)~=(myName or shortName(UnitName("player"))) then return end; msg=msg:gsub("|to=[^|]+$","") end
  SetConnected(true)
  if msg=="BUSY" then
    BlackjackBusy()
    return
  end
  if msg:find("^STATE:") then
    local bal=tonumber(msg:match("bal=(%d+%.?%d*)")); local cb=tonumber(msg:match("cb=(%d+%.?%d*)"))
    if cb then
      if cb>0 and ((GetTime and GetTime()) or 0)<cbIgnoreUntil then
        -- nyss collectad: ignorera en kvardrojande gammal siffra
      else CasinobabeDB.cashback=cb end
    end
    if bal then
      if pending then
        -- Starta spelet (visa ROLL/HIT/OVER) sa fort casinot bekraftar betet:
        -- antingen syns en avdragning (saldot foll) ELLER saldot matchar forvantat.
        -- Robust - kraver INTE exakt match, sa knappen dyker upp tillforlitligt.
        if not pending.started and (bal <= (pending.balBefore or bal) - 0.5
             or math.abs(bal-pending.expectAfter)<=0.5) then
          pending.started=true; pending.sawDeduction=true; PopupActivate(pending.game)
        end
        -- Vinst via casino-STATE: ENDAST blackjack/dice (huset avgor). Single-roll
        -- (normal/high/lucky7/roulette) avgors redan client-side i OnMyRoll - om vi
        -- aven registrerade dem har skulle vinsten kunna dubbelraknas i stats.
        if pending and pending.started and bal>pending.expectAfter+0.5
           and (pending.game=="blackjack" or pending.game=="dice") then
          local net=bal-(pending.balBefore or pending.expectAfter)
          SetBalDelta(net)
          FlashResult(true, math.floor(net+0.5), pending.game)
          RecordResult(pending.game, pending.stake, true, net)
          ShowResult(true, math.floor(net+0.5))
          PopupResolve(true, math.floor(net+0.5)); pending=nil
        end
      end
      CasinobabeDB.bal=bal
    end
    UpdateDisplay()
    if panel and panel.statusText:GetText():find("Connecting") then SetStatus("Connected to "..(groupLeaderName() or "the casino")) end
  end
end

-- ============================================================================
-- DISCORD-COPY
-- ============================================================================
StaticPopupDialogs["CASINOBABE_DISCORD"]={
  text="Casinobabe Discord - copy with Ctrl+C:", button1=CLOSE or "Close", hasEditBox=true, editBoxWidth=240,
  timeout=0, whileDead=true, hideOnEscape=true,
  OnShow=function(self) local txt=(CasinobabeDB.discord or "Casinobabetbc"); local eb=self.editBox or self.EditBox; if eb then eb:SetText(txt); eb:HighlightText(); eb:SetFocus() end end,
  EditBoxOnEscapePressed=function(self) self:GetParent():Hide() end,
}
function ShowDiscord() StaticPopup_Show("CASINOBABE_DISCORD") end

-- Bekraftelse for nollstallning av ALL statistik + history (kan ej angras).
StaticPopupDialogs["CASINOBABE_RESET_ALL"]={
  text="Reset ALL your stats and history?\nThis cannot be undone.",
  button1=YES or "Yes", button2=NO or "No",
  timeout=0, whileDead=true, hideOnEscape=true, showAlert=true,
  OnAccept=function()
    CasinobabeDB.stats={ games=0, wins=0, wagered=0, wonGold=0, lostGold=0, byGame={} }
    CasinobabeDB.history={}
    local k=Casinobabe_Info and Casinobabe_Info._kind
    if k=="stats" then ShowInfo("Your statistics", BuildStatsText(), "stats")
    elseif k=="history" then ShowInfo("Recent games", BuildHistoryText(), "history") end
    print("|cffFFD700Casinobabe|r stats & history reset.")
  end,
}

-- ============================================================================
-- SPRAK-MENY
-- ============================================================================
function OpenLanguageMenu()
  if not langMenu then
    langMenu=CreateFrame("Frame","Casinobabe_LangMenu", panel); langMenu:SetSize(120, #LANGS*20+8)
    langMenu:SetPoint("BOTTOMLEFT", panel.langBtn, "TOPLEFT", 0, 2); langMenu:SetFrameStrata("DIALOG")
    MakeBacking(langMenu,0.06,0.04,0.08,0.97); MakeBorder(langMenu,2):SetColor(uc(C.goldDk))
    for i,l in ipairs(LANGS) do
      local item=CreateFrame("Button", nil, langMenu); item:SetSize(112,20); item:SetPoint("TOPLEFT", langMenu, "TOPLEFT", 4, -4-(i-1)*20)
      local t=MakeText(item,11,"","LEFT"); t:SetText(l[1]); t:SetPoint("LEFT",6,0); t:SetTextColor(uc(C.light))
      local hl=item:CreateTexture(nil,"HIGHLIGHT"); hl:SetAllPoints(); hl:SetColorTexture(1,0.85,0.3,0.18)
      item:SetScript("OnClick", function()
        if PostPublic(l[2]) then
          CasinobabeDB.langLabel=l[1]
          CasinobabeDB.langCode=l[2]
          if panel and panel.langBtn then panel.langBtn.txt:SetText(l[1]) end
          SetStatus("Language set: "..l[1], C.green)
        end
        langMenu:Hide()
      end)
    end
    langMenu:Hide()
  end
  if langMenu:IsShown() then langMenu:Hide() else langMenu:Show() end
end

-- ============================================================================
-- BLOCK TRADES + WHO_LIST_UPDATE + DEALER TRADE EVENTS
-- ============================================================================
local evt=CreateFrame("Frame")
evt:RegisterEvent("TRADE_SHOW")
evt:RegisterEvent("TRADE_ACCEPT_UPDATE")
evt:RegisterEvent("TRADE_CLOSED")
evt:RegisterEvent("UI_INFO_MESSAGE")
evt:RegisterEvent("WHO_LIST_UPDATE")
evt:SetScript("OnEvent", function(_, event, ...)
  if event=="TRADE_SHOW" then
    if CasinobabeDB and CasinobabeDB.blockTrades then
      if CloseTrade then CloseTrade() end
      if panel and panel:IsShown() then SetStatus("Trade auto-declined (blocking on).", C.red) end
    end
    -- Dealer: trade opened
    if dealerEnabled then
      local targetName = UnitName("npc") or UnitName("target")
      if targetName then DealerOnTradeShow(targetName) end
    end
  elseif event=="TRADE_ACCEPT_UPDATE" then
    if dealerEnabled then
      local playerAccepted, targetAccepted = ...
      if targetAccepted == 1 then
        local targetName = UnitName("npc") or UnitName("target")
        if targetName then DealerOnTradeAccept(targetName) end
      end
    end
  elseif event=="TRADE_CLOSED" then
    if dealerEnabled then
      local targetName = UnitName("npc") or UnitName("target")
      local completed = ...  -- true if trade completed successfully
      if targetName then DealerOnTradeClose(targetName, completed) end
    end
  elseif event=="UI_INFO_MESSAGE" then
    if dealerEnabled then
      local msgType, msg = ...
      -- Trade result messages
      if msg and (msg:find("Trade") or msg:find("trade")) then
        -- Could parse specific trade result messages here
      end
    end
  elseif event=="WHO_LIST_UPDATE" then
    if whoPending then
      whoPending=false
      local n=0
      if C_FriendList and C_FriendList.GetNumWhoResults then n=C_FriendList.GetNumWhoResults() or 0
      elseif GetNumWhoResults then n=GetNumWhoResults() or 0 end
      if n and n>0 then SetOnlineStatus("Casinobabe ONLINE!  (see /who list)", C.green)
      else SetOnlineStatus("Nothing found - try again", C.mute) end
      -- INTE gomma fonstret: spelaren vill se /who-listan
    end
  end
end)

-- ============================================================================
-- MINIMAP-KNAPP med haftigt "C"
-- ============================================================================
local function CreateMinimapButton()
  -- FORSTAHANDSVAL: en riktig LibDBIcon-knapp (buntat bibliotek). Den samlas
  -- garanterat in av MinimapButtonButton och alla andra minimap-samlare.
  local ldbi = LibStub and LibStub("LibDBIcon-1.0", true)
  if ldbi and ldbi.IsRegistered and not ldbi:IsRegistered("Casinobabe") then
    CasinobabeDB.minimap = CasinobabeDB.minimap or {}
    -- migrera gammal "angle" -> LibDBIcons "minimapPos"
    CasinobabeDB.minimap.minimapPos = CasinobabeDB.minimap.minimapPos
       or CasinobabeDB.minimap.angle or 210
    local dataObj = {
      type="launcher", text="Casinobabe",
      icon="Interface\\Icons\\INV_Misc_Coin_01",
      OnClick=function(_, b) if panel and panel:IsShown() then panel:Hide() else OpenPanel() end end,
      OnTooltipShow=function(tt)
        tt:AddLine("|cffFFD700Casinobabe|r")
        tt:AddLine("Click to open  -  drag to move", 0.8,0.8,0.8)
      end,
    }
    local ok = pcall(function() ldbi:Register("Casinobabe", dataObj, CasinobabeDB.minimap) end)
    if ok then
      local btn = ldbi.GetMinimapButton and ldbi:GetMinimapButton("Casinobabe")
      if btn then
        -- behall vart "C"-utseende ovanpa LibDBIcon-knappen
        if btn.icon then btn.icon:Hide() end
        if not btn.casinoC then
          local cC=btn:CreateFontString(nil,"ARTWORK"); SetFontSafe(cC,"Fonts\\MORPHEUS.TTF",18,"OUTLINE")
          cC:SetPoint("CENTER",0,0); cC:SetText("C"); cC:SetTextColor(uc(C.gold))
          btn.casinoC=cC
        end
      end
      return btn
    end
  end

  -- FALLBACK: egen knapp (om LibDBIcon saknas eller registrering misslyckades).
  -- Namnges anda "LibDBIcon10_Casinobabe" + standardstruktur for basta chans.
  if LibDBIcon10_Casinobabe then return LibDBIcon10_Casinobabe end
  local btn=CreateFrame("Button","LibDBIcon10_Casinobabe", Minimap)
  btn:SetSize(31,31); btn:SetFrameStrata("MEDIUM"); btn:SetFrameLevel(8)
  btn:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")
  local bgc=btn:CreateTexture(nil,"BACKGROUND"); bgc:SetSize(22,22); bgc:SetPoint("CENTER")
  bgc:SetTexture("Interface\\Minimap\\UI-Minimap-Background"); bgc:SetVertexColor(0.06,0.03,0.08,0.9)
  local cC=btn:CreateFontString(nil,"ARTWORK"); SetFontSafe(cC,"Fonts\\MORPHEUS.TTF",20,"OUTLINE")
  cC:SetPoint("CENTER",0,0); cC:SetText("C"); cC:SetTextColor(uc(C.gold))
  local ring=btn:CreateTexture(nil,"OVERLAY"); ring:SetSize(53,53); ring:SetPoint("TOPLEFT"); ring:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
  local function pos() if btn:GetParent()~=Minimap then return end local a=(CasinobabeDB.minimap.angle or 210)*math.pi/180; btn:ClearAllPoints(); btn:SetPoint("CENTER", Minimap, "CENTER", math.cos(a)*80, math.sin(a)*80) end
  btn:RegisterForDrag("LeftButton","RightButton")
  btn:SetScript("OnDragStart", function(self)
    if self:GetParent()~=Minimap then return end
    self:SetScript("OnUpdate", function()
    local mx,my=Minimap:GetCenter(); local px,py=GetCursorPosition(); local s=UIParent:GetScale()
    CasinobabeDB.minimap.angle=math.deg(math.atan2(py/s-my, px/s-mx)); pos() end) end)
  btn:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)
  btn:SetScript("OnClick", function() if panel and panel:IsShown() then panel:Hide() else OpenPanel() end end)
  btn:SetScript("OnEnter", function(self) GameTooltip:SetOwner(self,"ANCHOR_LEFT"); GameTooltip:AddLine("|cffFFD700Casinobabe|r"); GameTooltip:AddLine("Click to open  -  drag to move",0.8,0.8,0.8); GameTooltip:Show() end)
  btn:SetScript("OnLeave", function() GameTooltip:Hide() end)
  pos(); return btn
end

-- ============================================================================
-- TOGGLE / EVENTS / SLASH
-- ============================================================================
-- Oppnar panelen OCH fyller i den (saldo, valt spel/summa) oavsett vag in.
function OpenPanel()
  if not panel then panel=CreatePanel() end
  panel:Show()
  SelectGame(CasinobabeDB.lastGame or "normal")
  SelectAmount(CasinobabeDB.lastAmount or 50)
  UpdateDisplay()
  RequestState()
end

local function TogglePanel()
  if not panel then panel=CreatePanel() end
  if panel:IsShown() then panel:Hide() else OpenPanel() end
end

local loader=CreateFrame("Frame")
loader:RegisterEvent("ADDON_LOADED"); loader:RegisterEvent("PLAYER_LOGIN"); loader:RegisterEvent("CHAT_MSG_ADDON")
loader:RegisterEvent("CHAT_MSG_SYSTEM")
loader:RegisterEvent("CHAT_MSG_WHISPER")
-- Gruppandringar: nar spelaren gar med i (eller lamnar) casinots raid maste vi
-- fraga om saldo/cashback pa nytt. Utan detta stod panelen kvar pa 0 tills man
-- reloadade. pcall for att eventnamn skiljer sig mellan klientversioner.
pcall(function() loader:RegisterEvent("GROUP_ROSTER_UPDATE") end)
pcall(function() loader:RegisterEvent("PARTY_MEMBERS_CHANGED") end)
pcall(function() loader:RegisterEvent("RAID_ROSTER_UPDATE") end)
pcall(function() loader:RegisterEvent("PLAYER_ENTERING_WORLD") end)
local rosterTkn, lastCh = 0, nil
loader:SetScript("OnEvent", function(self, event, ...)
  if event=="GROUP_ROSTER_UPDATE" or event=="PARTY_MEMBERS_CHANGED"
     or event=="RAID_ROSTER_UPDATE" or event=="PLAYER_ENTERING_WORLD" then
    -- Fraga bara nar det behovs: om vi inte ar anslutna, eller om gruppen
    -- faktiskt bytts (gick med / lamnade). Annars skulle varje roster-tick i
    -- en full raid ge onodig trafik.
    local ch=groupChannel()
    if (not connected) or ch~=lastCh then
      lastCh=ch
      rosterTkn=rosterTkn+1; local t=rosterTkn
      if C_Timer and C_Timer.After then
        -- kort fordrojning: roster-eventet fyrar flera ganger i rad nar man joinar
        C_Timer.After(1.5, function() if t==rosterTkn then RequestState() end end)
      else
        RequestState()
      end
    end
    if dealerEnabled then 
      pcall(DealerCoreOnGroupUpdate)
      -- Update dealer connection state on group changes
      local newCh = groupChannel()
      if newCh then
        if dealerConnState == DEALER_CONN_STATES.CONNECTING or dealerConnState == DEALER_CONN_STATES.OFF then
          DealerSetConnState(DEALER_CONN_STATES.CONNECTED, "raid/group joined")
          SendControl("HELLO")
        end
      else
        if dealerConnState == DEALER_CONN_STATES.CONNECTED or dealerConnState == DEALER_CONN_STATES.READY then
          DealerSetConnState(DEALER_CONN_STATES.CONNECTING, "no raid/group - waiting")
        end
      end
    end
    return
  end
  if event=="CHAT_MSG_WHISPER" then
    local msg, sender = ...
    if dealerEnabled then DealerOnWhisper(msg, sender) end
    return
  end
  if event=="ADDON_LOADED" and ...==ADDON then
    InitDB(); RegisterPrefix()
    -- Skapa minimap-knappen redan vid ADDON_LOADED (innan inloggning) sa den finns
    -- pa Minimap NAR samlar-addon som MinimapButtonButton gor sin insamling. Skapas
    -- knappen forst vid PLAYER_LOGIN kan samlaren hinna scanna fore oss och missa den.
    if not LibDBIcon10_Casinobabe then CreateMinimapButton() end
  elseif event=="PLAYER_LOGIN" then
    myName=shortName(UnitName("player"))
    CasinobabeDB.casino=nil   -- rensa gammalt felaktigt sparat namn; vi visar raid-ledaren
    if not LibDBIcon10_Casinobabe then CreateMinimapButton() end   -- fallback
    RequestState()
    -- Auto-detect dealer mode for Casinobae
    AutoDetectDealerMode()
    -- Forsta gangen efter installation: oppna panelen automatiskt sa nya spelare
    -- hittar den direkt (slipper leta efter C:et pa minimappen). Sker bara EN gang -
    -- flaggan sparas, sen oppnas den aldrig av sig sjalv igen.
    if not CasinobabeDB.seenIntro then
      CasinobabeDB.seenIntro=true
      if C_Timer and C_Timer.After then
        C_Timer.After(1.5, function() if OpenPanel then OpenPanel() end end)
      elseif OpenPanel then OpenPanel() end
    end
    print("|cffFFD700Casinobabe|r loaded. Type /cb to open. Join the casino's raid to connect.")
  elseif event=="CHAT_MSG_ADDON" then
    local prefix,message,channel,sender=...
    if prefix==PREFIX then HandleMessage(message,sender) end
  elseif event=="CHAT_MSG_SYSTEM" then
    local text=...
    if text and pending then
      -- "Namn rolls 73 (1-100)" -> om det ar VI som rullat, skicka till OnMyRoll.
      -- Locale-robust: pa icke-engelska klienter ar verbet annorlunda
      -- ("wuerfelt", "obtient" osv.), men "<namn> ... <roll> (1-100)" galler
      -- overallt. Vi matchar siffran + (1-100) och kollar att namnet ar vart.
      myName=myName or shortName(UnitName("player"))
      local who,roll=text:match("^(%S+) .-(%d+) %(1%-100%)")
      if who and roll and shortName(who)==myName then
        OnMyRoll(tonumber(roll))
      else
        -- fallback: klassisk engelsk formattering
        local who2,roll2=text:match("^(.-) rolls (%d+) %(%d+%-%d+%)")
        if who2 and roll2 and shortName(who2)==myName then OnMyRoll(tonumber(roll2)) end
      end
      -- DICE: huset rullar tva tarningar (1-6 var) synligt. Las dem och visa
      -- summan + over/under, sa spelaren ser vad huset slog. (Sjalva vinsten/
      -- forlusten kommer fortfarande fran casinots STATE / LossCheck.)
      if pending and pending.game=="dice" and pending.started and pending.actionTaken
         and pending.diceRolls and #pending.diceRolls<2 then
        local hwho,hroll=text:match("^(%S+) .-(%d+) %(1%-6%)")
        if hwho and hroll and shortName(hwho)~=myName then
          table.insert(pending.diceRolls, tonumber(hroll))
          if #pending.diceRolls>=2 then
            local d1,d2=pending.diceRolls[1],pending.diceRolls[2]
            local total=d1+d2; pending.diceTotal=total
            local res=(total>=8 and "OVER") or (total<=6 and "UNDER") or "SEVEN"
            SetCenterInfo(("HOUSE:  %d   (%d + %d)  -  %s"):format(total,d1,d2,res), C.gold)
          end
        end
      end
    end
    -- Dealer: capture rolls from other players
    if dealerEnabled then DealerOnSystemMsg(text) end
  end
end)

SLASH_CASINOBABE1="/cb"; SLASH_CASINOBABE2="/casinobabe"
SlashCmdList["CASINOBABE"]=function(msg)
  msg=(msg or ""):gsub("^%s+",""):gsub("%s+$","")
  local cmd,arg=msg:match("^(%S*)%s*(.-)$"); cmd=(cmd or ""):lower()
  -- DEALER SUBCOMMANDS
  if cmd=="dealer" then
    local subcmd, subarg = arg:match("^(%S*)%s*(.-)$")
    subcmd = (subcmd or ""):lower()
    if subcmd=="on" then DealerToggle("on")
    elseif subcmd=="off" then DealerToggle("off")
    elseif subcmd=="status" then DealerStatus()
    elseif subcmd=="ad" then DealerAdvertise()
    elseif subcmd=="invite" then
      if subarg=="" then print("|cffFFD700Casinobabe|r Usage: /cb dealer invite <player>") else DealerInvite(subarg) end
    elseif subcmd=="game" then
      local player, game, extra = subarg:match("^(%S+)%s+(%S+)%s*(.*)$")
      if not player or not game then print("|cffFFD700Casinobabe|r Usage: /cb dealer game <player> <game> [color|choice]") else DealerGame(player, game, extra ~= "" and extra or nil) end
    elseif subcmd=="stake" then
      local player, amount = subarg:match("^(%S+)%s+(.+)$")
      if not player or not amount then print("|cffFFD700Casinobabe|r Usage: /cb dealer stake <player> <amount>") else DealerStake(player, amount) end
    elseif subcmd=="roll" then
      local player, roll = subarg:match("^(%S+)%s*(%d*)$")
      if not player then print("|cffFFD700Casinobabe|r Usage: /cb dealer roll <player> [roll]") else DealerRecord(player, roll=="" and "" or roll) end
    elseif subcmd=="record" then
      local player, roll = subarg:match("^(%S+)%s+(%d+)$")
      if not player or not roll then print("|cffFFD700Casinobabe|r Usage: /cb dealer record <player> <roll>") else DealerRecord(player, roll) end
    elseif subcmd=="resolve" then
      if subarg=="" then print("|cffFFD700Casinobabe|r Usage: /cb dealer resolve <player>") else DealerResolve(subarg) end
    elseif subcmd=="payout" then
      local player, amount = subarg:match("^(%S+)%s*(.*)$")
      if not player then print("|cffFFD700Casinobabe|r Usage: /cb dealer payout <player> [amount]") else DealerPayout(player, amount) end
    elseif subcmd=="close" then
      if subarg=="" then print("|cffFFD700Casinobabe|r Usage: /cb dealer close <player>") else DealerClose(subarg) end
    elseif subcmd=="reset" then
      if subarg=="" then print("|cffFFD700Casinobabe|r Usage: /cb dealer reset <player>") else DealerReset(subarg) end
    elseif subcmd=="log" then DealerLog()
    elseif subcmd=="zone" then
      local zone = GetRealZoneText and GetRealZoneText() or GetZoneText and GetZoneText() or "Unknown"
      local subZone = GetSubZoneText and GetSubZoneText() or ""
      local faction = UnitFactionGroup and UnitFactionGroup("player") or "Unknown"
      local loc = GetLocale and GetLocale() or "enUS"
      local canAdvertise = DealerCanAdvertiseHere()
      local capitals = DEALER_CAPITALS[faction] or DEALER_CAPITALS.Alliance
      local capitalName = capitals[loc] or "Unknown"
      print("|cffFFD700Casinobabe|r === ZONE DEBUG ===")
      print("  RealZone: " .. zone)
      print("  SubZone: " .. (subZone ~= "" and subZone or "(none)"))
      print("  Faction: " .. faction)
      print("  Locale: " .. loc)
      print("  Faction Capital (" .. loc .. "): " .. capitalName)
      print("  CanAdvertise: " .. (canAdvertise and "|cff78EB96YES|r" or "|cffEB5E4FNO|r"))
    elseif subcmd=="show" then
      CasinoShow:StartShow()
    elseif subcmd=="quickad" then
      CasinoShow:StartQuickAd()
    elseif subcmd=="stopshow" then
      CasinoShow:StopShow()
    elseif subcmd=="showstatus" then
      print("|cffFFD700Casinobabe|r Show status: " .. CasinoShow:GetStatus())
    elseif subcmd=="emote" then
      if subarg=="" then print("|cffFFD700Casinobabe|r Usage: /cb dealer emote <emote>") else CasinoEmote:PlayPhysical(subarg) end
    else print("|cffFFD700Casinobabe|r Dealer commands: on|off|status|ad|invite|game|stake|roll|record|resolve|payout|close|reset|log|zone|show|quickad|stopshow|showstatus|emote") end
    
  elseif cmd=="auto" then
  -- Auto-dealer mode handlers
  if arg=="" then
    -- /cb auto - enable auto-dealer
    if not CasinobabeDB.autoDealer then
      CasinobabeDB.autoDealer = true
      DealerToggle("on")
      local ok, reason = AutoStartAttract()
      if ok then
        print("|cffFFD700Casinobabe|r Auto-dealer ENABLED - attract started")
      else
        print("|cffFFD700Casinobabe|r Auto-dealer ENABLED - attract paused (" .. tostring(reason) .. ")")
      end
    else
      if not autoAttractRunning then
        local ok, reason = AutoStartAttract()
        if ok then
          print("|cffFFD700Casinobabe|r Auto-dealer already ENABLED - attract (re)started")
        else
          print("|cffFFD700Casinobabe|r Auto-dealer already ENABLED - attract paused (" .. tostring(reason) .. ")")
        end
      else
        print("|cffFFD700Casinobabe|r Auto-dealer already ENABLED")
      end
    end
    
  elseif arg=="stop" then
    -- /cb auto stop - disable auto-dealer
    if CasinobabeDB.autoDealer then
      CasinobabeDB.autoDealer = false
      AutoStopAttract()
      DealerToggle("off")
      print("|cffFFD700Casinobabe|r Auto-dealer DISABLED - attract stopped, dealer disabled")
    else
      print("|cffFFD700Casinobabe|r Auto-dealer already DISABLED")
    end
    
  else
    -- /cb auto status - show status
    local autoStatus = CasinobabeDB.autoDealer and "|cff78EB96ENABLED|r" or "|cffEB5E4FDISABLED|r"
    local dealerStatus = dealerEnabled and "|cff78EB96ENABLED|r" or "|cffEB5E4FDISABLED|r"
    local attractStatus = autoAttractRunning and "|cff78EB96RUNNING|r" or (autoAttractRunning==false and "|cffEB5E4FPAUSED|r" or "|cffEB5E4FDISABLED|r")
    local charStatus = IsDealerCharacter() and "|cff78EB96Casinobae|r" or ""
    local attractChannel = GetAvailableAttractChannel()
    
    print("|cffFFD700Casinobabe|r Auto-dealer status:")
    print("  Auto: " .. autoStatus)
    print("  Dealer: " .. dealerStatus)
    print("  Attract: " .. attractStatus)
    print("  Channel: " .. (attractChannel or "NONE"))
    print("  Character: " .. charStatus)
  end
    
  elseif cmd=="who" and arg=="" then
    -- visa vad som kanns av pa DENNA klienten (felsokningshjalp)
    local loc=(GetLocale and GetLocale()) or "?"
    local fac=(UnitFactionGroup and UnitFactionGroup("player")) or "?"
    local eff=(CasinobabeDB.whoCustom and CasinobabeDB.whoQuery) or LocalizedWhoQuery()
    print("|cffFFD700Casinobabe|r client language: "..loc.." | faction: "..fac)
    print("|cffFFD700Casinobabe|r /who query"..(CasinobabeDB.whoCustom and " (custom)" or " (auto)")..": "..eff)
    print("|cffFFD700Casinobabe|r  use /cb who <text> to override, or /cb who auto to reset")
  elseif cmd=="who" and arg~="" then
    if arg=="auto" or arg=="reset" then
      CasinobabeDB.whoCustom=nil; CasinobabeDB.whoQuery=nil
      print("|cffFFD700Casinobabe|r online-check query reset to auto (faction + client language): "..LocalizedWhoQuery())
    else
      CasinobabeDB.whoQuery=arg; CasinobabeDB.whoCustom=true
      print("|cffFFD700Casinobabe|r online-check query set to: "..arg)
    end
  elseif cmd=="discord" and arg~="" then
    CasinobabeDB.discord=arg; print("|cffFFD700Casinobabe|r discord set to "..arg)
    if panel then panel.discordText:SetText("Discord: |cff8ab4f8"..arg.."|r for help") end
  elseif cmd=="fx" then CasinobabeDB.fx=not CasinobabeDB.fx; print("|cffFFD700Casinobabe|r fire fx: "..(CasinobabeDB.fx and "on" or "off"))
  elseif cmd=="resetstats" then
    CasinobabeDB.stats={ games=0, wins=0, wagered=0, wonGold=0, lostGold=0, byGame={} }; CasinobabeDB.history={}
    print("|cffFFD700Casinobabe|r statistics & history cleared.")
  elseif cmd=="reset" then CasinobabeDB.pos=nil; if panel then panel:ClearAllPoints(); panel:SetPoint("CENTER") end; print("|cffFFD700Casinobabe|r position reset.")
  elseif cmd=="intro" then
    CasinobabeDB.seenIntro=nil
    print("|cffFFD700Casinobabe|r intro reset - the panel will auto-open on your next /reload or login.")
  else
    print("|cffFFD700Casinobabe|r Commands: /cb dealer on|off|status|ad|invite|game|stake|roll|record|resolve|payout|close|reset|log|zone|show|quickad|stopshow|showstatus|emote | /cb auto | /cb auto stop | /cb auto status | /cb who | /cb discord <text> | /cb fx | /cb resetstats | /cb reset | /cb intro")
  end
end
