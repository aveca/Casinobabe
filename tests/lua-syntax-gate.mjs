// LUA SYNTAX GATE — deployable Lua files must parse under Lua 5.1 (WoW grammar).
//
// Uses wasmoon-lua5.1 (a real Lua 5.1 VM) to loadstring() every .lua file
// under runtime-addon/ WITHOUT executing it. Any parse failure blocks deploy:
//   - unclosed table constructor `{`
//   - unclosed `function` / `if` / `do` / `for` / `while` (missing `end`)
//   - unbalanced parenthesis
//   - stray `end` / '<eof>' errors
//   - any other Lua 5.1 grammar rejection
//
// Exit 0 = all files parse OK. Exit 1 = at least one file rejected.
"use strict";

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "url";
import { Lua } from "wasmoon-lua5.1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const RUNTIME_DIR = path.join(ROOT, "runtime-addon");

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(p);
    else if (entry.isFile() && entry.name.toLowerCase().endsWith(".lua")) yield p;
  }
}

async function main() {
  if (!fs.existsSync(RUNTIME_DIR)) {
    console.error("LUA_SYNTAX_GATE_INFRA_ERROR: runtime dir missing: " + RUNTIME_DIR);
    process.exit(2);
  }

  const files = [...walk(RUNTIME_DIR)].sort();
  if (files.length === 0) {
    console.error("LUA_SYNTAX_GATE_INFRA_ERROR: no .lua files found under runtime-addon");
    process.exit(2);
  }

  const failures = [];
  for (const file of files) {
    const rel = path.relative(ROOT, file);
    const src = fs.readFileSync(file, "utf8");
    const lua = await Lua.create();
    try {
      lua.global.set("__gate_src", src);
      const result = await lua.doString(
        'local f, err = loadstring(__gate_src); if f then return "OK" else return tostring(err) end'
      );
      if (result === "OK") {
        console.log("PASS  " + rel);
      } else {
        failures.push({ file: rel, error: result });
        console.error("FAIL  " + rel + "  ->  " + result);
      }
    } catch (error) {
      failures.push({ file: rel, error: String(error) });
      console.error("FAIL  " + rel + "  ->  " + String(error));
    } finally {
      lua.global.close();
    }
  }

  const passed = files.length - failures.length;
  console.log(
    "LUA_SYNTAX_GATE=" + (failures.length === 0 ? "PASS" : "FAIL") +
    " FILES=" + passed + "/" + files.length
  );
  process.exit(failures.length === 0 ? 0 : 1);
}

main().catch((error) => {
  console.error("LUA_SYNTAX_GATE_INFRA_ERROR=" + (error && error.stack || error));
  process.exit(2);
});
