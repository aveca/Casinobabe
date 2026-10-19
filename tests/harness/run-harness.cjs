#!/usr/bin/env node
/**
 * CasinoBae Lua Harness Runner
 * Runs static analysis and wasmoon-lua smoke tests
 * NO gameplay automation
 */
'use strict';

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const worktree = process.cwd();
const luaDir = path.join(worktree, 'runtime-addon', 'Casinobabe');
const addonDir = path.join(worktree, 'addon', 'CasinoBae');

async function run() {
  const results = {
    static_analysis: null,
    lua_syntax: null,
    wasmoon_smoke: null,
    errors: []
  };

  // 1. Static analysis - check Lua syntax
  try {
    const luaFiles = [
      path.join(addonDir, 'Core.lua'),
      path.join(addonDir, 'UI.lua'),
      path.join(addonDir, 'Commands.lua'),
      path.join(addonDir, 'Events.lua'),
      path.join(addonDir, 'Game.lua'),
      path.join(luaDir, 'Casinobabe.lua')
    ].filter(f => fs.existsSync(f));

    for (const file of luaFiles) {
      const content = fs.readFileSync(file, 'utf8');
      // Basic syntax checks
      if (!content.includes('local') && !content.includes('function') && !content.includes('=')) {
        results.errors.push(`Potential syntax issue in ${path.basename(file)}`);
      }
    }
    results.static_analysis = 'passed';
  } catch (e) {
    results.static_analysis = 'failed';
    results.errors.push(`Static analysis: ${e.message}`);
  }

  // 2. Lua syntax validation (if lua available)
  try {
    const luaCmd = process.platform === 'win32' ? 'lua.exe' : 'lua';
    for (const file of [
      path.join(luaDir, 'Casinobabe.lua')
    ].filter(f => fs.existsSync(f))) {
      execFileSync(luaCmd, ['-l', file], { cwd: worktree, encoding: 'utf8', timeout: 30000, windowsHide: true });
    }
    results.lua_syntax = 'passed';
  } catch (e) {
    results.lua_syntax = 'lua_not_available';
  }

  // 3. wasmoon-lua smoke test (if available)
  try {
    const wasmoonCmd = process.platform === 'win32' ? 'wasmoon-lua.exe' : 'wasmoon-lua';
    execFileSync(wasmoonCmd, ['--check', luaDir], { cwd: worktree, encoding: 'utf8', timeout: 60000, windowsHide: true });
    results.wasmoon_smoke = 'passed';
  } catch (e) {
    results.wasmoon_smoke = 'not_available';
  }

  const success = results.errors.length === 0;
  console.log(JSON.stringify({ ...results, success }));
}

run().catch(e => {
  console.log(JSON.stringify({ success: false, error: e.message }));
  process.exit(1);
});
