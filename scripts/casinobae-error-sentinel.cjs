#!/usr/bin/env node
"use strict";

const fs=require("node:fs"),path=require("node:path"),{spawn,execFileSync}=require("node:child_process");

const svPath=process.env.CASINOBAE_SV_PATH||"";
const repo=process.env.CASINOBAE_REPO||process.cwd();
const wowAddon=process.env.CASINOBAE_WOW_ADDON||"";
const pollMs=Number(process.env.CASINOBAE_POLL_MS||2000);
const opencode=process.env.OPENCODE_CMD||"opencode";

if(!svPath){console.error("CASINOBAE_SV_PATH is required.");process.exit(2)}
if(!fs.existsSync(repo)){console.error("CASINOBAE_REPO does not exist:",repo);process.exit(2)}

const inbox=path.join(repo,".casino-errors","inbox"),stateFile=path.join(repo,".casino-errors","sentinel-state.json");
fs.mkdirSync(inbox,{recursive:true});

function load(){try{return JSON.parse(fs.readFileSync(stateFile,"utf8"))}catch{return{seen:[]}}}
function save(s){s.seen=(s.seen||[]).slice(-500);fs.writeFileSync(stateFile,JSON.stringify(s,null,2)+"\n")}
function unesc(s){return s.replace(/\\n/g,"\n").replace(/\\r/g,"\r").replace(/\\t/g,"\t").replace(/\\"/g,'\"').replace(/\\\\/g,"\\")}
function extract(lua){
 const out=[],re=/\["id"\]\s*=\s*"((?:\\.|[^"\\])*)"\s*,?[\s\S]*?\["time"\]\s*=\s*(\d+)\s*,?[\s\S]*?\["message"\]\s*=\s*"((?:\\.|[^"\\])*)"\s*,?[\s\S]*?\["stack"\]\s*=\s*"((?:\\.|[^"\\])*)"/g;
 let m;while((m=re.exec(lua)))out.push({id:unesc(m[1]),time:Number(m[2]),message:unesc(m[3]),stack:unesc(m[4])});return out;
}
function sh(cmd,args){return execFileSync(cmd,args,{cwd:repo,encoding:"utf8",stdio:["ignore","pipe","pipe"]}).trim()}
function validate(){
 sh("git",["diff","--check"]);
 try{execFileSync("luac",["-p",path.join(repo,"runtime-addon","Casinobabe","Casinobabe.lua")],{stdio:"ignore"});console.log("[test] luac PASS")}catch{console.log("[test] luac unavailable/skipped")}
}
function syncRuntime(){
 if(!wowAddon)return;
 const source=path.join(repo,"runtime-addon","Casinobabe");
 const r=spawn("robocopy",[source,wowAddon,"/E","/XO","/R:1","/W:1","/NFL","/NDL","/NJH","/NJS"],{stdio:"ignore",shell:true});
 return new Promise((resolve,reject)=>r.on("exit",c=>c<8?resolve():reject(new Error("robocopy exit "+c))).on("error",reject));
}
function githubSync(){
 const branch=sh("git",["branch","--show-current"]);
 if(!branch||branch==="main"){console.log("[github] main: push/PR disabled");return}
 validate();sh("git",["push","-u","origin",branch]);
 try{sh("gh",["pr","view","--json","number"]);console.log("[github] PR already exists")}
 catch{sh("gh",["pr","create","--base","main","--head",branch,"--fill"]);console.log("[github] PR created")}
}
function runAgent(incident,incidentPath){
 const prompt=[
 "CASINOBAE LIVE ERROR — FIX NOW",
 "Repository: "+repo,
 "Incident file: "+incidentPath,
 "Fix the ROOT CAUSE in the addon source. Search all related call sites and forward declarations.",
 "Run available Lua/static/offline tests. Never patch WTF/SavedVariables as a workaround.",
 "Create or use a dedicated agent branch and commit only the necessary repair.",
 "Do not auto-merge payment, payout, trade, security, or destructive-data changes. Leave those PR-gated.",
 "After a successful repair, leave the worktree committed. The sentinel handles push, PR and runtime sync.",
 "Incident:\n"+JSON.stringify(incident,null,2)
 ].join("\n");
 const c=spawn(opencode,["run",prompt],{cwd:repo,stdio:"inherit",shell:process.platform==="win32"});
 c.on("error",e=>console.error("[agent]",e.message));
 c.on("exit",async code=>{
   console.log("[agent] exit",code);
   if(code!==0){busy=false;return}
   try{validate();await syncRuntime();githubSync();console.log("[loop] FIX -> TEST -> WOW SYNC -> GITHUB PASS")}
   catch(e){console.error("[loop] post-fix failed:",e.message)}
   busy=false;
 });
}
let state=load(),busy=false;
function tick(){
 if(busy||!fs.existsSync(svPath))return;
 let lua;try{lua=fs.readFileSync(svPath,"utf8")}catch{return}
 for(const incident of extract(lua)){
   if(state.seen.includes(incident.id))continue;
   state.seen.push(incident.id);save(state);
   const p=path.join(inbox,incident.id.replace(/[^\w.-]/g,"_")+".json");
   fs.writeFileSync(p,JSON.stringify({source:"WoW SavedVariables",addon:"Casinobabe",repository:"aveca/Casinobabe",capturedAt:new Date().toISOString(),...incident},null,2)+"\n");
   console.log("\n[ERROR] NEW Casinobabe error:",incident.message);
   busy=true;runAgent(incident,p);break;
 }
}
console.log("CasinoBae Live Error Sentinel ACTIVE");
console.log("SavedVariables:",svPath);
console.log("Repo:",repo);
console.log("WoW addon:",wowAddon||"(sync disabled)");
console.log("Poll:",pollMs+"ms");
setInterval(tick,pollMs);tick();
