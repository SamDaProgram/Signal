const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/actions.js'),'utf8');
class Element {
  constructor(tag,text){this.tag=tag;this.textContent=text;this.children=[];this.value='';}
  setAttribute(){}
  append(...children){this.children.push(...children);for(const child of children)child.parent=this;}
  replaceChildren(){this.children=[];}
  remove(){this.parent.children=this.parent.children.filter(child=>child!==this);}
  querySelector(tag){return this.children.find(child=>child.tag===tag);}
}
function setup(){
  const context=vm.createContext({el:(tag,text)=>new Element(tag,text)});
  vm.runInContext(source,context);
  return context;
}
test('OSC controls round-trip every supported argument type without JSON entry',()=>{
  const context=setup(),container=new Element('div');
  const args=[{type:'i',value:0},{type:'f',value:0.75},{type:'s',value:'cue name'},{type:'s',value:''},{type:'T'},{type:'F'}];
  for(const arg of args)context.addArgument(arg,container);
  assert.deepEqual(JSON.parse(JSON.stringify(context.readArguments(container))),args);
  assert.equal(container.children[4].querySelector('input').hidden,true);
  const row=container.children[0];row.querySelector('select').value='f';row.querySelector('select').onchange();
  assert.equal(row.querySelector('input').step,'any');
  row.querySelector('button').onclick();assert.equal(container.children.length,5);
});
test('MIDI dropdown preserves saved unavailable ports and shares discovered options',()=>{
  const context=setup();vm.runInContext('midiPorts=["QLab", "IAC Bus 1"]',context);
  for(let i=0;i<3;i++){
    const field=new Element('select');context.populateMidiPorts(field,'Disconnected synth');
    assert.deepEqual(field.children.map(option=>option.value),['','QLab','IAC Bus 1','Disconnected synth']);
    assert.equal(field.value,'Disconnected synth');
    assert.match(field.children[3].textContent,/unavailable/);
    field.value='QLab';context.populateMidiPorts(field);
    assert.equal(field.value,'QLab');assert.equal(field.children.length,3);
  }
});
