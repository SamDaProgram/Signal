'use strict';
const fadeConfigs=new WeakMap();
const fadeDefaults=()=>({fade_type:'f',fade_curve:'linear',fade_rate:30,fade_range:{min:0,max:1},fade_input_display:'dmx',fade_keyframes:[{level:0,values:[0]},{level:255,values:[1]}]});
let fadeOwner=null,fadeDraft=null,fadeReadOnly=false,fadeInspect=0,fadeSelected=-1;
const fadeColors=['#c3f779','#85c8ff','#e5b0fa','#eed285','#ff9f80','#80e0cc','#b2baff','#ffffff'];
function createFadePanel(form){
  const panel=el('div',undefined,'sequence-config');panel.dataset.action='fade';
  panel.append(el('p','','fade-config-summary'));
  const button=el('button','Edit fade & keyframes');button.type='button';button.onclick=()=>openFade(form);panel.append(button);
  panel.append(el('p','DMX 0–255 continuously controls numeric OSC arguments. Fades follow input independently of threshold triggers on the same channel. Save the trigger or preset after applying.','preset-note'));
  form.querySelector('.osc-fields').after(panel);setFadeData(form,{});
}
function setFadeData(form,values){
  const data=fadeDefaults();for(const key of Object.keys(data))if(values[key]!==undefined)data[key]=cloneSequence(values[key]);
  if(!values.fade_range)data.fade_range=fadeRange({...data,fade_range:null});
  fadeConfigs.set(form,data);
  form.querySelector('.fade-config-summary').textContent=`${data.fade_keyframes.length} keyframes · ${data.fade_keyframes[0].values.length} OSC value(s) · ${data.fade_range.min}–${data.fade_range.max} · ${data.fade_curve} · up to ${data.fade_rate} updates/s`;
}
function getFadeData(form){return cloneSequence(fadeConfigs.get(form)||fadeDefaults());}
function sampleFade(data,level){
  let left=data.fade_keyframes[0],right=left;
  for(const frame of data.fade_keyframes){right=frame;if(level<=frame.level)break;left=frame;}
  let t=right.level===left.level?0:Math.max(0,Math.min(1,(level-left.level)/(right.level-left.level)));
  if(data.fade_curve==='smooth')t=t*t*(3-2*t);else if(data.fade_curve==='hold')t=t>=1?1:0;
  return left.values.map((a,i)=>{const value=a*(1-t)+right.values[i]*t;return data.fade_type==='i'?Math.floor(value+.5):value;});
}
function fadeRange(data){
  if(data.fade_range)return {...data.fade_range};
  const values=data.fade_keyframes.flatMap(frame=>frame.values);
  return {min:Math.min(0,...values),max:Math.max(1,...values)};
}
function checkFadeRange(min,max){
  if(!Number.isFinite(min)||!Number.isFinite(max)||Math.abs(min)>3.402823466e38||Math.abs(max)>3.402823466e38||min>=max)throw Error('Enter a finite output minimum smaller than the maximum.');
}
function rescaleFade(data,min,max){
  checkFadeRange(min,max);const previous=fadeRange(data),copy=JSON.parse(JSON.stringify(data));
  if(previous.min===min&&previous.max===max)return copy;
  // Normalized and fractional ranges need float output to keep the curve useful.
  if(copy.fade_type==='i'&&(max-min<=1||!Number.isInteger(min)||!Number.isInteger(max)||min<-2147483648||max>2147483647))copy.fade_type='f';
  copy.fade_range={min,max};
  copy.fade_keyframes.forEach(frame=>{frame.values=frame.values.map(value=>{
    const t=(value-previous.min)/(previous.max-previous.min),scaled=Math.max(min,Math.min(max,min*(1-t)+max*t));
    return copy.fade_type==='i'?Math.floor(scaled+.5):scaled;
  });});
  return copy;
}
function fadeInputValue(level,display){return display==='normalized'?level/255:display==='percent'?level/255*100:level;}
function fadeInputLevel(value,display){return Math.max(0,Math.min(255,Math.round(display==='normalized'?value*255:display==='percent'?value*255/100:value)));}
function fadePositionText(level){const mode=fadeDraft.fade_input_display;return mode==='percent'?`${Number((level/255*100).toFixed(1))}%`:mode==='normalized'?String(Number((level/255).toFixed(3))):String(level);}
function insertFadeKeyframe(data,level,component=0,value=null){
  level=Math.max(0,Math.min(255,Math.round(level)));
  const existing=data.fade_keyframes.findIndex(frame=>frame.level===level);
  if(existing>=0)return existing;
  if(data.fade_keyframes.length>=64)throw Error('A fade can contain at most 64 keyframes.');
  const values=sampleFade(data,level);
  if(value!==null){const {min,max}=fadeRange(data),clamped=Math.max(min,Math.min(max,value));values[component]=data.fade_type==='i'?Math.floor(clamped+.5):clamped;}
  data.fade_keyframes.push({level,values});data.fade_keyframes.sort((a,b)=>a.level-b.level);
  return data.fade_keyframes.findIndex(frame=>frame.level===level);
}
function syncFadeScaleControls(){
  const {min,max}=fadeRange(fadeDraft),ranges={'unit':[0,1],'percent':[0,100],'byte':[0,255],'bipolar':[-1,1]};
  $('#fade-scale').value=Object.keys(ranges).find(key=>ranges[key][0]===min&&ranges[key][1]===max)||'custom';
  $('#fade-scale-min').value=min;$('#fade-scale-max').value=max;
  $('#fade-custom-scale').hidden=$('#fade-scale').value!=='custom';
  $('#fade-input-display').value=fadeDraft.fade_input_display;
}
function applyFadeRange(min,max){
  const previous=fadeDraft;try{fadeDraft=rescaleFade(fadeDraft,min,max);validateFadeDraft();}catch(error){fadeDraft=previous;throw error;}
  $('#fade-error').textContent='';$('#fade-type').value=fadeDraft.fade_type;syncFadeScaleControls();renderFadeEditor();
}
function addFadeKeyframe(level,component=0,value=null){
  if(fadeReadOnly)return;
  try{fadeSelected=insertFadeKeyframe(fadeDraft,level,component,value);fadeInspect=fadeDraft.fade_keyframes[fadeSelected].level;$('#fade-follow').checked=false;$('#fade-error').textContent='';renderFadeEditor();}
  catch(error){$('#fade-error').textContent=error.message;}
}
function validateFadeDraft(){
  const frames=fadeDraft.fade_keyframes,{min,max}=fadeRange(fadeDraft);checkFadeRange(min,max);
  if(frames.length<2||frames.length>64)throw Error('Use 2–64 keyframes.');
  if(!Number.isInteger(fadeDraft.fade_rate)||fadeDraft.fade_rate<1||fadeDraft.fade_rate>60)throw Error('Output rate must be 1–60 updates per second.');
  frames.forEach((frame,i)=>{
    if(!Number.isInteger(frame.level)||frame.level<0||frame.level>255||(i&&frame.level<=frames[i-1].level))throw Error('Keyframe levels must be unique, increasing integers from 0 to 255.');
    for(const value of frame.values){
      if(value<min||value>max)throw Error('Values must be within the output range. Change the output range to use other values.');
      if(!Number.isFinite(value)||Math.abs(value)>3.402823466e38)throw Error('Enter finite OSC values.');
      if(fadeDraft.fade_type==='i'&&(!Number.isInteger(value)||value<-2147483648||value>2147483647))throw Error('Integer OSC values must be whole numbers from −2147483648 to 2147483647.');
    }
  });
}
function fadeValueText(value){return Number(value.toPrecision(6)).toString();}
function openFade(form){
  fadeOwner=form;fadeDraft=getFadeData(form);fadeReadOnly=form.id==='form'&&form.elements.action.disabled;fadeInspect=0;fadeSelected=-1;
  $('#fade-error').textContent='';$('#fade-follow').checked=false;
  $('#fade-follow').disabled=form.id!=='form';$('#fade-live-status').textContent=form.id==='form'?'Enable Follow live DMX to inspect this input.':'Live input preview is available in the trigger editor.';
  for(const field of $('#fade-editor').querySelectorAll('[data-edit]'))field.disabled=fadeReadOnly;
  $('#fade-use').hidden=fadeReadOnly;$('#fade-curve').value=fadeDraft.fade_curve;$('#fade-type').value=fadeDraft.fade_type;$('#fade-rate').value=fadeDraft.fade_rate;
  $('#fade-template').value='brightness';syncFadeScaleControls();renderFadeEditor();$('#fade-editor').showModal();
}
function fadeBounds(){return fadeRange(fadeDraft);}
function fadeSvg(tag,attrs){const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value]of Object.entries(attrs))node.setAttribute(key,value);return node;}
function renderFadeGraph(){
  const svg=$('#fade-graph');svg.replaceChildren();const {min,max}=fadeBounds();
  const y=value=>180-(value-min)/(max-min)*150,x=level=>45+level/255*510;
  for(const level of [0,64,128,192,255]){
    svg.append(fadeSvg('line',{x1:x(level),x2:x(level),y1:25,y2:180,class:'fade-grid'}));
    const label=fadeSvg('text',{x:x(level),y:200,'text-anchor':'middle'});label.textContent=fadePositionText(level);svg.append(label);
  }
  for(const value of [min,(min+max)/2,max]){
    const label=fadeSvg('text',{x:40,y:y(value)+4,'text-anchor':'end'});label.textContent=fadeValueText(value);svg.append(label);
  }
  const width=fadeDraft.fade_keyframes[0].values.length;
  const colors=width===3?['#ff8383','#a8ef81','#85bcff']:fadeColors;
  const markers=[];
  for(let component=0;component<width;component++){
    const points=[];for(let level=0;level<=255;level++)points.push(`${x(level)},${y(sampleFade(fadeDraft,level)[component])}`);
    svg.append(fadeSvg('polyline',{points:points.join(' '),fill:'none',stroke:colors[component],'stroke-width':3,'data-component':component,class:'fade-curve'}));
    fadeDraft.fade_keyframes.forEach((frame,index)=>{
      const point=fadeSvg('circle',{cx:x(frame.level),cy:y(frame.values[component]),r:6,fill:colors[component],class:'fade-point'+(index===fadeSelected?' selected':''),'data-frame':index,'data-component':component});
      if(!fadeReadOnly){point.setAttribute('tabindex','0');point.setAttribute('role','slider');point.setAttribute('aria-label',`Keyframe ${index+1}, value ${component+1}, DMX ${frame.level}`);point.setAttribute('aria-valuenow',frame.values[component]);point.setAttribute('aria-valuemin',min);point.setAttribute('aria-valuemax',max);}
      markers.push(point);
    });
  }
  svg.append(...markers);
  svg.append(fadeSvg('line',{id:'fade-playhead',x1:x(fadeInspect),x2:x(fadeInspect),y1:20,y2:185,stroke:'#fff','stroke-width':1,'pointer-events':'none'}));
}
function fadeOscPreview(data,level,destination){
  const args=sampleFade(data,level).map(value=>({type:data.fade_type,value:data.fade_type==='f'?Math.fround(value):value}));
  const tags=','+args.map(arg=>arg.type).join('');
  // Nine significant digits uniquely identify a float32 value in readable form.
  const values=args.map(arg=>arg.type==='f'?String(Number(arg.value.toPrecision(9))):String(arg.value));
  return {message:`${destination.address||'<OSC address>'} ${tags} ${values.join(' ')}`,
    destination:`UDP → ${destination.host||'<destination>'}:${destination.port||'<port>'}`,
    json:JSON.stringify({...destination,args},null,2)};
}
function paintFadePreview(){
  $('#fade-inspect').value=fadeInspect;$('#fade-inspect').setAttribute('aria-valuetext',fadePositionText(fadeInspect));const values=sampleFade(fadeDraft,fadeInspect);
  $('#fade-preview-values').textContent=`${fadeDraft.fade_input_display==='dmx'?'DMX '+fadeInspect:fadePositionText(fadeInspect)+' (DMX '+fadeInspect+')'} → [${values.map(fadeValueText).join(', ')}] · ${fadeDraft.fade_type==='f'?'float':'integer'} OSC arguments · Preview sends no outputs`;
  const preview=fadeOscPreview(fadeDraft,fadeInspect,{host:fadeOwner.elements.host.value.trim(),port:Number(fadeOwner.elements.port.value),address:fadeOwner.elements.address.value.trim()});
  $('#fade-osc-destination').textContent=preview.destination;$('#fade-osc-message').textContent=preview.message;$('#fade-osc-json').textContent=preview.json;
  $('#fade-copy-status').textContent='';
  const swatch=$('#fade-swatch');swatch.hidden=values.length!==3;
  if(values.length===3){const {min,max}=fadeRange(fadeDraft),divisor=max-min;swatch.style.background=`rgb(${values.map(value=>Math.round(Math.max(0,Math.min(1,(value-min)/divisor))*255)).join(',')})`;}
  const line=$('#fade-playhead');if(line)for(const key of ['x1','x2'])line.setAttribute(key,45+fadeInspect/255*510);
}
function renderFadeEditor(){
  const list=$('#fade-keyframes');list.replaceChildren();
  fadeDraft.fade_keyframes.forEach((frame,index)=>{
    const row=el('div',undefined,'fade-keyframe'+(index===fadeSelected?' selected':''));
    function number(label,value,change,min,max){
      const wrapper=el('label',label),input=el('input');input.type='number';input.value=value;input.step='any';input.required=true;input.disabled=fadeReadOnly;
      if(min!==undefined)input.min=min;if(max!==undefined)input.max=max;
      input.onchange=()=>{const saved=cloneSequence(fadeDraft);try{if(input.value==='')throw Error('Enter a number.');change(Number(input.value));validateFadeDraft();$('#fade-error').textContent='';renderFadeGraph();paintFadePreview();}catch(error){fadeDraft=saved;$('#fade-error').textContent=error.message;renderFadeEditor();}};
      wrapper.append(input);row.append(wrapper);return input;
    }
    const mode=fadeDraft.fade_input_display,inputMax=mode==='normalized'?1:mode==='percent'?100:255;
    const position=number(`Position ${index+1}${mode==='percent'?' (%)':mode==='dmx'?' (DMX)':' (0–1)'}`,Number(fadeInputValue(frame.level,mode).toPrecision(8)),value=>{if(value<0||value>inputMax)throw Error(`Position must be between 0 and ${inputMax}.`);frame.level=fadeInputLevel(value,mode);},0,inputMax);
    position.step=mode==='dmx'?'1':'any';position.addEventListener('change',()=>{position.value=Number(fadeInputValue(frame.level,mode).toPrecision(8));});
    frame.values.forEach((value,component)=>number(frame.values.length===3?['R','G','B'][component]:`Value ${component+1}`,value,next=>{frame.values[component]=next;}));
    const remove=el('button','×','small');remove.type='button';remove.setAttribute('aria-label',`Remove keyframe ${index+1}`);remove.disabled=fadeReadOnly||fadeDraft.fade_keyframes.length<=2;
    remove.onclick=()=>{fadeDraft.fade_keyframes.splice(index,1);fadeSelected=-1;renderFadeEditor();};row.append(remove);list.append(row);
  });
  $('#fade-add').disabled=fadeReadOnly||fadeDraft.fade_keyframes.length>=64;
  const component=$('#fade-component'),selected=component.value;component.replaceChildren();fadeDraft.fade_keyframes[0].values.forEach((_,i)=>{const option=el('option',fadeDraft.fade_keyframes[0].values.length===3?['Red','Green','Blue'][i]:`Value ${i+1}`);option.value=i;component.append(option);});component.value=selected!==''&&Number(selected)<component.options.length?selected:'0';
  $('#fade-component-label').hidden=component.options.length===1;
  renderFadeGraph();paintFadePreview();
}
function liveFadeInput(form,data){
  const universe=Number(form.elements.universe?.value),channel=Number(form.elements.channel?.value);
  const rule=data.rules.find(rule=>rule.universe===universe&&rule.channel===channel);
  let level=rule?.level,live=rule?.live;
  if(universe===selectedUniverse){level=data.levels[channel-1];live=data.live_slots[channel-1];}
  return {universe,channel,level,live};
}
function renderFadeLive(data){
  if(!fadeOwner||!$('#fade-editor').open||!$('#fade-follow').checked)return;
  const {universe,channel,level,live}=liveFadeInput(fadeOwner,data);
  $('#fade-live-status').textContent=live?`Live DMX · Universe / block ${universe} · CH ${channel} · ${data.armed?'Outputs enabled':'Outputs paused'}`:'No live level available. Select this universe in Channel monitor and send DMX.';
  if(live&&level!=null){fadeInspect=level;paintFadePreview();}
}
function initializeFadeUI(){
  const dialog=el('dialog');dialog.id='fade-editor';dialog.setAttribute('aria-labelledby','fade-title');
  dialog.innerHTML=`<div class="dialog-header"><div><div class="eyebrow">DMX → OSC FADER</div><h2 id="fade-title">Fade keyframes</h2></div><button type="button" id="fade-close" class="icon-button" aria-label="Close fade editor">×</button></div>
  <p>Click the graph to add a keyframe at that level and value. Click the bottom ruler to insert one on the current curve. Click an existing point to select it, or drag it to adjust its position and value. Left/Right keys change the level; Up/Down change the value.</p>
  <div class="sequence-view-controls"><label>Template<select id="fade-template" data-edit><option value="brightness">Brightness 0–1</option><option value="rgb">RGB color sweep</option><option value="pan">Bipolar −1 to 1</option></select></label><button type="button" id="fade-load-template" data-edit>Replace with template</button></div>
  <div class="sequence-view-controls"><label>Interpolation<select id="fade-curve" data-edit><option value="linear">Linear</option><option value="smooth">Smooth</option><option value="hold">Hold / stepped</option></select></label><label>OSC type<select id="fade-type" data-edit><option value="f">Float</option><option value="i">Integer</option></select></label><label>Max updates/s<input id="fade-rate" data-edit type="number" min="1" max="60" value="30" required></label></div>
  <div class="sequence-view-controls"><label>Input level display<select id="fade-input-display" data-edit><option value="dmx">DMX 0–255</option><option value="normalized">Normalized 0–1</option><option value="percent">Percent 0–100%</option></select></label><label>Output range<select id="fade-scale" data-edit><option value="unit">0–1 normalized</option><option value="percent">0–100 percent</option><option value="byte">0–255</option><option value="bipolar">−1 to 1</option><option value="custom">Custom</option></select></label><label id="fade-component-label">Click edits<select id="fade-component" data-edit></select></label></div>
  <div id="fade-custom-scale" class="sequence-view-controls" hidden><label>Minimum<input id="fade-scale-min" type="number" step="any" data-edit></label><label>Maximum<input id="fade-scale-max" type="number" step="any" data-edit></label><button type="button" id="fade-apply-scale" data-edit>Apply range</button></div>
  <p class="preset-note">Input display changes labels only; DMX still has 256 positions. Output range rescales every keyframe and changes the OSC values after saving. Normalized ranges use float output.</p>
  <svg id="fade-graph" viewBox="0 0 600 215" aria-label="DMX keyframe curves"></svg>
  <label class="sequence-scrub-label">Preview DMX<input id="fade-inspect" type="range" min="0" max="255" step="1" value="0"></label>
  <div class="fade-preview"><span id="fade-swatch" aria-label="RGB color preview"></span><output id="fade-preview-values"></output></div>
  <section class="fade-osc-preview" aria-labelledby="fade-osc-title"><div class="fade-osc-heading"><h3 id="fade-osc-title">OSC message preview</h3><button type="button" id="fade-copy-message" class="small">Copy message</button></div><p id="fade-osc-destination"></p><pre id="fade-osc-message" tabindex="0" aria-label="OSC message"></pre><p class="preset-note">f = 32-bit float · i = 32-bit integer. Shows the current draft at the preview level; nothing is sent.</p><details><summary>Typed JSON</summary><pre id="fade-osc-json"></pre></details><span id="fade-copy-status" role="status"></span></section>
  <label class="check"><input type="checkbox" id="fade-follow">Follow live DMX</label><p id="fade-live-status" class="preset-note"></p>
  <div id="fade-keyframes"></div><button type="button" id="fade-add" data-edit>＋ Add keyframe at preview level</button>
  <p class="preset-note">Values outside your first/last keyframe hold at that endpoint. RGB uses three OSC arguments; the color preview uses the chosen output range. Destination and OSC address are set in the parent editor.</p>
  <p id="fade-error" role="alert"></p><div class="dialog-footer"><button type="button" id="fade-discard">Close without applying</button><button type="button" id="fade-use" class="primary">Use fade</button></div>`;
  document.body.append(dialog);
  $('#fade-copy-message').onclick=async()=>{try{await navigator.clipboard.writeText($('#fade-osc-destination').textContent+'\n'+$('#fade-osc-message').textContent);$('#fade-copy-status').textContent='Copied';}catch{$('#fade-copy-status').textContent='Select the message and copy it manually.';}};
  $('#fade-close').onclick=$('#fade-discard').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{fadeOwner=null;});
  $('#fade-use').onclick=()=>{try{if($('#fade-scale').value==='custom')applyFadeRange(readScaleNumber('#fade-scale-min'),readScaleNumber('#fade-scale-max'));validateFadeDraft();setFadeData(fadeOwner,fadeDraft);dialog.close();}catch(error){$('#fade-error').textContent=error.message;}};
  $('#fade-load-template').onclick=()=>{
    const kind=$('#fade-template').value;fadeDraft=fadeDefaults();
    if(kind==='pan'){fadeDraft.fade_range={min:-1,max:1};fadeDraft.fade_keyframes=[{level:0,values:[-1]},{level:128,values:[0]},{level:255,values:[1]}];}
    if(kind==='rgb')fadeDraft.fade_keyframes=[{level:0,values:[1,0,0]},{level:128,values:[0,1,0]},{level:255,values:[0,0,1]}];
    $('#fade-type').value='f';$('#fade-curve').value='linear';$('#fade-rate').value=30;$('#fade-error').textContent='';fadeSelected=-1;syncFadeScaleControls();renderFadeEditor();
  };
  $('#fade-curve').onchange=()=>{fadeDraft.fade_curve=$('#fade-curve').value;renderFadeGraph();paintFadePreview();};
  $('#fade-type').onchange=()=>{const previous=fadeDraft.fade_type;fadeDraft.fade_type=$('#fade-type').value;try{validateFadeDraft();$('#fade-error').textContent='';renderFadeEditor();}catch(error){fadeDraft.fade_type=previous;$('#fade-type').value=previous;$('#fade-error').textContent=error.message;}};
  $('#fade-rate').oninput=()=>{fadeDraft.fade_rate=Number($('#fade-rate').value);};
  $('#fade-inspect').oninput=()=>{$('#fade-follow').checked=false;fadeInspect=Number($('#fade-inspect').value);paintFadePreview();};
  $('#fade-follow').onchange=()=>{if(state)renderFadeLive(state);};
  $('#fade-add').onclick=()=>addFadeKeyframe(fadeInspect);
  $('#fade-input-display').onchange=()=>{fadeDraft.fade_input_display=$('#fade-input-display').value;renderFadeEditor();};
  function readScaleNumber(selector){if($(selector).value==='')throw Error('Enter both output range limits.');return Number($(selector).value);}
  $('#fade-scale').onchange=()=>{
    const choice=$('#fade-scale').value;$('#fade-custom-scale').hidden=choice!=='custom';
    if(choice==='custom'){const {min,max}=fadeRange(fadeDraft);$('#fade-scale-min').value=min;$('#fade-scale-max').value=max;return;}
    const ranges={unit:[0,1],percent:[0,100],byte:[0,255],bipolar:[-1,1]};
    try{applyFadeRange(...ranges[choice]);}catch(error){$('#fade-error').textContent=error.message;syncFadeScaleControls();}
  };
  $('#fade-apply-scale').onclick=()=>{try{applyFadeRange(readScaleNumber('#fade-scale-min'),readScaleNumber('#fade-scale-max'));}catch(error){$('#fade-error').textContent=error.message;}};
  const svg=$('#fade-graph');let drag=null;
  function localPoint(event){const point=svg.createSVGPoint();point.x=event.clientX;point.y=event.clientY;return point.matrixTransform(svg.getScreenCTM().inverse());}
  function move(event){
    if(!drag||event.pointerId!==drag.id)return;
    if(!drag.moved&&Math.hypot(event.clientX-drag.startX,event.clientY-drag.startY)<4)return;
    drag.moved=true;if(drag.insert)return;
    const local=localPoint(event),frame=fadeDraft.fade_keyframes[drag.index];
    frame.level=Math.max(drag.low,Math.min(drag.high,Math.round((local.x-45)/510*255)));
    const raw=drag.max-(Math.max(30,Math.min(180,local.y))-30)/150*(drag.max-drag.min);
    frame.values[drag.component]=fadeDraft.fade_type==='i'?Math.max(-2147483648,Math.min(2147483647,Math.round(raw))):Math.max(drag.min,Math.min(drag.max,Number(raw.toPrecision(6))));
    renderFadeGraph();paintFadePreview();
  }
  svg.addEventListener('pointerdown',event=>{
    if(fadeReadOnly||event.button!==0||drag)return;
    const point=event.target.closest('.fade-point'),local=localPoint(event);
    if(!point&&(local.x<45||local.x>555||local.y<25||local.y>210))return;
    event.preventDefault();$('#fade-follow').checked=false;
    const component=Number(event.target.closest('[data-component]')?.dataset.component??$('#fade-component').value),bounds=fadeBounds();
    drag={id:event.pointerId,component,...bounds,startX:event.clientX,startY:event.clientY,moved:false,insert:!point,saved:cloneSequence(fadeDraft)};
    if(point){const index=Number(point.dataset.frame);fadeSelected=index;Object.assign(drag,{index,low:index?fadeDraft.fade_keyframes[index-1].level+1:0,high:index<fadeDraft.fade_keyframes.length-1?fadeDraft.fade_keyframes[index+1].level-1:255});}
    $('#fade-component').value=component;svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener('pointermove',move);
  function finish(event){
    if(!drag||event.pointerId!==drag.id)return;
    if(event.type==='pointerup')move(event);const ended=drag;drag=null;
    if(event.type!=='pointerup')fadeDraft=ended.saved;
    if(svg.hasPointerCapture(ended.id))svg.releasePointerCapture(ended.id);
    if(ended.insert&&event.type==='pointerup'&&!ended.moved){const local=localPoint(event),level=Math.round((local.x-45)/510*255),value=local.y>180?null:ended.max-(Math.max(30,Math.min(180,local.y))-30)/150*(ended.max-ended.min);addFadeKeyframe(level,ended.component,value);}
    else{if(!ended.insert)fadeInspect=fadeDraft.fade_keyframes[ended.index].level;renderFadeEditor();}
  }
  svg.addEventListener('pointerup',finish);svg.addEventListener('pointercancel',finish);svg.addEventListener('lostpointercapture',finish);
  svg.addEventListener('keydown',event=>{
    const point=event.target.closest('.fade-point');if(!point||fadeReadOnly||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
    event.preventDefault();const index=Number(point.dataset.frame),component=Number(point.dataset.component),frame=fadeDraft.fade_keyframes[index],saved=cloneSequence(fadeDraft);
    if(event.key==='ArrowLeft')frame.level=Math.max(index?fadeDraft.fade_keyframes[index-1].level+1:0,frame.level-1);
    if(event.key==='ArrowRight')frame.level=Math.min(index<fadeDraft.fade_keyframes.length-1?fadeDraft.fade_keyframes[index+1].level-1:255,frame.level+1);
    if(event.key==='ArrowUp'||event.key==='ArrowDown'){const {min,max}=fadeBounds(),increment=fadeDraft.fade_type==='i'?1:(max-min)/(event.shiftKey?1000:100);frame.values[component]=Number((frame.values[component]+(event.key==='ArrowUp'?increment:-increment)).toPrecision(6));}
    try{validateFadeDraft();$('#fade-error').textContent='';}catch(error){fadeDraft=saved;$('#fade-error').textContent=error.message;}
    renderFadeEditor();svg.querySelector(`[data-frame="${index}"][data-component="${component}"]`).focus({preventScroll:true});
  });
}
