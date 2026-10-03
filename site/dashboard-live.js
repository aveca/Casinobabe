import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";

const cfg=window.CasinoBaeSupabase;
const supabase=createClient(cfg.url,cfg.publishableKey,{auth:{persistSession:false,autoRefreshToken:false}});
let sessionId=sessionStorage.getItem("cb_session_id")||null;
let writeToken=sessionStorage.getItem("cb_session_token")||null;
let channel=null;

const $=id=>document.getElementById(id);
function setBridge(text,detail){
  if($("bridge")) $("bridge").textContent=text;
  if($("bridgeDetail")) $("bridgeDetail").textContent=detail;
}
function ensurePanel(){
  const bar=document.querySelector(".lobby-bar");
  if(!bar||document.getElementById("cb-live-panel")) return;
  const box=document.createElement("div");
  box.id="cb-live-panel";
  box.style.cssText="grid-column:1/-1;margin-top:14px;padding:14px;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(255,255,255,.025);font-size:11px;color:#aaa4b0";
  box.innerHTML='<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><strong style="color:#fff">LIVE SESSION</strong><code id="cb-session-id">creating…</code><button id="cb-copy-token" type="button" style="cursor:pointer">COPY BRIDGE TOKEN</button></div><div id="cb-session-note" style="margin-top:8px">Le token autorise uniquement l’ingestion des événements de cette session.</div>';
  bar.appendChild(box);
  $("cb-copy-token").addEventListener("click",async()=>{
    if(!writeToken) return;
    try{await navigator.clipboard.writeText(writeToken);$("cb-session-note").textContent="Token copié. Ne le publie pas."; }catch{ $("cb-session-note").textContent="Copie automatique refusée par le navigateur."; }
  });
}
async function createSession(){
  ensurePanel();
  if(sessionId&&writeToken){$("cb-session-id").textContent=sessionId;return;}
  const res=await fetch(cfg.url+cfg.createSessionPath,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({metadata:{source:"dealer-cockpit",page:location.href}})});
  if(!res.ok) throw new Error("session_create_"+res.status);
  const data=await res.json();
  sessionId=data.session_id;writeToken=data.write_token;
  sessionStorage.setItem("cb_session_id",sessionId);
  sessionStorage.setItem("cb_session_token",writeToken);
  $("cb-session-id").textContent=sessionId;
}
function normalize(row){
  return {name:row.event_type,payload:row.payload||{},state:row.payload?.state||null,time:row.created_at,actor:row.actor||null};
}
async function loadHistory(){
  const {data,error}=await supabase.from("casino_session_events").select("id,event_type,actor,payload,created_at").eq("session_id",sessionId).order("created_at",{ascending:false}).limit(100);
  if(error) throw error;
  for(const row of [...(data||[])].reverse()) window.CasinoBae.receiveAddonEvent(normalize(row),true);
}
function subscribe(){
  if(channel) channel.unsubscribe();
  channel=supabase.channel("casino-session-"+sessionId).on("postgres_changes",{event:"INSERT",schema:"public",table:"casino_session_events",filter:"session_id=eq."+sessionId},payload=>{
    window.CasinoBae.receiveAddonEvent(normalize(payload.new),true);
  }).subscribe(status=>{
    if(status==="SUBSCRIBED") setBridge("SUPABASE ONLINE","Session "+sessionId.slice(0,8)+"… · Realtime actif");
    else if(status==="CHANNEL_ERROR"||status==="TIMED_OUT") setBridge("SUPABASE DEGRADED",status);
  });
}
async function sendEvent(event){
  if(!sessionId||!writeToken||!event) return;
  const res=await fetch(cfg.url+cfg.addonEventPath,{method:"POST",headers:{"Content-Type":"application/json","x-casino-session-token":writeToken},body:JSON.stringify({event_type:String(event.name||"EVENT").slice(0,64),actor:event.actor||null,payload:event.payload||{}})});
  if(!res.ok) console.warn("CasinoBae event rejected",res.status);
}
const original=window.CasinoBae?.receiveAddonEvent;
window.CasinoBae.receiveAddonEvent=async function(event,remote=false){
  if(original) original(event);
  if(!remote) await sendEvent(event);
};
(async()=>{
  try{
    await createSession();
    await loadHistory();
    subscribe();
  }catch(error){
    console.error(error);
    setBridge("SUPABASE OFFLINE",String(error.message||error));
  }
})();