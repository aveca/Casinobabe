import fs from "fs"; import path from "path"; import { Lua } from "wasmoon-lua5.1";
const ROOT = process.cwd();
const lua = await Lua.create();
await lua.doString(fs.readFileSync(path.join(ROOT,"tests","wow_mock.lua"),"utf8"));
await lua.doString(fs.readFileSync(path.join(ROOT,"runtime-addon","Casinobabe","Casinobabe.lua"),"utf8"));
// CasinobabeDiag.lua is a vararg addon file: local ADDON, ns = ...
// Level-2 long bracket avoids colliding with the ]] inside the source.
const diag = fs.readFileSync(path.join(ROOT,"runtime-addon","Casinobabe","CasinobabeDiag.lua"),"utf8");
await lua.doString("local f = loadstring([==[\n" + diag + "\n]==]) f('Casinobabe', Casinobabe)");
await lua.doString(fs.readFileSync(path.join(ROOT,"runtime-addon","Casinobabe","CasinobabeFishing.lua"),"utf8"));
console.log(await lua.doString(fs.readFileSync(path.join(ROOT,"tests","ui_smoke.lua"),"utf8")));
