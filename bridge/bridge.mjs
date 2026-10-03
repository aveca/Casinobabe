import fs from "node:fs";
import path from "node:path";
const configPath=path.join(process.cwd(),"config.json");
if(!fs.existsSync(configPath)){console.error("Missing config.json");process.exit(1);}
const cfg=JSON.parse(fs.readFileSync(configPath,"utf8"));
const endpoint=new URL(cfg.eventFunctionPath,cfg.supabaseUrl).toString();
let sent=new Set();
function parseLua(text){
  const records=[]; const re=/name="([^"]*)",state="([^"]*)",time="([^"]*)",payload=(%b{})/g; let m;
  while((m=re.exec(text))) records.push({name:m[1],state:m[2],time:m[3],payload:parsePayload(m[4])});
  return records;
}
function parsePayload(raw){
  const out={}; const body=raw.slice(1,-1); const re=/\[?"([^"]+)"\]?\s*=\s*"([^"]*)"/g; let m;
  while((m=re.exec(body))) out[m[1]]=m[2]; return out;
}
async function send(event){
  const key=JSON.stringify(event); if(sent.has(key)) return;
  const res=await fetch(endpoint,{method:"POST",headers:{"content-type":"application/json","x-casino-session-token":cfg.sessionToken},body:JSON.stringify({event_type:event.name,actor:"WoW-addon",payload:{...event.payload,state:event.state,time:event.time}})});
  if(!res.ok) throw new Error("Supabase rejected "+res.status);
  sent.add(key); if(sent.size>500) sent=new Set([...sent].slice(-250));
  console.log(new Date().toISOString(),"forwarded",event.name);
}
let last="";
async function tick(){try{if(!fs.existsSync(cfg.wowSavedVariables))return;const text=fs.readFileSync(cfg.wowSavedVariables,"utf8");if(text===last)return;last=text;for(const e of parseLua(text))await send(e);}catch(e){console.error("bridge:",e.message);}}
console.log("CasinoBae bridge watching",cfg.wowSavedVariables); setInterval(tick,Math.max(500,Number(cfg.pollMs)||1500)); tick();
