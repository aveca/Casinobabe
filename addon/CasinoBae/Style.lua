-- CasinoBae Style.lua : signature RP + ASCII du casino dans le chat WoW.
-- 100% ASCII (aucun accent, aucun emoji) : chaque ligne reste lisible
-- dans SAY/YELL/WHISPER/EMOTE sur toutes les resolutions.
-- Limite chat WoW : 255 caracteres par message. Ici : max 48 par ligne.
-- Moteur RP/chat uniquement : ne lance jamais de /rand, ne decide jamais
-- d'un resultat. Les scenes presentent des resultats REELS (venus de
-- Game.lua via le vrai /rand) ou des invitations a jouer.
CasinoBae.Style = CasinoBae.Style or {}
local Style = CasinoBae.Style

Style.VERSION = "1.0.0"
Style.enabled = true
Style.Compact = false

-- Signature : tout joueur qui voit cette ligne sait que c'est Casinobabe.
Style.SIGNATURE = "*--=[ Casinobabe ]=--*"

-- Emotes WoW reelles autorisees (tokens valides pour DoEmote/PerformEmote).
Style.EMOTES = {
  BECKON = true, SALUTE = true, POINT = true, LAUGH = true,
  ROAR = true, BOW = true, CHEER = true, APPLAUD = true,
  SHRUG = true, WAVE = true, TOAST = true, DRINK = true,
  PAT = true, DANCE = true, KNEEL = true, FLEX = true,
  CRY = true, TAUNT = true,
}

-- Budget anti-spam : 8 messages max par fenetre de 4 secondes.
Style.BUDGET = 8
Style.WINDOW = 4
Style._queue = {}
Style._window = {}
Style._flushScheduled = false
Style._lastVariant = {}

-- ============================================================
-- 0. CATALOGUE DES 12 STYLES
-- NAME / CONCEPT / EXAMPLE / USECASE / MAXLEN / EMOTES / DRAWING
-- (les 3 retenus portent PICK = true)
-- ============================================================
Style.STYLES = {
  { NAME = "SIGNATURE", CONCEPT = "Ligne-sceau identitaire qui signe chaque scene.",
    EXAMPLE = "*--=[ Casinobabe ]=--*", USECASE = "cloture de toutes les scenes",
    MAXLEN = 20, EMOTES = "aucune", DRAWING = "bandeau texte" },
  { NAME = "CADRE ROYAL", CONCEPT = "Cadre +---+ qui officialise une annonce.",
    EXAMPLE = "+--------------------------+",
    USECASE = "ouverture de lobby, presentation",
    MAXLEN = 28, EMOTES = "SALUTE", DRAWING = "cadre", PICK = true },
  { NAME = "RIDEAU", CONCEPT = "Rideau de velours en .:. pour entrer/sortir de scene.",
    EXAMPLE = ".:.  .:.  .:.  .:.  .:.", USECASE = "entree, sortie",
    MAXLEN = 23, EMOTES = "BECKON, WAVE", DRAWING = "rideau" },
  { NAME = "PIECE D OR", CONCEPT = "Piece gravee G, monnaie de la Maison.",
    EXAMPLE = "(  G  )", USECASE = "mises, cagnottes, cashback RP",
    MAXLEN = 9, EMOTES = "FLEX", DRAWING = "piece" },
  { NAME = "GOBELET", CONCEPT = "De a 6 faces redessine selon le tirage.",
    EXAMPLE = "+-----+ |o   o| ...", USECASE = "presentation des /rand reels",
    MAXLEN = 9, EMOTES = "POINT, SHRUG, CHEER", DRAWING = "de dynamique", PICK = true },
  { NAME = "EVENTAIL", CONCEPT = "Main de 3 cartes Co Ca Pi Tr, face visible ou cachee.",
    EXAMPLE = ".-----. .-----. .-----.", USECASE = "distribution, bluff",
    MAXLEN = 23, EMOTES = "POINT, LAUGH", DRAWING = "cartes" },
  { NAME = "TABLE ROYALE", CONCEPT = "Table vue du dessus, la scene est dressee.",
    EXAMPLE = ".----------------------.", USECASE = "lancement de manche",
    MAXLEN = 24, EMOTES = "POINT", DRAWING = "table" },
  { NAME = "COFFRE", CONCEPT = "Coffre OR, version JACKPOT sous les 97+.",
    EXAMPLE = "+------------+ |   * OR *   |", USECASE = "victoire",
    MAXLEN = 14, EMOTES = "CHEER, APPLAUD", DRAWING = "coffre", PICK = true },
  { NAME = "DUEL", CONCEPT = "Deux silhouettes o face a face, fleches tendues.",
    EXAMPLE = "o --->  <--- o", USECASE = "defi, provocation",
    MAXLEN = 15, EMOTES = "ROAR, TAUNT", DRAWING = "duel" },
  { NAME = "TICKET", CONCEPT = "Billet numerote pour les tirages animes au vocal.",
    EXAMPLE = "| TICKET No 07   |", USECASE = "tirage, tombola",
    MAXLEN = 18, EMOTES = "aucune", DRAWING = "ticket" },
  { NAME = "BANNIERE", CONCEPT = "Une seule ligne >> ... << pour YELL et raid.",
    EXAMPLE = ">> Alice remporte 97 ! <<", USECASE = "annonce compacte",
    MAXLEN = 48, EMOTES = "aucune", DRAWING = "aucun" },
  { NAME = "SCEAU", CONCEPT = "Prefixe (sceau) discret pour les whispers.",
    EXAMPLE = "(sceau) Tu es des notres.", USECASE = "whispers RP",
    MAXLEN = 48, EMOTES = "aucune", DRAWING = "aucun" },
}

-- ============================================================
-- 1. OUTILS
-- ============================================================
function Style._now()
  if GetTime and type(GetTime) == "function" then
    local ok, t = pcall(GetTime)
    if ok and type(t) == "number" then return t end
  end
  if os and os.time then return os.time() end
  return 0
end

-- Tirage anti-doublon : ne rejoue jamais 2 fois la meme variante de suite.
function Style._pick(key, list)
  if #list == 0 then return "" end
  if #list == 1 then return list[1] end
  local last = self and self._lastVariant and self._lastVariant[key]
  local choice = list[1]
  for _ = 1, 6 do
    choice = list[math.random(#list)]
    if choice ~= last then break end
  end
  Style._lastVariant[key] = choice
  return choice
end

function Style._safe(line)
  line = tostring(line or "")
  if #line > 255 then line = line:sub(1, 255) end
  return line
end

function Style._prune()
  local now = Style._now()
  local fresh = {}
  for _, t in ipairs(Style._queue and Style._window or {}) do
    if now - t < Style.WINDOW then fresh[#fresh + 1] = t end
  end
  Style._window = fresh
end

function Style._budgetLeft()
  Style._prune()
  return Style.BUDGET - #Style._window
end

function Style._stamp()
  Style._window[#Style._window + 1] = Style._now()
end

function Style._sendRaw(line)
  line = Style._safe(line)
  if line == "" then return end
  if Style._budgetLeft() > 0 then
    Style._stamp()
    CasinoBae:Say(line)
  else
    Style._queue[#Style._queue + 1] = line
    Style._scheduleFlush()
  end
end

function Style._scheduleFlush()
  if Style._flushScheduled then return end
  if C_Timer and C_Timer.After then
    Style._flushScheduled = true
    C_Timer.After(Style.WINDOW, function()
      Style._flushScheduled = false
      Style.Flush()
    end)
  end
end

-- Vide la file d'attente (appele par le timer, ou a la prochaine commande).
function Style.Flush()
  local i = 1
  while Style._queue[i] ~= nil and Style._budgetLeft() > 0 do
    Style._stamp()
    CasinoBae:Say(Style._queue[i])
    i = i + 1
  end
  local rest = {}
  while Style._queue[i] ~= nil do rest[#rest + 1] = Style._queue[i]; i = i + 1 end
  Style._queue = rest
  if #Style._queue > 0 then Style._scheduleFlush() end
end

function Style._emoteText(text)
  if not text or text == "" then return end
  text = Style._safe(text)
  if Style._budgetLeft() > 0 then
    Style._stamp()
    SendChatMessage(text, "EMOTE")
  else
    Style._queue[#Style._queue + 1] = text
    Style._scheduleFlush()
  end
end

-- Joue une scene : animation du perso -> /e RP -> lignes SAY -> signature.
-- Mode compact : une seule ligne resume + signature (raid, YELL, spam).
function Style.Play(scene)
  if not scene then return end
  Style.Flush()
  if Style.Compact and scene.compact then
    if scene.anim and Style.EMOTES[scene.anim] then CasinoBae:Emote(scene.anim) end
    Style._sendRaw(scene.compact)
    Style._sendRaw(Style.SIGNATURE)
    return
  end
  if scene.anim and Style.EMOTES[scene.anim] then CasinoBae:Emote(scene.anim) end
  if scene.emote then Style._emoteText(scene.emote) end
  for _, line in ipairs(scene.lines or {}) do Style._sendRaw(line) end
  if scene.signed ~= false then Style._sendRaw(Style.SIGNATURE) end
end

function Style.Banner(text)
  return ">> " .. tostring(text or "") .. " <<"
end

function Style.Seal(text)
  return "(sceau) " .. tostring(text or "")
end

-- ============================================================
-- 2. DESSINS ASCII (lignes <= 48, 100% ASCII)
-- ============================================================
local PIPS = {
  { "     ", "  o  ", "     " },
  { "o    ", "     ", "    o" },
  { "o    ", "  o  ", "    o" },
  { "o   o", "     ", "o   o" },
  { "o   o", "  o  ", "o   o" },
  { "o   o", "o   o", "o   o" },
}

function Style.DiceFace(v)
  v = math.max(1, math.min(6, tonumber(v) or 1))
  local p = PIPS[v]
  return { "+-----+", "|" .. p[1] .. "|", "|" .. p[2] .. "|", "|" .. p[3] .. "|", "+-----+" }
end

function Style.MiniDie()
  return { "+---+", "| o |", "+---+" }
end

function Style.Coin()
  return { ".---.", "(  G  )", " `---'" }
end

function Style.CoinTrio()
  return { ".---.  .---.  .---.", "(  G  )(  G  )(  G  )", " `---'   `---'   `---'" }
end

function Style.Card(rank, suit)
  rank = tostring(rank or "?"):sub(1, 2)
  suit = tostring(suit or "?"):sub(1, 2)
  local r = rank .. string.rep(" ", math.max(0, 5 - #rank))
  local pad = math.max(0, 5 - 2 - #suit)
  local left = math.floor(pad / 2)
  local s = string.rep(" ", left) .. suit .. string.rep(" ", pad - left)
  return { ".-----.", "|" .. r .. "|", "| " .. s .. " |", "`-----'" }
end

function Style.Fan(hidden)
  local a, b, c
  if hidden then
    a = Style.Card("?", "?"); b = Style.Card("?", "?"); c = Style.Card("?", "?")
  else
    a = Style.Card("A", "Co"); b = Style.Card("R", "Pi"); c = Style.Card("10", "Ca")
  end
  local out = {}
  for i = 1, 4 do out[i] = a[i] .. " " .. b[i] .. " " .. c[i] end
  return out
end

function Style.Table()
  return {
    ".----------------------.",
    "|  o   TABLE   o       |",
    "|      C B  C B        |",
    "`----------------------'",
  }
end

function Style.Chest(jackpot)
  if jackpot then
    return {
      "+------------+",
      "| *JACKPOT*  |",
      "|   * OR *   |",
      "|  o  o  o   |",
      "+------------+",
    }
  end
  return {
    "+------------+",
    "|   * OR *   |",
    "|  o  o  o   |",
    "+------------+",
  }
end

function Style.Rain()
  return { " o   o   o   o", "   o   o   o", " o   o   o   o" }
end

function Style.Duel()
  return { "o --->  <--- o" }
end

function Style.Ticket(num)
  num = tostring(num or "00")
  local label = "TICKET No " .. num
  label = label .. string.rep(" ", math.max(0, 14 - #label))
  return { "+----------------+", "| " .. label .. " |", "+----------------+" }
end

function Style.Curtain()
  return { ".:.  .:.  .:.  .:.  .:." }
end

function Style.Frame(title, sub)
  title = tostring(title or "CASINOBABE")
  sub = tostring(sub or "")
  local function center(s, w)
    s = tostring(s)
    if #s > w then s = s:sub(1, w) end
    local pad = w - #s
    local left = math.floor(pad / 2)
    return string.rep(" ", left) .. s .. string.rep(" ", pad - left)
  end
  local out = { "+--------------------------+", "| " .. center(title, 24) .. " |" }
  if sub ~= "" then out[#out + 1] = "| " .. center(sub, 24) .. " |" end
  out[#out + 1] = "+--------------------------+"
  return out
end

function Style.Mirror()
  local m = Style.MiniDie()
  local out = {}
  for i = 1, #m do out[i] = m[i] .. "  " .. m[i] end
  return out
end

-- ============================================================
-- 3. SCENES (chaque scene = { anim, emote, lines })
-- ============================================================
local E_OPEN = {
  "pose le rideau de velours et salue la table.",
  "ouvre grand les bras : la soiree commence.",
  "frappe deux fois dans ses mains, le casino s eveille.",
}
local E_EXIT = {
  "tire sa reverence, le rideau retombe.",
  "salue la table et s efface dans la lumiere.",
  "remet son chapeau : la Maison vous remercie.",
}
local E_LOBBY = {
  "La table est ouverte, les braves sont attendus.",
  "La Maison bat les cartes et attend ses invites.",
  "Croupier en place, table chaude, venez tenter l or.",
}
local E_DICE = {
  "secoue le gobelet : les des chantent.",
  "fait danser les des au creux de sa main.",
  "souffle sur les des et les confie au destin.",
}
local E_DEAL = {
  "effeuille les cartes d un geste vif.",
  "distribue les cartes, face cachee, sans un mot.",
  "glisse une carte a chaque brave autour de la table.",
}
local E_BLUFF = {
  "plisse les yeux : quelqu un bluffe ici...",
  "affiche un sourire qui ne dit rien de bon.",
  "tapotant ses cartes : qui ose suivre ?",
}
local E_CHALLENGE = {
  "plante son regard dans celui de son rival.",
  "releve le defi, la table retient son souffle.",
  "croise les bras : qu un brave se leve !",
}
local E_TOAST = {
  "leve son verre a la sante des joueurs.",
  "trinque avec toute la table, que l or coule.",
  "savourant une gorgee : aux braves, aux audacieux !",
}

function Style.Entrance(compact)
  local lines = {}
  for _, l in ipairs(Style.Curtain()) do lines[#lines + 1] = l end
  if compact then
    lines[#lines + 1] = "La Maison ouvre ses portes."
  else
    lines[#lines + 1] = Style._pick("entrance", E_OPEN)
    lines[#lines + 1] = "Bienvenue a la table Casinobabe."
  end
  return { anim = "BECKON", emote = Style._pick("entrance_e", E_OPEN), lines = lines,
    compact = ">> La Maison ouvre ses portes <<" }
end

function Style.Exit()
  return {
    anim = "WAVE",
    emote = Style._pick("exit", E_EXIT),
    lines = { ".:.  .:.  .:.  .:.  .:.", "La Maison vous remercie, a demain les braves." },
    compact = ">> La Maison vous remercie <<" }
end

function Style.Lobby(host)
  local lines = Style.Frame("LA MAISON EST OUVERTE", "whisper : join")
  lines[#lines + 1] = Style._pick("lobby", E_LOBBY)
  if host and host ~= "" then lines[#lines + 1] = "Croupier : " .. tostring(host) end
  return { anim = "SALUTE", lines = lines, compact = ">> Table ouverte : whisper join <<" }
end

function Style.DiceInvite(round)
  local lines = {}
  for _, l in ipairs(Style.Table()) do lines[#lines + 1] = l end
  lines[#lines + 1] = Style._pick("dice", E_DICE)
  if round then
    lines[#lines + 1] = "MANCHE " .. tostring(round) .. " : chacun lance /rand !"
  else
    lines[#lines + 1] = "A vous : lancez /rand, que le meilleur gagne !"
  end
  return { anim = "POINT", emote = Style._pick("dice_e", E_DICE), lines = lines,
    compact = ">> MANCHE : lancez /rand ! <<" }
end

function Style.Deal()
  local lines = Style.Fan(true)
  lines[#lines + 1] = Style._pick("deal", E_DEAL)
  return { anim = "POINT", emote = Style._pick("deal_e", E_DEAL), lines = lines,
    compact = ">> Cartes distribuees <<" }
end

function Style.Draw(name)
  local lines = Style.Ticket(math.random(1, 99))
  if name and name ~= "" then
    lines[#lines + 1] = "Ticket tire pour " .. tostring(name) .. " !"
  else
    lines[#lines + 1] = "Tirage ouvert : un ticket, un destin."
  end
  return { lines = lines, compact = ">> Tirage ouvert <<" }
end

function Style.Bluff()
  local lines = Style.Fan(true)
  lines[#lines + 1] = Style._pick("bluff", E_BLUFF)
  return { anim = "LAUGH", emote = Style._pick("bluff_e", E_BLUFF), lines = lines,
    compact = ">> Quelqu un bluffe ici... <<" }
end

function Style.Challenge(a, b)
  local lines = Style.Duel()
  if a and b then
    lines[#lines + 1] = tostring(a) .. " defie " .. tostring(b) .. " !"
  else
    lines[#lines + 1] = Style._pick("chall", E_CHALLENGE)
  end
  return { anim = "ROAR", emote = Style._pick("chall_e", E_CHALLENGE), lines = lines,
    compact = ">> Defi lance ! <<" }
end

function Style.Bow()
  return { anim = "BOW", emote = "tire sa reverence devant la table.",
    lines = { "Respect aux joueurs, honneur a la Maison." }, compact = ">> Respect aux joueurs <<" }
end

function Style.Toast()
  return { anim = "TOAST", emote = Style._pick("toast", E_TOAST), lines = Style.CoinTrio(),
    compact = ">> A la sante des joueurs <<" }
end

-- Resultat REEL d'un /rand : composition dynamique selon le palier.
function Style.RollResult(name, value)
  value = tonumber(value) or 0
  name = tostring(name or "???")
  local tier
  if value >= 100 then tier = "crit" elseif value >= 67 then tier = "high"
  elseif value >= 34 then tier = "mid" else tier = "low" end
  local face = ((value - 1) % 6) + 1
  local lines = Style.DiceFace(face)
  if tier == "crit" then
    lines[#lines + 1] = "CRITIQUE ! " .. name .. " frappe " .. value .. " !"
    for _, l in ipairs(Style.Rain()) do lines[#lines + 1] = l end
    return { anim = "CHEER", emote = "explose de joie : un critique parfait !", lines = lines,
      compact = ">> CRITIQUE : " .. name .. " " .. value .. " ! <<" }
  elseif tier == "high" then
    lines[#lines + 1] = name .. " lance " .. value .. " ! La table siffle."
    return { anim = "APPLAUD", emote = "applaudit le lancer de " .. name .. ".", lines = lines,
      compact = ">> " .. name .. " : " .. value .. " <<" }
  elseif tier == "mid" then
    lines[#lines + 1] = name .. " lance " .. value .. " : la partie reste ouverte."
    return { lines = lines, compact = ">> " .. name .. " : " .. value .. " <<" }
  else
    lines[#lines + 1] = name .. " lance " .. value .. " : les des restent froids."
    return { anim = "SHRUG", emote = "hausse les epaules devant ce lancer.", lines = lines,
      compact = ">> " .. name .. " : " .. value .. " <<" }
  end
end

function Style.Win(name, value)
  value = tonumber(value) or 0
  name = tostring(name or "???")
  local jackpot = value >= 97
  local lines = Style.Chest(jackpot)
  if jackpot then
    for _, l in ipairs(Style.Rain()) do lines[#lines + 1] = l end
    lines[#lines + 1] = "JACKPOT ! " .. name .. " rafle tout avec " .. value .. " !"
    return { anim = "APPLAUD", emote = "tombe a genoux devant le coffre grand ouvert !", lines = lines,
      compact = ">> JACKPOT : " .. name .. " " .. value .. " ! <<" }
  end
  lines[#lines + 1] = "VICTOIRE : " .. name .. " avec " .. value .. " !"
  return { anim = "CHEER", emote = "brandit le coffre sous les hourras !", lines = lines,
    compact = ">> VICTOIRE : " .. name .. " " .. value .. " <<" }
end

function Style.Lose(name)
  name = tostring(name or "???")
  local lines = Style.Coin()
  lines[#lines + 1] = name .. " s incline : la roue tournera."
  lines[#lines + 1] = "(jeton) La Maison offre une revanche."
  return { anim = "PAT", emote = "tapote l epaule de " .. name .. " : releve-toi, brave.", lines = lines,
    compact = ">> " .. name .. " s incline <<" }
end

function Style.Tie(value)
  value = tonumber(value) or 0
  local lines = Style.Mirror()
  lines[#lines + 1] = "Miroir a " .. value .. " : on relance, lancez /rand !"
  return { anim = "SHRUG", emote = "ecarquille les yeux : egalite parfaite !", lines = lines,
    compact = ">> Egalite " .. value .. " : relancez <<" }
end

-- Sequence cinematique complete : entree -> table -> des -> cartes ->
-- bluff -> resultats -> victoire/defaite -> toast -> sortie.
function Style.Demo()
  local beats = {
    Style.Entrance(),
    Style.Lobby(UnitName and UnitName("player")),
    Style.DiceInvite(1),
    Style.Deal(),
    Style.Bluff(),
    Style.RollResult("Alice", 12),
    Style.RollResult("Bob", 58),
    Style.Tie(77),
    Style.RollResult("Cara", 100),
    Style.Win("Cara", 100),
    Style.Lose("Alice"),
    Style.Toast(),
    Style.Exit(),
  }
  for _, scene in ipairs(beats) do Style.Play(scene) end
  Style.Flush()
  return #beats
end

-- ============================================================
-- 4. COMMANDES : /casino <sub> (et relais depuis /cb)
-- ============================================================
function Style.Command(sub, args)
  sub = string.lower(tostring(sub or ""))
  args = tostring(args or "")
  if sub == "" or sub == "help" or sub == "?" then
    CasinoBae:Announce("casino : roll dice deal draw win lose bluff challenge bow toast entrance exit demo compact style")
    return true
  end
  if sub == "entrance" then Style.Play(Style.Entrance()); return true end
  if sub == "exit" then Style.Play(Style.Exit()); return true end
  if sub == "lobby" then Style.Play(Style.Lobby(UnitName and UnitName("player"))); return true end
  if sub == "dice" then Style.Play(Style.DiceInvite()); return true end
  if sub == "deal" then Style.Play(Style.Deal()); return true end
  if sub == "draw" then Style.Play(Style.Draw(args ~= "" and args or nil)); return true end
  if sub == "bluff" then Style.Play(Style.Bluff()); return true end
  if sub == "bow" then Style.Play(Style.Bow()); return true end
  if sub == "toast" then Style.Play(Style.Toast()); return true end
  if sub == "challenge" then
    local a, b = args:match("^(%S+)%s+(%S+)$")
    Style.Play(Style.Challenge(a, b)); return true
  end
  if sub == "roll" then
    local name, score = args:match("^(%S+)%s+(%d+)$")
    if name and score then Style.Play(Style.RollResult(name, tonumber(score)))
    else Style.Play(Style.DiceInvite()) end
    return true
  end
  if sub == "win" then
    local name, score = args:match("^(%S+)%s*(%d*)$")
    if not name or name == "" then CasinoBae:Announce("Usage: /casino win NOM [SCORE]"); return true end
    Style.Play(Style.Win(name, tonumber(score) or 0)); return true
  end
  if sub == "lose" then
    if args == "" then CasinoBae:Announce("Usage: /casino lose NOM"); return true end
    Style.Play(Style.Lose(args)); return true
  end
  if sub == "demo" then
    CasinoBae:Announce("DEMO Casinobabe : que le spectacle commence.")
    Style.Demo(); return true
  end
  if sub == "compact" then
    if args == "on" then Style.Compact = true
    elseif args == "off" then Style.Compact = false
    else Style.Compact = not Style.Compact end
    CasinoBae:Announce("Mode compact : " .. (Style.Compact and "ON" or "OFF")); return true
  end
  if sub == "style" then
    for _, s in ipairs(Style.STYLES) do
      CasinoBae:Say("[" .. s.NAME .. "] " .. s.CONCEPT .. (s.PICK and " <3>" or ""))
    end
    return true
  end
  CasinoBae:Announce("casino : sous-commande inconnue : " .. sub)
  return true
end

SLASH_CASINO1 = "/casino"
SlashCmdList.CASINO = function(message)
  local command, rest = (message or ""):match("^%s*(%S*)%s*(.*)$")
  Style.Flush()
  Style.Command(command, rest)
  Style.Flush()
end
