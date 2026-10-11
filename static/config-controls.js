'use strict';
let editToken='', configAccess={configured:false,unlocked:false}, accessReady=false;
const configElement=id=>document.getElementById(id);
function paintConfigLock(){
  configElement('config-lock-button').textContent=!accessReady?'Checking lock…':!configAccess.configured?'Set editing PIN':configAccess.unlocked?'Lock editing':'Unlock editing';
  configElement('config-lock-button').disabled=!accessReady;
  configElement('config-lock-status').textContent=!accessReady?'Checking editing access':!configAccess.configured?'No editing PIN set':configAccess.unlocked?'Editing unlocked':'Editing locked · Monitoring is live';
  document.body.classList.toggle('config-locked',!configAccess.unlocked);
  for(const id of ['protocol','sacn-interface','save-interface','scripts-enabled'])configElement(id).disabled=!configAccess.unlocked;
}
async function lockRequest(path='',body){
  const response=await fetch('/api/config-lock'+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Signal-Edit':editToken},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(5000)});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Could not update the editing lock.');return data;
}
function openUnlock(){
  const setup=!configAccess.configured;
  configElement('pin-title').textContent=setup?'Set editing PIN':'Unlock configuration';
  configElement('pin-confirm-label').hidden=!setup;configElement('pin-confirm').required=setup;
  configElement('pin-form').reset();configElement('pin-error').textContent='';
  configElement('pin-submit').textContent=setup?'Set PIN':'Unlock editing';
  configElement('pin-dialog').showModal();configElement('editing-pin').focus();
}
function requireConfigEdit(){
  if(accessReady&&configAccess.unlocked)return true;
  if(accessReady)openUnlock();
  return false;
}
function configMutation(path,method,body){
  if(method==='GET'||path.startsWith('/api/config-lock'))return false;
  if(path==='/api/network'||path==='/api/log'||/^\/api\/rules\/[^/]+\/test$/.test(path)||path.startsWith('/api/sequences/'))return false;
  if(path==='/api/input'&&body&&Object.keys(body).every(key=>key==='monitor_universe'))return false;
  if(path==='/api/runtime'&&body&&Object.keys(body).every(key=>key==='armed'))return false;
  return true;
}
configElement('config-lock-button').onclick=async()=>{
  if(!configAccess.configured||!configAccess.unlocked){openUnlock();return;}
  try{configAccess=await lockRequest('/lock',{});editToken='';paintConfigLock();}
  catch(error){configElement('config-lock-status').textContent=error.message;}
};
configElement('pin-cancel').onclick=()=>configElement('pin-dialog').close();
configElement('pin-form').onsubmit=async event=>{
  event.preventDefault();const pin=configElement('editing-pin').value,setup=!configAccess.configured;
  if(setup&&pin!==configElement('pin-confirm').value){configElement('pin-error').textContent='The PINs do not match.';return;}
  configElement('pin-submit').disabled=true;
  try{const data=await lockRequest(setup?'/setup':'/unlock',{pin});editToken=data.token;configAccess=data;paintConfigLock();configElement('pin-dialog').close();}
  catch(error){configElement('pin-error').textContent=error.message;}
  finally{configElement('pin-submit').disabled=false;}
};
configElement('pin-dialog').addEventListener('close',()=>configElement('pin-form').reset());
async function refreshConfigAccess(){
  try{configAccess=await lockRequest();accessReady=true;paintConfigLock();}
  catch{accessReady=false;configAccess.unlocked=false;paintConfigLock();}
  setTimeout(refreshConfigAccess,10000);
}
refreshConfigAccess();
fetch('/api/navigation').then(response=>{
  if(!response.ok)throw Error('Navigation unavailable');
  return response.json();
}).then(data=>{
  if(typeof data.preshow!=='string'||!data.preshow.trim())return;
  const destination=new URL(data.preshow,location.href);
  if(['http:','https:'].includes(destination.protocol))configElement('preshow-tab').href=destination.href;
}).catch(()=>{});
