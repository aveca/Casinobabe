import fs from "fs"; import path from "path"; import { fileURLToPath } from "url";
import { Lua } from "wasmoon-lua5.1";
const ROOT = process.cwd();
const lua = await Lua.create();
await lua.doString(fs.readFileSync(path.join(ROOT,"tests","wow_mock.lua"),"utf8"));
await lua.doString(fs.readFileSync(path.join(ROOT,"runtime-addon","Casinobabe","Casinobabe.lua"),"utf8"));
console.log(await lua.doString(fs.readFileSync(path.join(ROOT,"state","payout_contract.lua"),"utf8")));
