// Style harness — loads the REAL modular addon (Core/Style/Events/Game/Commands/UI)
// in Lua 5.1 via wasmoon, then runs tests/style_assert.lua.
// Static source checks (pure ASCII, no gameplay automation) run first.
"use strict";

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Lua } from "wasmoon-lua5.1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const ADDON = path.join(ROOT, "addon", "CasinoBae");
const STYLE_SRC = path.join(ADDON, "Style.lua");

let failures = 0;
function check(name, cond, detail) {
  console.log((cond ? "  PASS " : "  FAIL ") + name + (detail ? " — " + detail : ""));
  if (!cond) failures++;
}

// ---------- static source checks ----------
const src = fs.readFileSync(STYLE_SRC, "utf8");
let maxByte = 0;
let badAt = -1;
for (let i = 0; i < src.length; i++) {
  const c = src.charCodeAt(i);
  if (c === 10 || c === 13 || c === 9) continue;
  if (c > maxByte) maxByte = c;
  if (c > 126 && badAt === -1) badAt = i;
}
check("static_ascii_only", badAt === -1, "maxByte=" + maxByte);
check("static_no_emoji", !/[\u0080-\uFFFF]/.test(src), "no multibyte chars");
check("static_no_gambling_automation", !/StartRand|RecordRoll|RandomRoll|SendControl/.test(src), "RP/chat only");
check("static_has_catalog", /Style\.STYLES/.test(src) && /PICK = true/.test(src), "12 styles + top3");
check("static_has_slash", /SLASH_CASINO1/.test(src), "/casino registered");
check("static_has_demo", /function Style\.Demo/.test(src), "Demo() present");
check("static_version", /Style\.VERSION = "1\.0\.0"/.test(src), "Style 1.0.0");

// ---------- Lua mocks ----------
const MOCKS = `
_Sent = {}
_Emotes = {}
_Printed = {}
_NamedFrames = {}
_MockTime = 0
SlashCmdList = {}
UIParent = {}
STANDARD_TEXT_FONT = "Fonts\\\\FRIZQT__.TTF"
RaidWarningFrame = {}
ChatTypeInfo = { RAID_WARNING = {} }
function RaidNotice_AddMessage(...) end
function GetTime() return _MockTime end
function date(fmt) return "12:00:00" end
function UnitName(u) if u == "player" then return "Host" end return nil end
function UnitInParty(n) return false end
function SendChatMessage(msg, kind, lang, target)
  _Sent[#_Sent+1] = { kind = kind, msg = msg, target = target }
end
function print(...)
  local t = {}
  for i = 1, select('#', ...) do t[#t+1] = tostring(select(i, ...)) end
  _Printed[#_Printed+1] = table.concat(t, "\\t")
end
C_ChatInfo = { PerformEmote = function(token) _Emotes[#_Emotes+1] = token; return true end }
function DoEmote(token) _Emotes[#_Emotes+1] = token end
local function makeFontString(parent)
  local fs = { _parent = parent, _text = "" }
  function fs:SetFont(...) end
  function fs:SetTextColor(...) end
  function fs:SetText(t)
    self._text = tostring(t or "")
    if self._text == "START FULL CASINO DEMO" then _NamedFrames[self._text] = self._parent end
  end
  function fs:SetPoint(...) end
  function fs:SetWidth(...) end
  return fs
end
function CreateFrame(ftype, name, parent, template)
  local f = { _scripts = {}, _shown = false }
  function f:SetBackdrop(...) end
  function f:SetBackdropColor(...) end
  function f:SetBackdropBorderColor(...) end
  function f:SetSize(...) end
  function f:SetPoint(...) end
  function f:SetHeight(...) end
  function f:SetWidth(...) end
  function f:Hide() self._shown = false end
  function f:Show() self._shown = true end
  function f:SetMovable(...) end
  function f:EnableMouse(...) end
  function f:RegisterForDrag(...) end
  function f:RegisterEvent(...) end
  function f:SetScript(k, fn) self._scripts[k] = fn end
  function f:StartMoving() end
  function f:StopMovingOrSizing() end
  function f:CreateFontString(...) return makeFontString(self) end
  _EventFrame = f
  return f
end
function _Event(...)
  local h = _EventFrame._scripts.OnEvent
  assert(h, "event handler missing")
  h(nil, ...)
end
`;

async function main() {
  if (failures > 0) {
    console.log("STATIC_CHECKS_FAILED=" + failures);
    process.exit(1);
  }
  const lua = await Lua.create();
  try {
    await lua.doString(MOCKS);
    for (const f of ["Core.lua", "Style.lua", "Events.lua", "Game.lua", "Commands.lua", "UI.lua"]) {
      await lua.doString(fs.readFileSync(path.join(ADDON, f), "utf8"));
    }
    console.log("LOAD_ORDER_OK=Core,Style,Events,Game,Commands,UI");
  } catch (e) {
    console.error("LOAD_FAILED=" + String(e && e.stack || e));
    process.exit(2);
  }
  try {
    await lua.doString(fs.readFileSync(path.join(ROOT, "tests", "style_assert.lua"), "utf8"));
    const debugState = await lua.doString("return CasinoBae and CasinoBae.STATE or 'nil'");
    console.log("DEBUG_STATE=" + debugState);
  } catch (e) {
    console.error("ASSERT_FAILED=" + String(e && e.message || e));
    try {
      const out = await lua.doString("return table.concat(_Printed, '\\n')");
      console.error("--- partial output ---\n" + out);
    } catch (_) { /* ignore */ }
    process.exit(1);
  }
  const out = await lua.doString("return table.concat(_Printed, '\\n')");
  console.log(out);
  const sent = await lua.doString("return #_Sent");
  const emotes = await lua.doString("return table.concat(_Emotes, ',')");
  console.log("SENT_TOTAL=" + sent);
  console.log("EMOTES_SEEN=" + emotes);
  console.log("STYLE_HARNESS_EXIT=0");
  process.exit(0);
}

main().catch((e) => {
  console.error("HARNESS_INFRA_ERROR=" + (e.stack || e));
  process.exit(2);
});
