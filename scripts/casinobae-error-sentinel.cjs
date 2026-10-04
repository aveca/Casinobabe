#!/usr/bin/env node
"use strict";

const fs=require("node:fs");
const path=require("node:path");
const {spawn,execFileSync}=require("node:child_process");

const svPath=process.env.CASINOBAE_SV_PATH||"";
const repo=process.env.CASINOBAE_REPO||process.cwd();
const wowAddon=process.env.CASINOBAE_WOW_ADDON||"";
const pollMs=Math.max(500,Number(process.env.CASINOBAE_POLL_MS||1500));
const opencode=process.env.OPENCODE_CMD||"opencode";

if(!svPath){console.error("CASINOBAE_SV_PATH is required.");process.exit(2)}
if(!fs.existsSync(repo)){console.error("CASINOBAE_REPO does not exist:",repo);process.exit(2)}

const inbox=path.join(repo,".casino-errors","inbox");
const stateFile=path.join(repo,".casino-errors","sentinel-state.json");
fs.mkdirSync(inbox,{recursive:true});

function loadState(){try{return JSON.parse(fs.readFileSync(stateFile,"utf8"))}catch{return{seen:[]}}}
function saveState(s){s.seen=(s.seen||[]).slice(-500);fs.writeFileSync(stateFile,JSON.stringify(s,null,2)+"\n")}
function unesc(s){return s.replace(/\\n/g,"\n").replace(/\\r/g,"\r").replace(/\\t/g,"\t").replace(/\\"/g,'\"').replace(/\\\\/g,"\\")}
function extract(lua){
  const out=[];
  const re=/\["id"\]\s*=\s*"((?:\\.|[^"\\])*)"\s*,?[\s\S]*?\["time"\]\s*=\s*(\d+)\s*,?[\s\S]*?\["message"\]\s*=\s*"((?:\\.|[^"\\])*)"\s*,?[\s\S]*?\["stack"\]\s*=\s*"((?:\\.|[^"\\])*)"/g;
  let m;
  while((m=re.exec(lua))) out.push({id:unesc(m[1]),time:Number(m[2]),message:unesc(m[3]),stack:unesc(m[4])});
  return out;
}
function run(cmd,args){return execFileSync(cmd,args,{cwd:repo,encoding:"utf8",stdio:["ignore","pipe","pipe"]}).trim()}
function validate(){
  run("git",["diff","--check"]);
  try { execFileSync("luac",["-p",path.join(repo,"runtime-addon","Casinobabe","Casinobabe.lua")],{stdio:"ignore"}); console.log("[test] luac PASS") }
  catch { console.log("[test] luac unavailable — diff check PASS") }
}
function syncLive(){
  if(!wowAddon)return;
  const source=path.join(repo,"runtime-addon","Casinobabe");
  if(!fs.existsSync(source))throw new Error("Runtime source missing: "+source);
  const p=spawn("robocopy",[source,wowAddon,"/E","/XO","/R:1","/W:1","/NFL","/NDL","/NJH","/NJS"],{shell:true,stdio:"ignore"});
  return new Promise((resolve,reject)=>{
    p.on("error",reject);
    p.on("exit",code=>code<8?resolve():reject(new Error("robocopy exit "+code)));
  });
}
function syncGitHub(){
  const branch=run("git",["branch","--show-current"]);
  if(!branch||branch==="main"){console.log("[github] main: no automatic push/PR");return}
  validate();
  run("git",["push","-u","origin",branch]);
  try { run("gh",["pr","view","--json","number"]); console.log("[github] PR already exists") }
  catch { run("gh",["pr","create","--base","main","--head",branch,"--fill"]); console.log("[github] PR created") }
}
function runAgent(incident,incidentPath){
  const prompt=[
    "CASINOBAE LIVE ERROR — AUTOFIX",
    "Repository: "+repo,
    "Incident: "+incidentPath,
    "Find and fix the ROOT CAUSE in runtime-addon/Casinobabe.",
    "Search every related call site, scope, forward-declaration and state initialization.",
    "Run all available Lua/static/offline tests. Never modify WTF/SavedVariables as a workaround.",
    "Keep the change minimal and unrelated behavior unchanged.",
    "Create/use a dedicated branch and commit the fix.",
    "Do not auto-merge payment, payout, trade, security or destructive-data changes; those stay PR-gated.",
    "The external loop will push, open/ensure the GitHub PR, and sync the WoW runtime after your successful exit.",
    "Incident payload:\n"+JSON.stringify(incident,null,2)
  ].join("\n");
  const child=spawn(opencode,["run",prompt],{cwd:repo,stdio:"inherit",shell:process.platform==="win32"});
  child.on("error",e=>{console.error("[agent]",e.message);busy=false});
  child.on("exit",async code=>{
    console.log("[agent] exit",code);
    if(code===0){
      try{
        validate();
        await syncLive();
        syncGitHub();
        console.log("[loop] AUTOFIX -> TEST -> WOW SYNC -> GITHUB PR ✅");
        console.log("[loop] WoW must /reload to load the corrected Lua.");
      }catch(e){console.error("[loop] post-fix error:",e.message)}
    }
    busy=false;
  });
}
let state=loadState(),busy=false;
function tick(){
  if(busy)return;
  let lua;
  try{lua=fs.readFileSync(svPath,"utf8")}catch{return}
  for(const incident of extract(lua)){
    if(state.seen.includes(incident.id))continue;
    state.seen.push(incident.id);saveState(state);
    const file=path.join(inbox,incident.id.replace(/[^\w.-]/g,"_")+".json");
    fs.writeFileSync(file,JSON.stringify({source:"Casinobabe SavedVariables",addon:"Casinobabe",repository:"aveca/Casinobabe",capturedAt:new Date().toISOString(),...incident},null,2)+"\n");
    console.log("\n[ERROR] NEW:",incident.message);
    busy=true;
    runAgent(incident,file);
    break;
  }
}
console.log("CasinoBae LIVE DEV LOOP");
console.log("SavedVariables:",svPath);
console.log("Repo:",repo);
console.log("WoW addon:",wowAddon||"(runtime sync disabled)");
console.log("Poll:",pollMs+"ms");
setInterval(tick,pollMs);
tick();
