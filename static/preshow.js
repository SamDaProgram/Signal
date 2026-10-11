'use strict';
const $=selector=>document.querySelector(selector);
let busy=false, revision=0, actionError='', currentPreshow=null;
function render(data){
  if(typeof data.preshow!=='boolean')throw Error('The server returned an invalid status.');
  const preshow=data.preshow;
  currentPreshow=preshow;
  $('#status').textContent=preshow?'In preshow':'Running show';
  $('#status-dot').dataset.mode=preshow?'preshow':'show';
  $('#connection').textContent='● Connected';
  $('#show-action').dataset.action=preshow?'show':'preshow';
  $('#show-action').disabled=busy;
  $('#show-action').setAttribute('aria-busy',String(busy));
  $('#action-title').textContent=preshow?'Start show':'Start preshow';
  $('#action-icon').textContent=preshow?'▶':'◐';
  $('#action-description').textContent=preshow?'Switch to the running show.':'Start the preshow cue in Signal.';
  $('#error').textContent=actionError;
  $('#error').hidden=!actionError;
}
function offline(error){
  $('#connection').textContent='○ Connection lost';
  $('#status').textContent='Status unavailable';
  currentPreshow=null;
  $('#action-title').textContent='Connecting…';
  $('#action-description').textContent='Waiting for the show controller.';
  $('#show-action').setAttribute('aria-busy','false');
  $('#status-dot').removeAttribute('data-mode');
  for(const button of document.querySelectorAll('button')){button.disabled=true;button.removeAttribute('aria-pressed');}
  $('#error').textContent=error.message;
  $('#error').hidden=false;
}
async function request(options={},path='/api/status'){
  const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(8000),...options});
  const data=await response.json();
  if(!response.ok)throw Error(data.error||'Could not update show status.');
  return data;
}
async function poll(){
  const current=revision;
  if(!busy)try{const data=await request();if(current===revision&&!busy)render(data);}catch(error){if(current===revision&&!busy)offline(error);}
  setTimeout(poll,1000);
}
async function setPreshow(preshow){
  if(busy)return;
  busy=true;revision++;actionError='';
  for(const button of document.querySelectorAll('button'))button.disabled=true;
  $('#connection').textContent='Updating…';
  $('#show-action').setAttribute('aria-busy','true');
  $('#action-title').textContent=preshow?'Starting preshow…':'Starting show…';
  try{const data=await request({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(preshow?{}:{preshow:false})},preshow?'/api/start-preshow':'/api/status');busy=false;render(data);}
  catch(error){busy=false;actionError=error.message;try{render(await request());}catch(connectionError){offline(connectionError);}}
}
$('#show-action').onclick=()=>{if(currentPreshow!==null)setPreshow(!currentPreshow);};
poll();

fetch('/api/navigation').then(response=>{
  if(!response.ok)throw Error('Navigation unavailable');
  return response.json();
}).then(data=>{
  if(typeof data.config!=='string'||!data.config.trim())return;
  const destination=new URL(data.config,location.href);
  if(['http:','https:'].includes(destination.protocol))$('#config-tab').href=destination.href;
}).catch(()=>{});
