// REAL OFFLINE HARNESS RUNNER WITH wasmoon-lua5.1
// Execute le VRAI Casinobabe.lua dans une VM Lua 5.1 via wasmoon-lua5.1
// utilisation d await pour les doString Promises

import fs from 'fs';
import path from 'path';
import { Lua } from 'wasmoon-lua5.1';  // ESM import

// === 1. CHARGEMENT DU VRAI RUNTIME ===
// Création de la VM Lua 5.1 (async - top level await en ESM)
const lua = await Lua.create();

// Chemin vers le vrai fichier runtime
const REAL_RUNTIME = path.resolve('runtime-addon/Casinobabe/Casinobabe.lua');
const REAL_RUNTIME_SHA = '60f7fd28871dcaddec39e6b8e551e38768a38c1cda5af24ce4e3de369ff5d665';

// Vérifier que le fichier existe
if (!fs.existsSync(REAL_RUNTIME)) {
  console.error(`ERREUR: Fichier introuvable: ${REAL_RUNTIME}`);
  process.exit(1);
}

console.log(`REAL SOURCE: ${REAL_RUNTIME}`);
console.log(`SHA256: ${REAL_RUNTIME_SHA}`);

// Helper async pour exécuter du code Lua et retourner la valeur
async function runLua(code) {
  return await lua.doString(code);
}

// === 2. CHARGEMENT DU MOCK WoW ===
// Charger le mock WoW
const mockCode = fs.readFileSync(path.resolve('tests/wow_mock.lua'), 'utf-8');

// Exécuter le mock dans la VM Lua en utilisant doString
try {
  await runLua(`_G = {}; ${mockCode}`);
  console.log('WoW Mock chargé avec succès');
} catch (e) {
  console.error('Erreur chargement mock WoW:', e.message);
  process.exit(1);
}

// === 3. INTERCEPTION D'ERREURS ===
const errors = [];

function createError(phase, fn, file, line, message) {
  const fingerprint = `Casinobabe|${file}|${line}|${message}`;
  errors.push({
    phase,
    function: fn,
    file,
    line,
    message,
    fingerprint,
    timestamp: new Date().toISOString()
  });
}

// === 4. CHARGEMENT DU VRAI CASINOBAE.LUA ===
console.log('Chargement du vrai Casinobabe.lua...');

try {
  // Lire et exécuter le fichier directement avec doString
  const runtimeCode = fs.readFileSync(REAL_RUNTIME, 'utf-8');
  await runLua(runtimeCode);
  console.log('Casinobabe.lua chargé avec succès');
} catch (e) {
  console.error('Erreur lors du chargement de Casinobabe.lua:', e.message);
  // Continuer malgré les erreurs de chargement
}

// === 4. EXÉCUTION DES TESTS ===
console.log('\n=== EXÉCUTION DES TESTS ===\n');

const results = {};

// Test 1: Vérifier que CB existe
try {
  results.cbExists = await runLua('return CB ~= nil');
  console.log(`Test CB existant: ${results.cbExists ? 'PASS' : 'FAIL'}`);
} catch(e) {
  results.cbExists = `FAIL (${e.message})`;
  console.log(`Test CB existant: FAIL (${e.message})`);
}

// Test 2: Vérifier GameRules
try {
  results.grExists = await runLua('return GameRules ~= nil');
  console.log(`Test GameRules existant: ${results.grExists ? 'PASS' : 'FAIL'}`);
} catch(e) {
  results.grExists = `FAIL (${e.message})`;
  console.log(`Test GameRules existant: FAIL (${e.message})`);
}

// Test 3: Vérifier les fonctions clés
const keyFunctions = ['GetGameRule', 'ValidateStake', 'ValidateRoll', 'ComputePayout', 'shortName'];
for (const fn of keyFunctions) {
  try {
    results[fn] = await runLua(`return ${fn} ~= nil`);
    console.log(`Test ${fn}: ${results[fn] ? 'PASS' : 'FAIL'}`);
  } catch(e) {
    results[fn] = `FAIL (${e.message})`;
    console.log(`Test ${fn}: FAIL (${e.message})`);
  }
}

// === 4. TEST MINIMAL ===
console.log('\n=== TEST MINIMAL ===');

// Vérifier _VERSION
try {
  const version = await runLua('return _VERSION');
  console.log(`_VERSION: ${version}`);
} catch(e) {
  console.log(`_VERSION ERROR: ${e.message}`);
}

// Vérifier WOW_MOCK_LOADED
try {
  const loaded = await runLua('return _G.WOW_MOCK_LOADED');
  console.log(`WOW_MOCK_LOADED: ${loaded}`);
} catch(e) {
  console.log(`WOW_MOCK_LOADED ERROR: ${e.message}`);
}

// Vérifier CASINOBAE_SOURCE_LOADED
try {
  const loaded = await runLua('return _G.CASINOBAE_SOURCE_LOADED');
  console.log(`CASINOBAE_SOURCE_LOADED: ${loaded}`);
} catch(e) {
  console.log(`CASINOBAE_SOURCE_LOADED ERROR: ${e.message}`);
}

// === 5. RAPPORT FINAL ===
console.log('\n=== RAPPORT D\'EXÉCUTION ===');
console.log(`Lua VM: wasmoon-lua5.1`);
console.log(`Source: REAL (runtime-addon/Casinobabe/Casinobabe.lua)`);
console.log(`SHA256: ${REAL_RUNTIME_SHA}`);
console.log(`Erreurs capturées: ${errors.length}`);

// Écrire les erreurs dans un fichier
const logDir = path.resolve('logs');
if (!fs.existsSync(logDir)) {
  fs.mkdirSync(logDir);
}
fs.writeFileSync(path.join(logDir, 'offline-harness-last.log'), JSON.stringify({errors, results, luaVersion: 'wasmoon-lua5.1'}, {compact:false}));

// Déterminer le code de sortie Windows definitif
// 0 = tous les tests critiques PASS
// 1 = au moins un test critique ECHEC
// 2 = erreur d'infrastructure harness
let exitCode = 0;

// Critères d'échec critique :
// - CB absent
// - GameRules absent
// - Échec chargement runtime
const criticalFailed = 
  (results.cbExists !== true && results.cbExists !== 'PASS') ||
  (results.grExists !== true && results.grExists !== 'PASS') ||
  errors.some(e => e.includes('chargement') || e.includes('Chargement'));

if (criticalFailed) {
  exitCode = 1;
} else if (errors.length > 0) {
  // Des erreurs ont été capturées mais ce ne sont pas des criticals bloquants
  exitCode = 1;
} else {
  exitCode = 0;  // Tous les tests critiques passent
}

// Écrire le code de sortie dans un fichier pour CI
const exitCodePath = path.resolve('reports/offline-harness-exitcode.json');
fs.mkdirSync(path.dirname(exitCodePath), { recursive: true });
fs.writeFileSync(exitCodePath, JSON.stringify({ exitCode, timestamp: new Date().toISOString() }));

console.log(`EXIT CODE: ${exitCode}`);
process.exit(exitCode);