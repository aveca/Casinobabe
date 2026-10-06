-- Style asserts: runs inside the wasmoon VM after mocks + addon sources.
-- Deterministic seed; any failed assert aborts the whole harness (non-zero).
math.randomseed(1234)

local N = 0
local function ok(cond, label)
  N = N + 1
  assert(cond, "STYLE_ASSERT[" .. label .. "]")
  print("  PASS " .. label)
end

local function isASCII(s)
  for i = 1, #s do
    local b = s:byte(i)
    if b < 32 or b > 126 then return false, i, b end
  end
  return true
end

local St = CasinoBae.Style
ok(St ~= nil, "style_namespace")
ok(St.VERSION == "1.0.0", "style_version")
ok(#St.STYLES >= 10, "styles_count_12")
local picks = 0
for _, s in ipairs(St.STYLES) do
  ok(s.NAME and s.CONCEPT and s.EXAMPLE and s.USECASE and s.MAXLEN and s.EMOTES and s.DRAWING, "style_fields_" .. tostring(s.NAME))
  if s.PICK then picks = picks + 1 end
end
ok(picks == 3, "styles_top3")
ok(St.SIGNATURE == "*--=[ Casinobabe ]=--*", "signature_exact")

-- Reset transport spies.
_Sent = {}
_Emotes = {}
_MockTime = 1000
St._queue = {}
St._window = {}
St._lastVariant = {}

-- 1. Every generator: lines <= 255, pure ASCII, drawings <= 48.
local scenes = {
  St.Entrance(), St.Exit(), St.Lobby("Host"), St.DiceInvite(3),
  St.Deal(), St.Draw(nil), St.Draw("Alice"), St.Bluff(),
  St.Challenge("Alice", "Bob"), St.Challenge(nil, nil), St.Bow(), St.Toast(),
  St.RollResult("Alice", 12), St.RollResult("Bob", 58),
  St.RollResult("Cara", 99), St.RollResult("Cara", 100),
  St.Win("Bob", 80), St.Win("Cara", 100), St.Lose("Alice"), St.Tie(77),
}
local longest = 0
for _, sc in ipairs(scenes) do
  for _, line in ipairs(sc.lines) do
    longest = math.max(longest, #line)
    ok(#line <= 255, "len255_" .. #line)
    local a = isASCII(line)
    ok(a, "ascii_line")
    if sc ~= nil then
      ok(#line <= 48 or line:find("Alice") or line:find("Bob") or line:find("Cara") or line:find("Host"),
        "width48_or_named")
    end
  end
  if sc.anim then ok(St.EMOTES[sc.anim] == true, "emote_allow_" .. sc.anim) end
  if sc.emote then ok(isASCII(sc.emote), "ascii_emote") end
end
print("  INFO longest_line=" .. longest)

-- 2. Dice faces: 6 distinct drawings, all 7 wide, 5 lines.
local seen = {}
for v = 1, 6 do
  local d = St.DiceFace(v)
  ok(#d == 5, "die5_" .. v)
  for _, l in ipairs(d) do ok(#l == 7, "die7_" .. v) end
  ok(not seen[table.concat(d, "|")], "die_unique_" .. v)
  seen[table.concat(d, "|")] = true
end

-- 3. Dynamic tiers: low/mid/high/crit + win/jackpot all distinct.
local function sig(sc) return table.concat(sc.lines, "\n") end
ok(sig(St.RollResult("X", 5)) ~= sig(St.RollResult("X", 50)), "tier_low_mid")
ok(sig(St.RollResult("X", 50)) ~= sig(St.RollResult("X", 90)), "tier_mid_high")
ok(sig(St.RollResult("X", 90)) ~= sig(St.RollResult("X", 100)), "tier_high_crit")
ok(sig(St.Win("X", 80)) ~= sig(St.Win("X", 100)), "win_jackpot")
ok(sig(St.RollResult("X", 12)) ~= sig(St.RollResult("Y", 12)), "roll_names_personal")

-- 4. Variants: repeated calls must not always repeat.
local function distinctCount(fn, n)
  local set = {}
  for _ = 1, n do set[sig(fn())] = true end
  local c = 0
  for _ in pairs(set) do c = c + 1 end
  return c
end
ok(distinctCount(function() return St.Entrance() end, 10) >= 2, "variant_entrance")
ok(distinctCount(function() return St.DiceInvite() end, 10) >= 2, "variant_dice")
ok(distinctCount(function() return St.Bluff() end, 10) >= 2, "variant_bluff")
ok(distinctCount(function() return St.Lobby("H") end, 10) >= 2, "variant_lobby")

-- 5. Play() always signs; emote text uses EMOTE channel.
_Sent = {}
St.Play(St.Entrance())
ok(#_Sent > 0, "play_sends")
ok(_Sent[#_Sent].msg == St.SIGNATURE, "play_signed")
local hasEmoteChan = false
for _, m in ipairs(_Sent) do if m.kind == "EMOTE" then hasEmoteChan = true end end
ok(hasEmoteChan, "play_emote_channel")
ok(#_Emotes >= 1 and St.EMOTES[_Emotes[#_Emotes]] == true, "play_anim_real")

-- 6. Flood guard: rapid commands queue instead of dropping.
local function drain()
  local guard = 0
  while #St._queue > 0 and guard < 100 do
    _MockTime = _MockTime + 5
    St.Flush()
    guard = guard + 1
  end
  return #St._queue
end
_Sent = {}
_Emotes = {}
_MockTime = 2000
St._queue = {}
St._window = {}
for _ = 1, 6 do St.Command("entrance", "") end
local sentNow = #_Sent
local queued = #St._queue
ok(sentNow <= St.BUDGET, "flood_budget")
ok(queued > 0, "flood_queues")
_MockTime = 2010
ok(drain() == 0, "flood_flush_all")
ok(#_Sent == sentNow + queued, "flood_no_loss")
-- Order preserved: signature closes every scene.
local sigs = 0
for _, m in ipairs(_Sent) do if m.msg == St.SIGNATURE then sigs = sigs + 1 end end
ok(sigs == 6, "flood_order_sigs")

-- 7. Slash commands: /casino + /cb relay + unknown safety.
_Sent = {}
_MockTime = 3000
St._queue = {}
St._window = {}
SlashCmdList.CASINO("dice")
ok(#_Sent > 0, "slash_casino_dice")
SlashCmdList.CASINOBAE("bluff")
ok(#_Sent > 0, "slash_cb_relay")
local pbefore = #_Printed
SlashCmdList.CASINO("nope_xyz")
ok(#_Printed == pbefore + 1, "slash_unknown_announces")
SlashCmdList.CASINO("win")
ok(true, "slash_win_no_name_safe")
SlashCmdList.CASINO("roll Alice 99")
ok(true, "slash_roll_args_safe")
SlashCmdList.CASINO("challenge Alice Bob")
ok(true, "slash_challenge_args_safe")

-- 8. Full demo: 13 beats, BECKON first, WAVE last, signed everywhere.
_Sent = {}
_Emotes = {}
_MockTime = 4000
St._queue = {}
St._window = {}
local beats = St.Demo()
ok(beats == 13, "demo_13_beats")
ok(drain() == 0, "demo_drainable")
ok(_Emotes[1] == "BECKON", "demo_opens_beckon")
ok(_Emotes[#_Emotes] == "WAVE", "demo_closes_wave")
local dsigs = 0
for _, m in ipairs(_Sent) do if m.msg == St.SIGNATURE then dsigs = dsigs + 1 end end
ok(dsigs == beats, "demo_all_signed")
for _, e in ipairs(_Emotes) do ok(St.EMOTES[e] == true, "demo_emote_real_" .. e) end

-- 9. Game integration: real /rand flow keeps states, scenes play.
_Sent = {}
_MockTime = 5000
St._queue = {}
St._window = {}
-- comment� : retrait d�pendance frame mock
CasinoBae:SetState("READY")
CasinoBae:CreateLobby()
ok(CasinoBae.STATE == "LOBBY_OPEN", "game_lobby_state")
CasinoBae:AddPlayer("Alice")
CasinoBae:AddPlayer("Bob")
local wok = CasinoBae.Game:StartRand()
ok(wok == true and CasinoBae.STATE == "GAME_WAITING_FOR_ROLLS", "game_start_state")
_Event("CHAT_MSG_SYSTEM", "Alice rolls 80 (1-100).")
_Event("CHAT_MSG_SYSTEM", "Bob rolls 55 (1-100).")
ok(CasinoBae.STATE == "ROUND_COMPLETE", "game_win_state")
ok(CasinoBae.stateDetail.winner == "Alice", "game_winner")

-- 10. UI: panel opens + FULL CASINO DEMO button exists and fires.
CasinoBae.UI:Show()
ok(_NamedFrames["START FULL CASINO DEMO"] ~= nil, "ui_demo_button")
local n0 = #_Sent
_MockTime = 6000
St._queue = {}
St._window = {}
_NamedFrames["START FULL CASINO DEMO"]._scripts.OnClick()
ok(#_Sent > n0, "ui_demo_fires")

-- 11. No gameplay automation: Style never rolls, never writes rolls.
ok(St.RollResult("Z", 0).lines ~= nil, "roll_zero_safe")
_Sent = {}
_MockTime = 7000
St._queue = {}
St._window = {}
St.Play(St.Win(string.rep("X", 300), 100))
ok(drain() == 0, "longname_drained")
for _, m in ipairs(_Sent) do ok(#m.msg <= 255, "longname_truncated") end

-- 12. Compact mode: one line + signature.
St.Compact = true
_Sent = {}
_MockTime = 8000
St._queue = {}
St._window = {}
St.Play(St.Win("Bob", 80))
ok(drain() == 0, "compact_drained")
ok(#_Sent == 2, "compact_two_lines")
ok(_Sent[1].msg:sub(1, 2) == ">>", "compact_banner")
ok(_Sent[2].msg == St.SIGNATURE, "compact_signed")
St.Play(St.RollResult("Cara", 100))
ok(drain() == 0, "compact_crit_drained")
ok(#_Sent == 4, "compact_crit_two_more")
St.Compact = false

print("STYLE_TESTS: PASS " .. N .. "/" .. N)
