'use strict';
const sequenceConfigs=new WeakMap();
let sequenceOwner=null, sequenceDraft=[], sequenceSelected=-1, sequenceReadOnly=false, previewFrame=null, previewStarted=0, previewTime=0, previewOrigin=0, previewPlaying=false;
const cloneSequence=value=>JSON.parse(JSON.stringify(value));
const formatSequenceTime=ms=>ms<1000?`${ms} ms`:`${(ms/1000).toFixed(ms%1000?2:0)} s`;
const stepDefault=number=>({name:`Step ${number}`,action:'osc',delay_ms:number===1?0:500,host:'127.0.0.1',port:8000,address:'/go',args:[]});
function createSequencePanel(form){
  const panel=el('div',undefined,'sequence-config');panel.dataset.action='sequence';
  panel.append(el('p','No steps configured.','sequence-config-summary'));
  const button=el('button','Edit sequence & timeline');button.type='button';button.onclick=()=>openSequence(form);panel.append(button);
  panel.append(el('p','Up to 64 timed OSC, MIDI, and script steps. Save the trigger or preset after applying your sequence.','preset-note'));
  form.querySelector('.osc-fields').after(panel);
  setSequenceData(form,{});
}
function setSequenceData(form,values){
  const data={steps:cloneSequence(values.steps||[]),sequence_on_error:values.sequence_on_error||'stop',sequence_source:values.sequence_source||'time',sequence_direction:values.sequence_direction||'forward'};
  sequenceConfigs.set(form,data);
  form.querySelector('.sequence-config-summary').textContent=data.steps.length?`${data.steps.length} steps · ${data.sequence_source==='dmx'?'DMX driven · ':''}${formatSequenceTime(data.steps.reduce((sum,s)=>sum+s.delay_ms,0))} timeline · ${data.sequence_on_error==='stop'?'Stop':'Continue'} on error`:'No steps configured.';
}
function getSequenceData(form){return cloneSequence(sequenceConfigs.get(form)||{steps:[],sequence_on_error:'stop'});}
function sequenceSchedule(steps){let at=0;return steps.map(step=>({...step,at_ms:at+=step.delay_ms,status:'pending'}));}
function drawSequenceTimeline(target,steps,onSelect=null,selected=-1){
  target.replaceChildren();target.dataset.duration=1000;
  if(!steps.length){target.append(el('p','Add a step to build the timeline.'));return;}
  const dmx=target.id==='sequence-timeline'&&$('#sequence-source').value==='dmx';
  const duration=Math.max(dmx?1:1000,...steps.map(step=>step.at_ms));
  target.dataset.duration=duration;
  const canvas=el('div',undefined,'timeline-canvas'),axis=el('div',undefined,'timeline-axis');
  axis.append(el('span',dmx?'DMX INPUT':'SCHEDULED START'));
  const ticks=el('div',undefined,'timeline-ticks');
  for(let i=0;i<=4;i++){const tick=el('span',dmx?String(Math.round(255*i/4)):formatSequenceTime(Math.round(duration*i/4)));tick.style.left=`${i*25}%`;ticks.append(tick);}
  axis.append(ticks);canvas.append(axis);
  let previous=0;
  steps.forEach((step,index)=>{
    const row=el('div',undefined,`timeline-row ${step.action} ${step.status||'pending'}`);
    if(onSelect){row.onclick=()=>onSelect(index);row.setAttribute('aria-label',`Edit step ${index+1}: ${step.name}, at ${formatSequenceTime(step.at_ms)}`);row.dataset.selected=String(index===selected);}
    const label=el(onSelect?'button':'span',undefined,'timeline-label');if(onSelect){label.type='button';label.setAttribute('aria-pressed',String(index===selected));label.setAttribute('aria-label',`Edit step ${index+1}: ${step.name}`);}label.append(el('strong',`${index+1}. ${step.name}`),el('small',`${step.action.toUpperCase()} · ${formatSequenceTime(step.at_ms)}${dmx?' · DMX '+Math.ceil(step.at_ms/duration*255):''}${step.status&&step.status!=='pending'?' · '+step.status:''}`));
    const track=el('span',undefined,'timeline-track'),delay=el('span',undefined,'timeline-delay'),marker=el('span',String(index+1),'timeline-marker'),playhead=el('span',undefined,'timeline-playhead');
    delay.style.left=`${previous/duration*100}%`;delay.style.width=`${(step.at_ms-previous)/duration*100}%`;marker.style.left=`${step.at_ms/duration*100}%`;
    if(onSelect){
      row.dataset.index=index;
      if(!sequenceReadOnly){marker.tabIndex=0;marker.setAttribute('role','slider');marker.setAttribute('aria-label',`Scheduled start for ${step.name}`);marker.setAttribute('aria-valuemin',previous);marker.setAttribute('aria-valuemax',600000-(steps.at(-1).at_ms-step.at_ms));marker.setAttribute('aria-valuenow',step.at_ms);marker.setAttribute('aria-valuetext',formatSequenceTime(step.at_ms));}
    }
    track.append(delay,marker,playhead);row.append(label,track);row.title=step.detail||`${step.name}: ${formatSequenceTime(step.at_ms)} after sequence starts`;canvas.append(row);previous=step.at_ms;
  });
  target.append(canvas);
}
function sequenceDuration(){return Math.max($('#sequence-source').value==='dmx'?1:1000,sequenceDraft.reduce((sum,step)=>sum+step.delay_ms,0));}
function stopSequencePreview(reset=true){
  if(previewFrame!==null)cancelAnimationFrame(previewFrame);previewFrame=null;previewPlaying=false;
  $('#preview-sequence').disabled=!sequenceDraft.length;$('#preview-sequence').textContent=reset?'▶ Preview timing':'▶ Resume preview';$('#stop-preview').hidden=reset;
  if(reset){previewTime=0;$('#sequence-preview-status').textContent='Preview animates timing only. No outputs are sent.';paintSequencePreview(0,false);}
}
function paintSequencePreview(elapsed,highlight=true){
  const target=$('#sequence-timeline'),scale=Number(target.dataset.duration)||1000;
  previewTime=Math.max(0,Math.min(scale,elapsed));
  target.style.setProperty('--playhead',`${previewTime/scale*100}%`);
  const schedule=sequenceSchedule(sequenceDraft);
  target.querySelectorAll('.timeline-row').forEach((row,i)=>row.classList.toggle('preview-passed',highlight&&schedule[i].at_ms<=previewTime));
  $('#sequence-scrub').value=Math.round(previewTime);
  $('#sequence-scrub').setAttribute('aria-valuetext',formatSequenceTime(Math.round(previewTime)));
}
function seekSequencePreview(time){
  $('#sequence-follow').checked=false;
  stopSequencePreview(false);paintSequencePreview(time);
  $('#sequence-preview-status').textContent=`Inspecting ${formatSequenceTime(Math.round(previewTime))} / ${formatSequenceTime(sequenceDuration())} · No outputs sent`;
}
function startSequencePreview(){
  $('#sequence-follow').checked=false;
  if(previewPlaying){stopSequencePreview(false);$('#sequence-preview-status').textContent=`Paused at ${formatSequenceTime(Math.round(previewTime))} · No outputs sent`;return;}
  if(!commitSequenceStep()||!sequenceDraft.length)return;
  renderSequenceDraft();
  if(previewTime>=sequenceDuration())previewTime=0;
  previewOrigin=previewTime;previewStarted=performance.now();previewPlaying=true;
  $('#preview-sequence').textContent='Ⅱ Pause preview';$('#stop-preview').hidden=false;
  function frame(now){
    const duration=sequenceDuration(),elapsed=Math.min(duration,previewOrigin+(now-previewStarted)*Number($('#sequence-speed').value));
    paintSequencePreview(elapsed);$('#sequence-preview-status').textContent=`Preview · ${formatSequenceTime(Math.round(elapsed))} / ${formatSequenceTime(duration)} · No outputs sent`;
    if(elapsed<duration)previewFrame=requestAnimationFrame(frame);
    else{stopSequencePreview(false);$('#preview-sequence').textContent='↻ Replay preview';$('#sequence-preview-status').textContent='Preview complete. No outputs were sent.';}
  }
  previewFrame=requestAnimationFrame(frame);
}
// Delays are relative: moving a marker preserves all later inter-step gaps.
function sequenceDelayAt(steps,index,time,snap=1){
  const before=steps.slice(0,index).reduce((sum,step)=>sum+step.delay_ms,0);
  const after=steps.slice(index+1).reduce((sum,step)=>sum+step.delay_ms,0);
  return Math.max(0,Math.min(600000-before-after,Math.round(time/snap)*snap-before));
}
function setSequenceMarkerTime(index,time,snap){
  sequenceDraft[index].delay_ms=sequenceDelayAt(sequenceDraft,index,time,snap);
  if(sequenceSelected===index)$('#step-form').elements.delay_ms.value=sequenceDraft[index].delay_ms;
}
function zoomSequenceTimeline(){
  const canvas=$('#sequence-timeline .timeline-canvas');
  if(canvas)canvas.style.width=`${Number($('#sequence-zoom').value)*100}%`;
}
function initializeTimelineDrag(){
  const target=$('#sequence-timeline');let drag=null,suppressClick=false;
  target.addEventListener('click',event=>{if(suppressClick||event.target.closest('.timeline-track,.timeline-ticks')){event.stopPropagation();suppressClick=false;}},true);
  target.addEventListener('pointerdown',event=>{
    if(event.button!==0||drag)return;
    const track=event.target.closest('.timeline-track,.timeline-ticks');if(!track)return;
    const marker=event.target.closest('.timeline-marker');
    if(marker&&sequenceReadOnly)return;
    if(!commitSequenceStep())return;
    $('#sequence-follow').checked=false;stopSequencePreview(false);
    const index=marker?Number(marker.closest('.timeline-row').dataset.index):-1;
    const rect=track.getBoundingClientRect(),scale=Number(target.dataset.duration)||1000;
    const schedule=sequenceSchedule(sequenceDraft);
    const pointerTime=(event.clientX-rect.left)/rect.width*scale;
    drag={id:event.pointerId,index,rect,scale,offset:marker?schedule[index].at_ms-pointerTime:0,original:cloneSequence(sequenceDraft),startX:event.clientX,moved:false};
    target.setPointerCapture(event.pointerId);event.preventDefault();suppressClick=true;
    if(marker)marker.focus({preventScroll:true});
    move(event);
  });
  function move(event){
    if(!drag||event.pointerId!==drag.id)return;
    const time=(event.clientX-drag.rect.left)/drag.rect.width*drag.scale+drag.offset;
    if(drag.index<0){seekSequencePreview(time);return;}
    if(!drag.moved&&Math.abs(event.clientX-drag.startX)<2)return;
    drag.moved=true;
    setSequenceMarkerTime(drag.index,time,event.shiftKey?1:Number($('#sequence-snap').value));
    const schedule=sequenceSchedule(sequenceDraft);let previous=0;
    target.querySelectorAll('.timeline-row').forEach((row,i)=>{
      const step=schedule[i],marker=row.querySelector('.timeline-marker'),delay=row.querySelector('.timeline-delay');
      marker.style.left=`${step.at_ms/drag.scale*100}%`;marker.setAttribute('aria-valuenow',step.at_ms);marker.setAttribute('aria-valuetext',formatSequenceTime(step.at_ms));
      delay.style.left=`${previous/drag.scale*100}%`;delay.style.width=`${step.delay_ms/drag.scale*100}%`;
      row.querySelector('.timeline-label small').textContent=`${step.action.toUpperCase()} · ${formatSequenceTime(step.at_ms)}`;previous=step.at_ms;
    });
    $('#sequence-summary').textContent=`${sequenceDraft.length} / 64 steps · ${formatSequenceTime(previous)}`;
    $('#sequence-preview-status').textContent=`${sequenceDraft[drag.index].name}: ${formatSequenceTime(schedule[drag.index].at_ms)} · Later steps shift with this step`;
  }
  function finish(event){
    if(!drag||event.pointerId!==drag.id)return;
    if(event.type==='pointerup')move(event);
    const ended=drag;drag=null;
    if(event.type==='pointercancel')sequenceDraft=ended.original;
    if(target.hasPointerCapture(ended.id))target.releasePointerCapture(ended.id);
    if(ended.index>=0){selectSequenceStep(ended.index,false);$('#sequence-timeline').querySelectorAll('.timeline-marker')[ended.index]?.focus({preventScroll:true});}
    // The synthesized click follows pointerup before this timer.
    setTimeout(()=>{suppressClick=false;},0);
  }
  target.addEventListener('pointermove',move);target.addEventListener('pointerup',finish);target.addEventListener('pointercancel',finish);target.addEventListener('lostpointercapture',finish);
  target.addEventListener('keydown',event=>{
    const marker=event.target.closest('.timeline-marker');
    if(!marker||sequenceReadOnly||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();event.stopPropagation();if(!commitSequenceStep())return;
    const index=Number(marker.closest('.timeline-row').dataset.index),at=sequenceSchedule(sequenceDraft)[index].at_ms;
    const increment=event.shiftKey?1:Number($('#sequence-snap').value);
    setSequenceMarkerTime(index,event.key==='Home'?0:event.key==='End'?600000:at+(event.key==='ArrowLeft'?-increment:increment),1);
    selectSequenceStep(index,false);target.querySelectorAll('.timeline-marker')[index].focus();
  });
}
function readSequenceStep(){
  const form=$('#step-form');
  if(!form.reportValidity())throw Error('Complete the step fields before continuing.');
  const name=form.elements.name.value.trim(),delay_ms=Number(form.elements.delay_ms.value),data={name,delay_ms,...readAction(form)};
  if(!name)throw Error('Enter a step name.');
  if(!Number.isInteger(delay_ms)||delay_ms<0||delay_ms>600000)throw Error('Delay must be an integer from 0 to 600000 ms.');
  if(data.action==='osc')data.args=readArguments($('#step-arguments'));
  return data;
}
function commitSequenceStep(){
  if(sequenceSelected<0||sequenceReadOnly)return true;
  try{
    const step=readSequenceStep();
    if(sequenceDraft.reduce((sum,s,i)=>sum+(i===sequenceSelected?step.delay_ms:s.delay_ms),0)>600000)throw Error('The timeline cannot exceed 10 minutes.');
    sequenceDraft[sequenceSelected]=step;$('#sequence-step-error').textContent='';return true;
  }catch(error){$('#sequence-step-error').textContent=error.message;return false;}
}
function selectSequenceStep(index,commit=true){
  if(commit&&!commitSequenceStep())return;
  stopSequencePreview();sequenceSelected=index;
  const form=$('#step-form'),step=sequenceDraft[index];form.hidden=!step;
  if(step){fillAction(form,step);form.elements.name.value=step.name;form.elements.delay_ms.value=step.delay_ms;$('#step-arguments').replaceChildren();(step.args||[]).forEach(arg=>addArgument(arg,$('#step-arguments')));configureAction(form);$('#step-title').textContent=`Step ${index+1} details`;if(sequenceReadOnly)for(const field of form.querySelectorAll('input,select,textarea,button'))field.disabled=true;}
  $('#sequence-step-error').textContent='';renderSequenceDraft();
}
function renderSequenceDraft(){
  const duration=sequenceDraft.reduce((sum,step)=>sum+step.delay_ms,0);
  $('#sequence-summary').textContent=`${sequenceDraft.length} / 64 steps · ${formatSequenceTime(duration)}`;
  $('#sequence-scrub').max=sequenceDuration();$('#sequence-scrub').disabled=!sequenceDraft.length;
  $('#add-sequence-step').disabled=sequenceReadOnly||sequenceDraft.length>=64;
  drawSequenceTimeline($('#sequence-timeline'),sequenceSchedule(sequenceDraft),selectSequenceStep,sequenceSelected);
  zoomSequenceTimeline();paintSequencePreview(Math.min(previewTime,sequenceDuration()),previewTime>0);$('#preview-sequence').disabled=!sequenceDraft.length;
  const list=$('#sequence-step-list');list.replaceChildren();
  sequenceDraft.forEach((step,index)=>{
    const row=el('div',undefined,'sequence-list-row'+(index===sequenceSelected?' selected':''));
    const select=el('button',`${index+1}. ${step.name}`);select.type='button';select.onclick=()=>selectSequenceStep(index);row.append(select);
    for(const [text,title,operation] of [['⧉','Duplicate step',2],['↑','Move up',-1],['↓','Move down',1],['×','Remove step',0]]){
      const button=el('button',text,'small');button.type='button';button.setAttribute('aria-label',`${title} ${index+1}`);button.disabled=sequenceReadOnly||(operation===2?sequenceDraft.length>=64:operation<0?index===0:operation>0?index===sequenceDraft.length-1:false);
      button.onclick=()=>{
        if(!commitSequenceStep())return;stopSequencePreview();
        if(operation===2){const copy=cloneSequence(sequenceDraft[index]);if(sequenceDraft.reduce((sum,s)=>sum+s.delay_ms,0)+copy.delay_ms>600000){$('#sequence-step-error').textContent='Duplicating this step would exceed 10 minutes.';return;}copy.name=(copy.name+' copy').slice(0,100);sequenceDraft.splice(index+1,0,copy);selectSequenceStep(index+1,false);}
        else if(operation){const other=index+operation;[sequenceDraft[index],sequenceDraft[other]]=[sequenceDraft[other],sequenceDraft[index]];selectSequenceStep(other,false);}
        else{sequenceDraft.splice(index,1);selectSequenceStep(Math.min(index,sequenceDraft.length-1),false);}
      };row.append(button);
    }
    list.append(row);
  });
}
function openSequence(form){
  sequenceReadOnly=form.id==='form'&&form.elements.action.disabled;
  $('#sequence-on-error').disabled=sequenceReadOnly;$('#sequence-source').disabled=sequenceReadOnly;$('#sequence-direction').disabled=sequenceReadOnly;$('#use-sequence').hidden=sequenceReadOnly;$('#apply-sequence-step').hidden=sequenceReadOnly;
  for(const field of $('#step-form').querySelectorAll('input,select,textarea,button'))field.disabled=false;
  $('#discard-sequence').textContent=sequenceReadOnly?'Close timeline':'Discard changes';
  sequenceOwner=form;const data=getSequenceData(form);sequenceDraft=data.steps.length?data.steps:[stepDefault(1)];sequenceSelected=-1;
  $('#sequence-follow').checked=false;$('#sequence-follow').disabled=form.id!=='form';
  $('#sequence-source').value=data.sequence_source;$('#sequence-direction').value=data.sequence_direction;updateSequenceSource();$('#sequence-on-error').value=data.sequence_on_error;$('#sequence-error').textContent='';
  selectSequenceStep(0,false);$('#sequence-editor').showModal();
}
function updateSequenceSource(){
  const dmx=$('#sequence-source').value==='dmx';$('#sequence-direction-label').hidden=!dmx;$('#sequence-follow-label').hidden=!dmx;
  $('#sequence-on-error').options[0].textContent=dmx?'Stop remaining crossed cues':'Stop remaining steps';
  $('#sequence-source-help').textContent=dmx?'DMX 0–255 maps to 0–100% of the scheduled timeline. First input establishes a baseline. Forward motion fires every crossed cue, including skipped-over cues. Reverse motion re-arms cues; Both directions also fires them on reverse crossings. Time-zero cues fire when leaving DMX 0. Preview sends no outputs.':'Steps run on a clock after the trigger activates.';
}
function initializeSequenceUI(){
  $('#add-step-arg').onclick=()=>addArgument(undefined,$('#step-arguments'));
  $('#sequence-source').onchange=()=>{if(!commitSequenceStep())return;stopSequencePreview();$('#sequence-follow').checked=false;updateSequenceSource();renderSequenceDraft();};
  $('#sequence-follow').onchange=()=>{if(state)renderSequenceLive(state);};
  $('#close-sequence').onclick=$('#discard-sequence').onclick=()=>$('#sequence-editor').close();
  $('#sequence-editor').addEventListener('close',()=>{stopSequencePreview();sequenceOwner=null;});
  $('#step-form').onsubmit=event=>{event.preventDefault();if(commitSequenceStep()){stopSequencePreview();renderSequenceDraft();}};
  $('#add-sequence-step').onclick=()=>{if(sequenceReadOnly||!commitSequenceStep()||sequenceDraft.length>=64)return;sequenceDraft.push(stepDefault(sequenceDraft.length+1));selectSequenceStep(sequenceDraft.length-1,false);};
  $('#use-sequence').onclick=()=>{
    if(sequenceReadOnly||!commitSequenceStep())return;
    if(!sequenceDraft.length){$('#sequence-error').textContent='Add at least one step.';return;}
    if($('#sequence-source').value==='dmx'&&!sequenceDraft.some(step=>step.delay_ms>0)){$('#sequence-error').textContent='Add a delay so the DMX cue timeline has a nonzero duration.';return;}
    setSequenceData(sequenceOwner,{steps:sequenceDraft,sequence_on_error:$('#sequence-on-error').value,sequence_source:$('#sequence-source').value,sequence_direction:$('#sequence-direction').value});updateFollowerFields();$('#sequence-editor').close();
  };
  $('#preview-sequence').onclick=startSequencePreview;
  $('#stop-preview').onclick=()=>{$('#sequence-follow').checked=false;stopSequencePreview();};
  $('#sequence-scrub').oninput=()=>seekSequencePreview(Number($('#sequence-scrub').value));
  $('#sequence-zoom').onchange=zoomSequenceTimeline;
  $('#sequence-speed').onchange=()=>{previewOrigin=previewTime;previewStarted=performance.now();};
  initializeTimelineDrag();
}
function renderSequenceLive(data){
  if(!sequenceOwner||!$('#sequence-editor').open||$('#sequence-source').value!=='dmx'||!$('#sequence-follow').checked)return;
  const input=liveFadeInput(sequenceOwner,data);stopSequencePreview(false);
  if(input.live&&input.level!=null){paintSequencePreview(input.level/255*sequenceDuration());$('#sequence-preview-status').textContent=`Live DMX ${input.level} · ${formatSequenceTime(Math.round(previewTime))} · Preview sends no outputs`;}
  else $('#sequence-preview-status').textContent='No live DMX level available. Preview holds its position.';
}
function renderSequenceRuns(runs){
  const container=$('#sequence-runs');
  if(!runs.length){if(container.dataset.empty!=='true'){container.replaceChildren(el('p','No sequences have run yet. Choose Action sequence in a trigger to build one.'));container.dataset.empty='true';}return;}
  if(container.dataset.empty==='true'){container.replaceChildren();container.dataset.empty='false';}
  const ids=new Set(runs.map(run=>run.id));for(const card of [...container.children])if(!ids.has(card.dataset.id))card.remove();
  for(const [index,run] of runs.entries()){
    let card=[...container.children].find(card=>card.dataset.id===run.id);
    if(!card){
      card=el('div',undefined,'sequence-run');card.dataset.id=run.id;
      const header=el('div',undefined,'sequence-run-heading');header.append(el('strong'),el('span'));
      const cancel=el('button','Cancel pending steps','small');cancel.type='button';cancel.onclick=async()=>{cancel.disabled=true;try{await api('/api/sequences/'+run.id,'DELETE');pollSoon=true;}catch(error){notice(error.message);}finally{cancel.disabled=false;}};header.append(cancel);
      const details=el('details');details.open=index===0;details.append(el('summary','View step timeline'),el('div',undefined,'sequence-timeline'));
      card.append(header,el('p'),details);container.append(card);
    }
    if(container.children[index]!==card)container.insertBefore(card,container.children[index]||null);
    card.className='sequence-run '+run.status;const header=card.firstChild;header.children[0].textContent=run.name;header.children[1].textContent=`${run.status.toUpperCase()} · ${formatSequenceTime(run.elapsed_ms)}`;header.children[2].hidden=run.status!=='running';card.children[1].textContent=run.detail;
    const timeline=card.querySelector('.sequence-timeline'),signature=JSON.stringify(run.steps);
    if(timeline.dataset.signature!==signature){drawSequenceTimeline(timeline,run.steps);timeline.dataset.signature=signature;}
    timeline.style.setProperty('--playhead',`${Math.min(100,run.elapsed_ms/(Number(timeline.dataset.duration)||1000)*100)}%`);
  }
}
