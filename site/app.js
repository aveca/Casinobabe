const connection=document.querySelector("#connection");
const state={connected:false,lastEvent:null};
function render(){connection.textContent=state.connected?"Addon connection: connected":"Addon connection: waiting for a real WoW event.";}
window.CasinoBae={receiveAddonEvent(event){state.connected=true;state.lastEvent=event;render();}};
render();