-- ============================================================================
-- CASINOBAE SHOW ENGINE
-- Native WoW emote + Custom emote + Chat + ASCII show manager
-- SÉPARATION ABSOLUE des 4 couches : NATIVE_EMOTE | CUSTOM_EMOTE | CHAT | ASCII
-- ============================================================================

-- ===== Forward declarations =====
local ShowEngine, ShowStep, ShowScheduler, ShowCooldowns
local NativeEmoteHandler, CustomEmoteHandler, ChatHandler, ASCIIHandler, WaitHandler

-- ===== LoreContext : dialogue de PNJ selon le monde vivant de WoW =====
-- Fournit des lignes de croupier immersives : capitale, auberge, race, classe,
-- faction, heure (nuit = ambiance casino), sous-zone (nom d'auberge).
LoreContext = {
  -- Titres de croupier par race (côté dealer, pas joueur)
  dealerTitles = {
    Human      = { "Madame", "Monsieur", "Citoyen", "Citoyenne" },
    Orc        = { "Lok'tar", "Grom'gol", "Peau-verte", "Guerrier" },
    Dwarf      = { "Cousin", "Cousine", "Fer", "Marteau" },
    NightElf   = { "Sentinelle", "Druide", "Elune", "Ombre" },
    Undead     = { "Mortel", "Ombre", "Sylvanas", "Réprouvé" },
    Tauren     = { "Frère", "Sœur", "Terre", "Totem" },
    Gnome      = { "Petit", "Génie", "Rouage", "Étincelle" },
    Troll      = { "Mon ami", "Vaudoo", "Zandalar", "Lance" },
    Goblin     = { "Client", "Affaire", "Or", "Marché" },
    BloodElf   = { "Soleil", "Magistère", "Sin'dorei", "Arcane" },
    Draenei    = { "Lumière", "Exarque", "Naaru", "Voyageur" },
    Worgen     = { "Bête", "Gilnéen", "Lune", "Croc" },
    Pandaren   = { "Ami", "Maître", "Bambou", "Brume" },
    Nightborne = { "Étoile", "Arcaniste", "Suramar", "Nuit" },
    Highmountain = { "Montagne", "Totem", "Ciel", "Corne" },
    VoidElf    = { "Vide", "Éthereum", "Ombre", "Rift" },
    Lightforged = { "Lumière", "Vaillant", "Naaru", "Croisé" },
    Zandalari  = { "Roi", "Reine", "Loa", "Or" },
    KulTiran   = { "Marin", "Amiral", "Vague", "Ancre" },
    Mechagnome = { "Rouage", "Unité", "Donnée", "Cœur" },
    DarkIron   = { "Feu", "Fer", "Sombre", "Marteau" },
    Vulpera    = { "Renard", "Sable", "Caravane", "Astuce" },
    Maghar     = { "Guerrier", "Fer", "Ciel", "Lame" },
  },

  -- Salutations par zone (capitales = formel, auberges = intime, extérieur = voyageur)
  zoneGreetings = {
    -- Capitales Alliance
    ["Stormwind City"] = {
      "Welcome to the Crown's finest table, %s. The King's own coins grace our felt.",
      "A pleasure, %s. Stormwind's elite know where the real gold flows.",
      "Step right up, %s. The Trade District's best-kept secret.",
    },
    ["Ironforge"] = {
      "By the Anvil, %s! Ironforge steel meets golden chance tonight.",
      "Welcome, %s. The Bronzebeards don't gamble — they invest.",
      "Pull up a stool, %s. The Great Forge glows hotter with every roll.",
    },
    ["Darnassus"] = {
      "Elune's light finds even the shadows of chance, %s.",
      "Welcome, %s. Teldrassil's roots run deep; so do our pots.",
      "The Moonwell reflects fortune tonight, %s. Care to gaze?",
    },
    ["The Exodar"] = {
      "The Light guides all hands to this table, %s.",
      "Welcome, %s. Even crystal ships need a lucky port.",
      "By the Naaru, %s — your destiny deals tonight.",
    },
    ["Gilneas City"] = {
      "Behind the Greymane Wall, the cards still fall, %s.",
      "Welcome, %s. Gilneas remembers how to play for keeps.",
    },

    -- Capitales Horde
    ["Orgrimmar"] = {
      "Lok'tar ogar, %s! The Warchief's own dice roll here.",
      "Strength and honor... and a little luck, %s. Welcome to Orgrimmar's table.",
      "The Valley of Strength bends to no one — except the dealer, %s.",
    },
    ["Thunder Bluff"] = {
      "The Earthmother smiles on bold wagers, %s.",
      "Welcome, %s. High on the bluffs, the stakes touch the sky.",
      "Walk the spirit path to fortune, %s. The totems witness.",
    },
    ["Undercity"] = {
      "Death comes for all... but tonight, gold comes for you, %s.",
      "Welcome, %s. The Forsaken know the value of a second chance.",
      "In the crypts beneath, the house always wins — unless you're clever, %s.",
    },
    ["Silvermoon City"] = {
      "Anar'alah belore, %s. The Sunwell glitters in every chip.",
      "Welcome, %s. Quel'Thalas burns bright; so does our jackpot.",
      "Magic and chance intertwine, %s. The Magistrix would approve.",
    },
    ["Dazar'alor"] = {
      "The Loa watch every roll, %s. Zandalar's gold is yours to claim.",
      "Welcome, %s. Atal'Dazar's heights pale beside this table.",
      "By Rezan's flame, %s — fortune favors the trollish bold.",
    },

    -- Auberges neutres / zones contestées
    ["Booty Bay"] = {
      "Ah, %s! The Blackwater cuts deep, but our pots cut deeper.",
      "Welcome to the Bay, %s. Goblins cheat — we just deal fair.",
      "The Bruisers watch the door, %s. Inside? Only Lady Luck.",
    },
    ["Ratchet"] = {
      "Steam and gold, %s. Ratchet's gears turn for the house tonight.",
      "Welcome, %s. The Barrens are harsh — this table isn't.",
    },
    ["Gadgetzan"] = {
      "The sands hide secrets, %s. Our cards reveal them.",
      "Welcome to Gadgetzan, %s. No Water, no problem — just gold.",
    },
    ["Shattrath City"] = {
      "The Naaru blink, %s. Even in Outland, chance finds a way.",
      "Welcome, %s. A'dal's light reaches the felt.",
    },
    ["Dalaran"] = {
      "Magic in the air, gold on the table, %s. The Kirin Tor approves.",
      "Welcome, %s. Floating above Northrend, we deal the arcane hand.",
    },
    ["The Wandering Isle"] = {
      "Balance in all things, %s — even the odds.",
      "Welcome, %s. The turtle swims; the dice tumble.",
    },
  },

  -- Sous-zones : auberges connues
  innLines = {
    ["The Lion's Pride"] = "The Lion roars, %s — but tonight, the dealer holds the mane.",
    ["The Wyvern's Tail"] = "The Wyvern's sting is gold, %s. Care to be stung?",
    ["The Golden Keg"] = "Ale flows, gold glows, %s. The Keg knows your name.",
    ["The Slaughtered Lamb"] = "Even shadows drink here, %s. Your secret's safe.",
    ["The Blue Recluse"] = "Mages whisper, warlocks scheme, %s. We just deal.",
    ["The Gilded Rose"] = "Petals fall, chips stack, %s. Beauty in every bet.",
    ["The Stonefire Tavern"] = "Fire in the hearth, fire in the pot, %s.",
    ["The Drunken Hozen"] = "Monkeys throw fruit; we throw jackpots, %s.",
    ["The Keg and Whistle"] = "Steam whistles, coins whistle louder, %s.",
    ["The Winking Skeever"] = "A skeever winks, %s. So does Lady Luck.",
    ["The Bannered Mare"] = "Whiterun's finest mead, %s — but our vintage is gold.",
    ["The Frozen Hearth"] = "Ice outside, fire within, %s. The Hearth never cools.",
    ["The Silvermoon"] = "Elegance in every pour, %s. The Sin'dorei standard.",
    ["The Wayfarer's Rest"] = "Rest your boots, %s. The road's long; the night's longer.",
    ["The Legerdemain Lounge"] = "Illusions fade, %s. This gold is real.",
    ["The Filthy Animal"] = "Animals fight, %s. We play for keeps.",
  },

  -- Classes : répliques de métier
  classLines = {
    WARRIOR     = "A warrior's resolve, %s — but even blades dull. Dice don't.",
    PALADIN     = "The Light guides, %s. Tonight, it guides to the jackpot.",
    HUNTER      = "You track beasts, %s. Tonight, track the winning hand.",
    ROGUE       = "Shadows hide blades, %s. They also hide aces up sleeves.",
    PRIEST      = "Faith heals, %s. Fortune pays. We deal both.",
    DEATHKNIGHT = "The Lich King's grasp is cold, %s. Our chips run hot.",
    SHAMAN      = "Elements serve you, %s. Tonight, they serve the pot.",
    MAGE        = "Arcane intellect, %s. Calculate the odds — then defy them.",
    WARLOCK     = "Demons bargain, %s. The house only deals.",
    MONK        = "Chi flows, %s. Let it flow straight to the jackpot.",
    DRUID       = "Shapeshift into a winner, %s. Bear, cat, or high roller.",
    DEMONHUNTER = "Illidan's eyes saw destiny, %s. Ours see the next card.",
    EVOKER      = "Empower the bet, %s. The dragonflight watches.",
  },

  -- Heure du jeu (GetGameTime -> 0-23)
  timeLines = {
    night = {  -- 20h-5h
      "The moon rises, %s. High rollers only after dark.",
      "Midnight oil burns, %s. The best hands play at witching hour.",
      "Stars align, %s. So do the sevens.",
    },
    dawn = {   -- 5h-9h
      "Dawn breaks, %s. Early bird catches the jackpot.",
      "First light, first roll, %s. The table's fresh.",
    },
    day = {    -- 9h-17h
      "Sun high, stakes higher, %s. The day belongs to the bold.",
      "Merchant hours, %s. Trade coin for destiny.",
    },
    dusk = {   -- 17h-20h
      "Twilight falls, %s. The evening's first winner could be you.",
      "Shadows lengthen, %s. So do the payouts.",
    },
  },

  -- Faction flavor
  factionFlavor = {
    Alliance = "For the Alliance, %s — and for the gold!",
    Horde    = "For the Horde, %s — lok'tar ogar, win big!",
  },

  -- Race-specific flavor lines (dealer s'adresse au joueur)
  raceFlavor = {
    Human      = "Stormwind steel in your spine, %s. Show the table.",
    Orc        = "Blood and thunder, %s. The dice answer to strength.",
    Dwarf      = "Iron in your beard, gold in your pouch, %s.",
    NightElf   = "Ten thousand years of patience, %s. One roll changes all.",
    Undead     = "What's dead can still win, %s. The house remembers.",
    Tauren     = "The Earthmother watches, %s. Her blessing on your bet.",
    Gnome      = "Small hands, big wins, %s. Engineering the jackpot.",
    Troll      = "Voodoo in your fingers, %s. Shake the bones right.",
    Goblin     = "Time is money, %s. This roll's a steal.",
    BloodElf   = "Magic in your veins, %s. Arcane the odds.",
    Draenei    = "The Light spans worlds, %s. This table's just one.",
    Worgen     = "The beast within calculates, %s. Let it hunt gold.",
    Pandaren   = "Slow pour, fast win, %s. Balance finds the jackpot.",
    Nightborne = "Ten millennia of arcane, %s. One night of chance.",
    Highmountain = "Mountain-strong, %s. The peak pays.",
    VoidElf    = "The Void whispers odds, %s. Listen and win.",
    Lightforged = "Forged in light, %s. Gold reflects brightest.",
    Zandalari  = "Kings and queens play here, %s. Crown yourself.",
    KulTiran   = "Storm-forged, %s. Ride the wave to the pot.",
    Mechagnome = "Optimize the roll, %s. Probability: 100% profit.",
    DarkIron   = "Fire in the blood, %s. Melt the pot.",
    Vulpera    = "Fox-quick, %s. The caravan stops at the jackpot.",
    Maghar     = "Unbroken, %s. The dice don't break either.",
  },

  -- Helper : pick random from table
  _pick = function(self, t)
    if not t or #t == 0 then return "" end
    return t[math.random(1, #t)]
  end,

  -- Build a contextual greeting for the current player/dealer context
  GetGreeting = function(self, targetName)
    local name = targetName or "friend"
    local lines = {}

    -- 1. Zone greeting (highest priority)
    local zone = (GetRealZoneText and GetRealZoneText()) or (GetZoneText and GetZoneText()) or ""
    local subZone = (GetSubZoneText and GetSubZoneText()) or ""
    if self.zoneGreetings[zone] then
      local fmt = self:_pick(self.zoneGreetings[zone])
      if fmt and fmt ~= "" then table.insert(lines, fmt:format(name)) end
    elseif self.innLines[subZone] then
      table.insert(lines, self.innLines[subZone]:format(name))
    end

    -- 2. Race flavor
    local race = (UnitRace and select(2, UnitRace("player"))) or "Human"
    if self.raceFlavor[race] then
      table.insert(lines, self.raceFlavor[race]:format(name))
    end

    -- 3. Class flavor
    local class = (UnitClass and select(2, UnitClass("player"))) or "WARRIOR"
    if self.classLines[class] then
      table.insert(lines, self.classLines[class]:format(name))
    end

    -- 4. Time of day
    local hour = 12
    if GetGameTime then hour = select(1, GetGameTime()) end
    local timeKey = (hour >= 20 or hour < 5) and "night"
               or (hour >= 5 and hour < 9) and "dawn"
               or (hour >= 9 and hour < 17) and "day"
               or "dusk"
    if self.timeLines[timeKey] then
      table.insert(lines, self:_pick(self.timeLines[timeKey]):format(name))
    end

    -- 4. Faction
    local faction = (UnitFactionGroup and UnitFactionGroup("player")) or "Alliance"
    if self.factionFlavor[faction] then
      table.insert(lines, self.factionFlavor[faction]:format(name))
    end

    return lines
  end,

  -- One-liner for quick emote context
  GetOneLiner = function(self, category, targetName)
    local name = targetName or "friend"
    local race = (UnitRace and select(2, UnitRace("player"))) or "Human"
    local class = (UnitClass and select(2, UnitClass("player"))) or "WARRIOR"
    if category == "race" and self.raceFlavor[race] then
      return self.raceFlavor[race]:format(name)
    elseif category == "class" and self.classLines[class] then
      return self.classLines[class]:format(name)
    elseif category == "time" then
      local hour = 12
      if GetGameTime then hour = select(1, GetGameTime()) end
      local timeKey = (hour >= 20 or hour < 5) and "night"
                 or (hour >= 5 and hour < 9) and "dawn"
                 or (hour >= 9 and hour < 17) and "day"
                 or "dusk"
      return self:_pick(self.timeLines[timeKey]):format(name)
    end
    return ""
  end,
}

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
-- { type = SHOW_TYPE.CUSTOM_EMOTE, text = "The dealer slams the table." }
-- { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Place your bets!" }
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
    -- Register default shows with LoreContext for living WoW dialogue
    local L = _G.LoreContext
    local playerName = UnitName and UnitName("player") or "friend"

    -- Helper: build a contextual show (runs LoreContext at show START, not init)
    local function C(showName, baseSteps)
      return function()
        local steps = {}
        for _, s in ipairs(baseSteps) do
          local step = { type = s.type }
          -- Copy static fields
          for k, v in pairs(s) do
            if k ~= "type" then step[k] = v end
          end
          -- Inject contextual text for CUSTOM_EMOTE and CHAT
          if step.type == SHOW_TYPE.CUSTOM_EMOTE and step.text then
            local ctx = L:GetOneLiner("race", playerName)
            if ctx and ctx ~= "" then step.text = ctx end
          elseif step.type == SHOW_TYPE.CHAT and step.text then
            local greetings = L:GetGreeting(playerName)
            if #greetings > 0 then
              step.text = greetings[math.random(1, #greetings)]
            end
          end
          table.insert(steps, step)
        end
        return steps
      end
    end

    -- WELCOME: wave + contextual greeting + casino ASCII
    self:RegisterShow("WELCOME", C("WELCOME", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "WAVE" },
      { type = SHOW_TYPE.WAIT, duration = 0.8 },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "spreads her arms wide beneath the lanterns, coins chiming at her belt." },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Welcome to Casinobabe!" },
      { type = SHOW_TYPE.WAIT, duration = 0.5 },
      { type = SHOW_TYPE.ASCII, template = "CASINO" },
    }))

    -- DICE: point + kodo knucklebones lore + dice ASCII
    self:RegisterShow("DICE", C("DICE", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "POINT" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "rattles genuine kodo-knucklebones from the Barrens, etched with Horde runes." },
      { type = SHOW_TYPE.ASCII, template = "BIG_DICE" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Fresh bones, fresh luck - call your total, high rollers!" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
    }))

    -- JACKPOT: dance + freeze + marquee ASCII + faction cheer
    self:RegisterShow("JACKPOT", C("JACKPOT", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.WAIT, duration = 1.2 },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "freezes mid-step... then slowly raises both arms to the chandelier as gold rains." },
      { type = SHOW_TYPE.WAIT, duration = 0.8 },
      { type = SHOW_TYPE.ASCII, template = "JACKPOT" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "JACKPOT! The house rains gold tonight!" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "VICTORY" },
    }))

    -- ROULETTE: spin + wheel shimmer ASCII + faction flavor
    self:RegisterShow("ROULETTE", C("ROULETTE", {
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "spins the fel-iron wheel as the silver ball dances around the rim..." },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "No more bets, ladies and gentlemen... watch the wheel!" },
      { type = SHOW_TYPE.ASCII, template = "ROULETTE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GASP" },
    }))

    -- BLACKJACK: bow + card fan + class-specific line
    self:RegisterShow("BLACKJACK", C("BLACKJACK", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "BOW" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "fans mooncloth cards across velvet with a flick that whispers of Dalaran training." },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "POINT" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Twenty-one awaits the bold. Place your bets!" },
    }))

    -- FIRE: roar + stage flames + fire ASCII
    self:RegisterShow("FIRE", C("FIRE", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "ROAR" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "strides through Ragnaros-grade flames as the stage erupts in emberlight." },
      { type = SHOW_TYPE.ASCII, template = "FIRE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
    }))

    -- SHOWGIRL: curtsey + spotlight + dance
    self:RegisterShow("SHOWGIRL", C("SHOWGIRL", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CURTSEY" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "a moonbeam spotlight cuts through smoke as the showgirl claims the stage." },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "WAVE" },
    }))

    -- FINALE: full emote sequence + casino ASCII + contextual farewell
    self:RegisterShow("FINALE", C("FINALE", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DANCE" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CHEER" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CLAP" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "VICTORY" },
      { type = SHOW_TYPE.ASCII, template = "CASINO" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "takes a final bow as golden confetti rains, the night's last jackpot claimed." },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Thank you for playing at Casinobabe — good night, and good luck!" },
    }))

    -- POKER: gloat/flex/victory + river card + trophy ASCII
    self:RegisterShow("POKER", C("POKER", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GLOAT" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "FLEX" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "VICTORY" },
      { type = SHOW_TYPE.WAIT, duration = 0.5 },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "lays the river card with a wrist-turn that'd make a Goblin accountant weep." },
      { type = SHOW_TYPE.WAIT, duration = 0.5 },
      { type = SHOW_TYPE.ASCII, template = "TROPHY" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "Full house! The table erupts!" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GASP" },
    }))

    -- POKER_LOSS: gasp/cry/drink + loss ASCII
    self:RegisterShow("POKER_LOSS", C("POKER_LOSS", {
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "GASP" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "CRY" },
      { type = SHOW_TYPE.NATIVE_EMOTE, token = "DRINK" },
      { type = SHOW_TYPE.CUSTOM_EMOTE, text = "a hush falls as the losing hand turns — even the shadows hold breath." },
      { type = SHOW_TYPE.ASCII, template = "LOSS" },
      { type = SHOW_TYPE.CHAT, channel = "SAY", text = "A brutal bad beat... better luck on the next hand." },
    }))

    print("|cffFFD700Casinobabe|r Show Engine initialized with " .. #self.shows .. " contextual show definitions")
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
    self.megaLoop = false
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

    -- Classic d'abord : DoEmote(token-string) est l'API prouvee sur Anniversary/TBC.
    -- C_ChatInfo.PerformEmote attend un emoteID numerique sur retail et peut
    -- reussir son pcall SANS jouer l'animation avec un token string (bug "elle bouge pas").
    if DoEmote then
      local success = pcall(function()
        DoEmote(token)
      end)
      if success then
        print("|cff78EB96NativeEmote|r Performed via DoEmote: " .. token)
        return true
      end
      print("|cffFFAA00NativeEmote|r DoEmote failed for: " .. tostring(token) .. ", trying PerformEmote")
    else
      print("|cffEB5E4FNativeEmote|r DoEmote not available on this client")
    end

    -- Fallback moderne (emoteID numerique uniquement)
    if C_ChatInfo and C_ChatInfo.PerformEmote and tonumber(token) then
      local ok = pcall(function() C_ChatInfo.PerformEmote(tonumber(token)) end)
      if ok then
        print("|cff78EB96NativeEmote|r Performed via C_ChatInfo.PerformEmote: " .. token)
        return true
      end
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
    -- Two dice, hard eight (5 + 3). Symmetric 15-wide, left-anchored.
    BIG_DICE = {
      "┌─────┐ ┌─────┐",
      "│ ● ● │ │ ●   │",
      "│  ●  │ │  ●  │",
      "│ ● ● │ │   ● │",
      "└─────┘ └─────┘",
    },
    -- Slot-machine marquee reveal. The aha moment: name in lights.
    JACKPOT = {
      "┌──────────────────────┐",
      "│ ★ ★ ★  7 7 7  ★ ★ ★  │",
      "│   J A C K P O T !    │",
      "└──────────────────────┘",
    },
    -- Spinning wheel shimmer. Repeating pattern = alignment-proof in chat.
    ROULETTE = {
      "○ ● ○ ● ○ ● ○ ●",
      "● ○ ● ○ ● ○ ● ○",
      "○ ● ○ ● ○ ● ○ ●",
      "~ round and round she goes ~",
    },
    -- Stage flame, left-anchored (chat trims trailing spaces).
    FIRE = {
      "     .",
      "    (●)",
      "   (●●●)",
      "  (●●●●●)",
      "  ― FIRE! ―",
    },
    -- Venue marquee with the house name in lights.
    CASINO = {
      "┌────────────────────┐",
      "│  ★ CASINOBABE ★    │",
      "│  where gold shines │",
      "└────────────────────┘",
    },
    -- Bad beat: snake eyes stare back.
    LOSS = {
      "┌──────────┐",
      "│ ●      ● │",
      "│    ●     │",
      "│ so close │",
      "└──────────┘",
    },
    -- Winner's cup, drawn left-anchored line by line.
    TROPHY = {
      "  __________",
      "  \\ WINNER /",
      "   \\ GOLD /",
      "    \\    /",
      "     \\  /",
      "      \\/",
      "      ||",
      "   ___||___",
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
  -- NOTE: /cbs + /cbshow are registered ONCE by ShowEngineBootstrap (Casinobabe.lua)
  -- as SlashCmdList["CASINOBAE_SHOW_ENGINE"]. Do not register here: a second
  -- registration of the same slash names collides in hash_SlashCmdList.
end

function ShowEngine:ShowHelp()
  print("|cffFFD700Casinobabe Show Engine Help|r")
  print("Usage: /cbs <show_name> | /cbs mega (playlist full-auto)")
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
  print("  /cbs mega - MEGA playlist full-auto (welcome>showgirl>dice>roulette>blackjack>jackpot>fire>finale)")
end

-- ===== MEGA playlist full-auto =====
-- 1 commande = enchaine tous les shows fous via onComplete, sans toucher au throttle global.
function ShowEngine:StartMegaShow()
  local playlist = { "WELCOME", "SHOWGIRL", "DICE", "ROULETTE", "BLACKJACK", "JACKPOT", "FIRE", "FINALE" }
  local idx = 0
  local function next()
    idx = idx + 1
    local name = playlist[idx]
    if not name then
      print("|cffFFD700Show Engine|r MEGA complete - all 8 shows played, what a night!")
      return
    end
    if not self.shows[name] then
      print("|cffFFAA00Show Engine|r MEGA skip missing: " .. tostring(name))
      next()
      return
    end
    -- reset throttle pour enchainer sans attendre 2s entre shows
    self.lastShowTime = 0
    print("|cff78EB96Show Engine|r MEGA " .. idx .. "/8: " .. name)
    local ok, err = self:StartShow(name, next)
    if not ok then
      print("|cffEB5E4FShow Engine|r MEGA blocked on " .. name .. ": " .. tostring(err))
    end
  end
  if self.isRunning then self:CancelShow() end
  self.megaLoop = false
  next()
  return true
end

-- ===== MEGA LOOP : rejoue la playlist en boucle jusqu'a /cbs stop =====
-- Shows uniquement (emotes + SAY + ASCII). Le dealer reste au clavier :
-- /cbs stop ou n'importe quel CancelShow coupe la boucle (timers orphelins neutres).
function ShowEngine:StartMegaLoop()
  if self.megaLoop then
    print("|cffFFAA00Show Engine|r MEGA LOOP already running - /cbs stop to end the night")
    return true
  end
  if self.isRunning then self:CancelShow() end
  self.megaLoop = true
  print("|cffFFD700Show Engine|r MEGA LOOP started - the night never ends! /cbs stop to take a bow")
  local playlist = { "WELCOME", "SHOWGIRL", "DICE", "ROULETTE", "BLACKJACK", "JACKPOT", "FIRE", "FINALE" }
  local idx = 0
  local next
  next = function()
    if not self.megaLoop then return end
    idx = idx + 1
    local name = playlist[idx]
    if not name then
      idx = 0
      print("|cffFFD700Show Engine|r MEGA LOOP cycle complete - encore! Back to the top")
      next()
      return
    end
    if not self.shows[name] then
      print("|cffFFAA00Show Engine|r MEGA LOOP skip missing: " .. tostring(name))
      next()
      return
    end
    self.lastShowTime = 0
    print("|cff78EB96Show Engine|r MEGA LOOP: " .. name)
    local ok, err = self:StartShow(name, next)
    if not ok then
      print("|cffEB5E4FShow Engine|r MEGA LOOP blocked on " .. name .. ": " .. tostring(err))
    end
  end
  next()
  return true
end

function ShowEngine:StopMegaLoop()
  self.megaLoop = false
  if self.isRunning then self:CancelShow() end
  print("|cffFFD700Show Engine|r MEGA LOOP stopped")
  return true
end

-- Make ShowEngine globally accessible (but controlled)
_G.ShowEngine = ShowEngine
_G.LoreContext = LoreContext

-- Refresh all shows with fresh LoreContext (call after zone/race/class change)
function ShowEngine:RefreshShows()
  local ok = self:InitializeShows()
  if ok then print("|cff78EB96Show Engine|r Shows refreshed with fresh LoreContext") end
  return ok
end

-- ============================================================================
-- END OF SHOW ENGINE
-- ============================================================================