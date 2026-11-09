#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const addon = path.join(root, 'runtime-addon', 'Casinobabe');
const read = (file) => fs.readFileSync(path.join(addon, file), 'utf8');
const lua = read('Casinobabe.lua');
const toc = read('Casinobabe.toc');
const checks = [];
function check(name, ok) {
  checks.push({ name, ok: Boolean(ok) });
  console.log(`${ok ? '  ok ' : 'FAIL'} ${name}`);
}

check('TOC ne charge plus LibStub', !/LibStub/i.test(toc));
check('TOC ne charge plus LibDBIcon', !/LibDBIcon/i.test(toc));
check('runtime ne dépend plus de LibStub', !/\bLibStub\b/.test(lua));
check('runtime ne dépend plus de LibDBIcon', !/LibDBIcon/i.test(lua));
check('ancien asset background non référencé', !/media\\\\background|MEDIA/.test(lua));
check('bouton minimap natif nommé et créé', /CreateFrame\("Button",\s*"CasinobabeMinimapButton",\s*Minimap\)/.test(lua));
check('bouton minimap natif ouvre le panneau', /btn:SetScript\("OnClick"/.test(lua) && /OpenPanel\(\)/.test(lua));
check('bouton minimap natif conserve sa position', /CasinobabeDB\.minimap\.angle/.test(lua) && /OnDragStart/.test(lua));
check('plus aucune référence à l’ancien nom de frame', !/LibDBIcon10_Casinobabe/.test(lua));
check('fichier LibStub supprimé', !fs.existsSync(path.join(addon, 'Libs', 'LibStub', 'LibStub.lua')));
check('fichier LibDBIcon supprimé', !fs.existsSync(path.join(addon, 'Libs', 'LibDBIcon-1.0', 'LibDBIcon-1.0.lua')));
check('texture background sans provenance supprimée', !fs.existsSync(path.join(addon, 'media', 'background.tga')));

const failed = checks.filter((item) => !item.ok);
console.log(failed.length ? `RUNTIME RIGHTS : ${failed.length} ECHEC(S)` : 'RUNTIME RIGHTS : OK');
process.exit(failed.length ? 1 : 0);
