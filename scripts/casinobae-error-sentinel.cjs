#!/usr/bin/env node
"use strict";
const fs=require("node:fs"),path=require("node:path"),{spawn}=require("node:child_process");
const svPath=process.env.CASINOBAE_SV_PATH,repo=process.env.CASINOBAE_REPO||process.cwd(),pollMs=Number(process.env.CASINOBAE_POLL_MS||2000),opencode=process.env.OPENCODE_CMD||"opencode";
if(!svPath){console.error("CASINOBAE_SV_PATH is required.");process.exit(2)}
if(!fs.existsSync(repo)){console.error("CASINOBAE_REPO does not exist:",repo);process.exit(2)}
const inbox=path.join(repo,".casino-errors","inbox"),stateFile=path.join(repo,".casino-errors","sentinel-state.json");fs.mkdirSync(inbox,{recursive:true});
function load(){try{return JSON.parse(fs.readFileSync(stateFile,"utf8"))}catch{return{seen:[]}}}
function save(s){s.seen=s.seen.slice(-200);fs.writeFileSync(stateFile,JSON.stringify(s,null,2)+"\n")}
function unesc(s){return s.replace(/\\n/g,"\n").replace(/\\r/g,"\r").replace(/\\t/g,"\t").replace(/\\"/g,"\"").replace(/\\\\/g,"\\")}
function extract(lua){const out=[],re=/\["id"\]\s*=\s*"((?:\\.|[^"\\])*)"\s*,?[\s\S]*?\["time"\]\s*=\s*(\d+)\s*,?[\s\S]*?\["message"\]\s*=\s*"((?:\\.|[^"\\])*)"\s*,?[\s\S]*?\["stack"\]\s*=\s*"((?:\\.|[^"\\])*)"/g;let m;while((m=re.exec(lua)))out.push({id:unesc(m[1]),time:Number(m[2]),message:unesc(m[3]),stack:unesc(m[4])});return out}
function runAgent(i,p){const prompt=["CASINOBAE LIVE INCIDENT — AUTONOMOUS REPAIR","Repository: "+repo,"Incident file: "+p,"Inspect the exact source and fix the ROOT CAUSE. Search all related call sites and nil/forward-declaration hazards.","Run available Lua/static/offline tests. Never modify WTF/SavedVariables as a workaround.","Use a dedicated branch and commit. Payment, trade acceptance, payout accounting, security and destructive-data changes must remain PR-gated.","Push/open a PR only if configured by the local workflow.","Incident:\n"+JSON.stringify(i,null,2)].join("\n");const c=spawn(opencode,["run",prompt],{cwd:repo,stdio:"inherit",shell:process.platform==="win32"});c.on("error",e=>console.error("[sentinel] OpenCode:",e.message));c.on("exit",code=>console.log("[sentinel] OpenCode exit",code))}
let s=load(),busy=false;
function tick(){if(busy||!fs.existsSync(svPath))return;let lua;try{lua=fs.readFileSync(svPath,"utf8")}catch{return}for(const i of extract(lua)){if(s.seen.includes(i.id))continue;s.seen.push(i.id);save(s);const p=path.join(inbox,i.id.replace(/[^\w.-]/g,"_")+".json");fs.writeFileSync(p,JSON.stringify({source:"WoW SavedVariables",addon:"Casinobabe",repository:"aveca/Casinobabe",capturedAt:new Date().toISOString(),...i},null,2)+"\n");console.log("[sentinel] NEW:",i.id,i.message);busy=true;runAgent(i,p);setTimeout(()=>busy=false,1000)}}
console.log("CasinoBae Live Error Sentinel | SV="+svPath+" | repo="+repo);setInterval(tick,pollMs);tick();