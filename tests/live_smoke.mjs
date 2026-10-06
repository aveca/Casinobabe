// LIVE SMOKE TEST - CasinoBae
// Charge le VRAI addon (fichier live WoW si présent, sinon runtime-addon)
// dans la VM Lua 5.1 + mock WoW, puis :
//   1. vérifie la syntaxe (load sans exécution)
//   2. charge Libs + LiveErrorCapture + Casinobabe
//   3. déclenche les événements de session (ADDON_LOADED, LOGIN, zone, chat)
//   4. exécute les commandes slash principales
//   5. échoue si une seule erreur Lua est capturée
//
// Usage : node tests/live_smoke.mjs [addonDir]

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Lua } from "wasmoon-lua5.1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

const LIVE_WOW_DIR =
  "C:\\Program Files (x86)\\World of Warcraft\\_anniversary_\\Interface\\AddOns\\Casinobabe";
const RUNTIME_DIR = path.join(ROOT, "runtime-addon", "Casinobabe");

const argDir = process.argv[2];
const ADDON_DIR = argDir
  ? path.resolve(argDir)
  : fs.existsSync(path.join(LIVE_WOW_DIR, "Casinobabe.lua"))
    ? LIVE_WOW_DIR
    : RUNTIME_DIR;

const failures = [];
const notes = [];

function fail(step, message) {
  failures.push({ step, message });
  console.log(`  FAIL [${step}] ${message}`);
}

function ok(step, message) {
  console.log(`  ok   [${step}] ${message}`);
}

if (!fs.existsSync(path.join(ADDON_DIR, "Casinobabe.lua"))) {
  console.error(`Casinobabe.lua introuvable dans ${ADDON_DIR}`);
  process.exit(2);
}

console.log(`ADDON DIR: ${ADDON_DIR}`);

const lua = await Lua.create();

// ---------------------------------------------------------------------------
// 1. SYNTAX CHECK (load sans exécution)
// ---------------------------------------------------------------------------
console.log("\n[1] Syntaxe");
const syntaxFiles = ["LiveErrorCapture.lua", "Casinobabe.lua"];
for (const name of syntaxFiles) {
  const file = path.join(ADDON_DIR, name);
  if (!fs.existsSync(file)) {
    notes.push(`${name} absent (${ADDON_DIR})`);
    continue;
  }
  const src = fs.readFileSync(file, "utf-8");
  try {
    // Lua 5.1 : loadstring (load attend un lecteur de fonction)
    const res = await lua.doString(
      `local fn, err = loadstring([==[${src}]==], ${JSON.stringify(name)})\n` +
        `if fn then return "SYNTAX_OK" else return "SYNTAX_ERR:" .. tostring(err) end`
    );
    if (String(res) === "SYNTAX_OK") ok("syntax", name);
    else fail("syntax", `${name} -> ${String(res)}`);
  } catch (e) {
    fail("syntax", `${name} -> ${typeof e === "string" ? e : e.message}`);
  }
}

// ---------------------------------------------------------------------------
// 2. CHARGEMENT MOCK + ADDON
// ---------------------------------------------------------------------------
console.log("\n[2] Chargement");
try {
  await lua.doString(fs.readFileSync(path.join(ROOT, "tests", "wow_mock.lua"), "utf-8"));
  ok("mock", "wow_mock.lua chargé");
} catch (e) {
  fail("mock", typeof e === "string" ? e : e.message);
  console.log("\nImpossible de charger le mock - arrêt.");
  process.exit(1);
}

// SavedVariables RÉELLES du client (état du joueur : dealer activé, historique,
// position du panneau...). C'est cet état qui fait planter l'addon en dur, pas
// un fichier vierge : on le recharge donc tel quel avant l'addon.
const WTF_ROOT = "C:\\Program Files (x86)\\World of Warcraft\\_anniversary_\\WTF";
const svFiles = [];
function walkSv(dir) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walkSv(full);
    else if (e.name === "Casinobabe.lua" && /SavedVariables$/.test(dir)) svFiles.push(full);
  }
}
if (process.env.CASINOBAE_SV !== "0") {
  walkSv(WTF_ROOT);
  svFiles.sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  if (svFiles.length) {
    try {
      await lua.doString(fs.readFileSync(svFiles[0], "utf-8"));
      ok("savedvars", svFiles[0]);
    } catch (e) {
      fail("savedvars", `${svFiles[0]} -> ${typeof e === "string" ? e : e.message}`);
    }
  } else {
    notes.push("aucun SavedVariables Casinobabe.lua trouvé dans WTF");
  }
}

// Libs : le mock fournit déjà LibStub + LibDBIcon (le vrai LibStub.lua est
// chargé en premier s'il existe, comme dans WoW).
const loadOrder = [
  path.join(ADDON_DIR, "Libs", "LibStub", "LibStub.lua"),
  path.join(ADDON_DIR, "Libs", "LibDBIcon-1.0", "LibDBIcon-1.0.lua"),
  path.join(ADDON_DIR, "LiveErrorCapture.lua"),
  path.join(ADDON_DIR, "Casinobabe.lua"),
];

for (const file of loadOrder) {
  const name = path.basename(file);
  if (!fs.existsSync(file)) {
    notes.push(`${name} absent - ignoré`);
    continue;
  }
  try {
    await lua.doString(fs.readFileSync(file, "utf-8"));
    ok("load", name);
  } catch (e) {
    fail("load", `${name} -> ${typeof e === "string" ? e : e.message}`);
  }
}

// ---------------------------------------------------------------------------
// 3. EVENEMENTS DE SESSION
// ---------------------------------------------------------------------------
console.log("\n[3] Événements de session");
const events = [
  ['WoWMock.fireEvent("ADDON_LOADED", "Casinobabe")', "ADDON_LOADED"],
  ['WoWMock.fireEvent("PLAYER_LOGIN")', "PLAYER_LOGIN"],
  ['WoWMock.fireEvent("PLAYER_ENTERING_WORLD")', "PLAYER_ENTERING_WORLD"],
  ['WoWMock.fireEvent("ZONE_CHANGED_NEW_AREA")', "ZONE_CHANGED_NEW_AREA"],
  ['WoWMock.fireEvent("GROUP_ROSTER_UPDATE")', "GROUP_ROSTER_UPDATE"],
  ['WoWMock.fireEvent("CHAT_MSG_WHISPER", "JOIN", "Testplayer-Realm")', "CHAT_MSG_WHISPER"],
  ['WoWMock.fireEvent("CHAT_MSG_SYSTEM", "Testplayer rolls 45 (1-100)")', "CHAT_MSG_SYSTEM"],
  ['WoWMock.fireEvent("TRADE_SHOW")', "TRADE_SHOW"],
  ['WoWMock.fireEvent("TRADE_CLOSED")', "TRADE_CLOSED"],
];

for (const [code, label] of events) {
  try {
    await lua.doString(code);
    ok("event", label);
  } catch (e) {
    fail("event", `${label} -> ${typeof e === "string" ? e : e.message}`);
  }
}

// Les timers différés (ouverture du panneau, watchers) ne partent que si le
// temps avance. On fait tourner 5 s de montre avant d'exercer l'UI.
try {
  await lua.doString("WoWMock.advanceTime(5)");
  ok("event", "advanceTime(5) après login");
} catch (e) {
  fail("event", `advanceTime -> ${typeof e === "string" ? e : e.message}`);
}

// ---------------------------------------------------------------------------
// 4. COMMANDES SLASH + FLUX DEALER/DEMO/TEST
// ---------------------------------------------------------------------------
console.log("\n[4] Commandes slash");

async function slash(cmd, label) {
  try {
    await lua.doString(
      `if SlashCmdList and SlashCmdList["CASINOBABE"] then SlashCmdList["CASINOBABE"](${JSON.stringify(
        cmd
      )}) else error("SlashCmdList CASINOBABE absent") end`
    );
    ok("slash", label || `/cb ${cmd}`);
  } catch (e) {
    fail("slash", `${label || `/cb ${cmd}`} -> ${typeof e === "string" ? e : e.message}`);
  }
}

async function tick(label, seconds = 1) {
  try {
    await lua.doString(`WoWMock.advanceTime(${seconds})`);
    ok("time", `${label} (+${seconds}s)`);
  } catch (e) {
    fail("time", `${label} -> ${typeof e === "string" ? e : e.message}`);
  }
}

async function clickScript(luaBody, label) {
  let report = "";
  try {
    report = await lua.doString(luaBody);
  } catch (e) {
    fail("click", `${label} -> ${typeof e === "string" ? e : e.message}`);
    return "";
  }
  const lines = String(report || "").split("\n").filter(Boolean);
  const first = lines[0] || "";
  if (first.startsWith("MISSING") || first.startsWith("NO_ONCLICK") || first.startsWith("ERR")) {
    fail("click", `${label}: ${first.replace(/\t/g, " ")}`);
    return "";
  }
  ok("click", `${label} (${first} clics)`);
  for (const line of lines.slice(1)) {
    const [name, ...rest] = line.split("\t");
    fail("click", `${label} / ${name}: ${rest.join("\t")}`);
  }
  return report;
}

const clickAllLua = `
local out, clicked = {}, 0
for i = 1, #WoWMock.config.allFrames do
  local f = WoWMock.config.allFrames[i]
  local fn = f.scripts and f.scripts.OnClick
  if fn and f.visible ~= false and not f.noAutoClick then
    clicked = clicked + 1
    local pok, err = pcall(fn, f, "LeftButton", false)
    if not pok then out[#out + 1] = (f.name or ("frame#" .. i)) .. "\\t" .. tostring(err) end
  end
end
return clicked .. "\\n" .. table.concat(out, "\\n")`;

// Hors dealer : l'UI refuse demo/test (comportement attendu).
await slash("dealer status");
await slash("dealer zone");
await slash("help");

// Le flux exact de la capture d'écran : dealer activé puis demo win / test win.
await slash("dealer on", "/cb dealer on");
await tick("après dealer on");

// ---------------------------------------------------------------------------
// 5. INTERFACE : OUVERTURE DU PANNEAU + CLICS
// ---------------------------------------------------------------------------
console.log("\n[5] Interface : panneau et boutons");

// Ouvre le panneau comme un joueur : clic sur le bouton minimap. C'est là que
// CreatePanel construit les ~200 widgets de l'UI (KUND-PANEL v4).
await clickScript(
  `
local f = WoWMock.config.uiFrames["LibDBIcon10_Casinobabe"]
if not f then return "MISSING frame" end
if not (f.scripts and f.scripts.OnClick) then return "NO_ONCLICK" end
local pok, err = pcall(f.scripts.OnClick, f, "LeftButton", false)
if not pok then return "ERR\\t" .. tostring(err) end
return "0"`,
  "clic bouton minimap"
);
await tick("ouverture du panneau", 1);

await slash("demo status");
await slash("demo win", "/cb demo win (auto-run)");
await tick("début du scénario demo", 0.5);
await tick("déroulé demo", 20);
await slash("demo next", "/cb demo next");
await tick("step suivant", 2);
await slash("demo next", "/cb demo next (2)");
await tick("step suivant 2", 2);
await slash("demo stop", "/cb demo stop");

await slash("test status");
await slash("test win", "/cb test win");
await tick("test", 5);
await slash("test stop", "/cb test stop");

// Clics massifs sur tous les boutons visibles (boutons du panneau, chips de
// mise, boutons dealer, boutons demo/test déjà vus).
await clickScript(clickAllLua, "passe 1 - boutons visibles");
await tick("après passe 1", 3);

await slash("dealer status");
await slash("dealer off", "/cb dealer off");
await tick("après dealer off");
await clickScript(clickAllLua, "passe 2 - boutons visibles (dealer off)");
await tick("après passe 2", 3);

// Logout en fin de parcours (libère les frames et sauvegarde les states).
try {
  await lua.doString('WoWMock.fireEvent("PLAYER_LOGOUT")');
  ok("event", "PLAYER_LOGOUT");
} catch (e) {
  fail("event", `PLAYER_LOGOUT -> ${typeof e === "string" ? e : e.message}`);
}

// ---------------------------------------------------------------------------
// 6. RAPPORT MOCK (erreurs Lua interceptées)
// ---------------------------------------------------------------------------
console.log("\n[6] Erreurs Lua interceptées par le mock");
// wasmoon renvoie une table Lua : on sérialise côté Lua avant de parser.
let serializedErrors = "";
try {
  serializedErrors = await lua.doString(`
local out = {}
for _, e in ipairs(WoWMock.errors) do
  out[#out + 1] = (e.phase or "?") .. "\\t" .. (e["function"] or "") .. "\\t" .. (e.message or "")
end
return table.concat(out, "\\n")`);
} catch (e) {
  fail("mock-errors", typeof e === "string" ? e : e.message);
}

const mockErrors = String(serializedErrors || "")
  .split("\n")
  .filter(Boolean)
  .map((line) => {
    const [phase, fn, ...rest] = line.split("\t");
    return { phase, fn, message: rest.join("\t") };
  });

const realErrors = [];
const benign = [];
for (const err of mockErrors) {
  const msg = String(err && err.message);
  const phase = String(err && err.phase);
  // Lecture/écriture d'un global non fourni par le mock : signal d'inventaire
  // du mock, pas un crash (un vrai nil indexé/callé est capturé en pcall plus
  // haut et remonte en tant qu'EVENT_HANDLER / load).
  if (phase === "UNDEFINED_GLOBAL_WRITE" || phase === "UNDEFINED_GLOBAL") {
    benign.push({ phase, msg });
  } else {
    realErrors.push({ phase, msg, fn: err && err.fn });
  }
}

for (const err of realErrors) {
  fail("lua", `${err.phase}: ${err.msg}${err.fn ? ` (${err.fn})` : ""}`);
}
if (benign.length) {
  notes.push(`${benign.length} accès à un global absent du mock (inventaire mock, non bloquant en dur)`);
  const missing = [
    ...new Set(
      benign
        .map((b) => (b.msg.match(/global: (\w+)/) || [])[1])
        .filter(Boolean)
    ),
  ];
  if (missing.length) notes.push(`globals non mokés: ${missing.join(", ")}`);
}
console.log(`  erreurs réelles: ${realErrors.length} | warnings mock: ${benign.length}`);

// ---------------------------------------------------------------------------
// SORTIE
// ---------------------------------------------------------------------------
console.log("\n=== RAPPORT LIVE SMOKE ===");
console.log(`Addon: ${ADDON_DIR}`);
console.log(`Échecs: ${failures.length}`);
for (const n of notes) console.log(`note: ${n}`);
for (const f of failures) console.log(`FAIL: [${f.step}] ${f.message}`);

const exitCode = failures.length > 0 ? 1 : 0;
const reportDir = path.join(ROOT, "reports");
fs.mkdirSync(reportDir, { recursive: true });
fs.writeFileSync(
  path.join(reportDir, "live-smoke-last.json"),
  JSON.stringify(
    { addonDir: ADDON_DIR, exitCode, failures, notes, realErrors, benignCount: benign.length, timestamp: new Date().toISOString() },
    null,
    2
  )
);
console.log(`EXIT CODE: ${exitCode}`);
process.exit(exitCode);
