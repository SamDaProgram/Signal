// Run with: node --test tests/test_faders.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/faders.js'),'utf8'),context);
const configs=[];
for(const fade_curve of ['linear','smooth','hold'])for(const fade_type of ['i','f'])configs.push({fade_curve,fade_type,fade_keyframes:[{level:20,values:[-10,0,255]},{level:128,values:[10,128,0]},{level:240,values:[0,255,128]}]});
test('visual preview agrees with OSC interpolation at every DMX level',()=>{
  const backend=JSON.parse(execFileSync('python3',['-c',
    'import sys,json; from app import fade_args; print(json.dumps([[[a["value"] for a in fade_args(c,n)] for n in range(256)] for c in json.load(sys.stdin)]))'
  ],{cwd:path.join(__dirname,'..'),input:JSON.stringify(configs),encoding:'utf8'}));
  configs.forEach((config,i)=>{
    for(let level=0;level<256;level++)Array.from(context.sampleFade(config,level)).forEach((value,j)=>assert.ok(Math.abs(value-backend[i][level][j])<1e-10,`${config.fade_curve} ${config.fade_type} at ${level}`));
  });
});
test('preview clamps outside keyframes and blends RGB midpoint',()=>{
  const config={fade_curve:'linear',fade_type:'f',fade_keyframes:[{level:20,values:[1,0,0]},{level:220,values:[0,0,1]}]};
  assert.deepEqual(Array.from(context.sampleFade(config,0)),[1,0,0]);
  assert.deepEqual(Array.from(context.sampleFade(config,120)),[.5,0,.5]);
  assert.deepEqual(Array.from(context.sampleFade(config,255)),[0,0,1]);
});

test('graph insertion uses click value for selected component and interpolates the others',()=>{
  const data={fade_type:'f',fade_curve:'linear',fade_range:{min:0,max:1},fade_keyframes:[{level:0,values:[1,0,0]},{level:255,values:[0,0,1]}]};
  assert.equal(context.insertFadeKeyframe(data,128,1,.75),1);
  assert.equal(data.fade_keyframes[1].values[1],.75);
  assert.ok(Math.abs(data.fade_keyframes[1].values[0]-127/255)<1e-12);
  assert.ok(Math.abs(data.fade_keyframes[1].values[2]-128/255)<1e-12);
  assert.equal(context.insertFadeKeyframe(data,128,1,.9),1);
  assert.equal(data.fade_keyframes.length,3);
  assert.equal(data.fade_keyframes[1].values[1],.75);
});
test('ruler insertion samples existing curve and respects the keyframe limit',()=>{
  const data={fade_type:'i',fade_curve:'linear',fade_range:{min:0,max:255},fade_keyframes:[{level:0,values:[0]},{level:255,values:[255]}]};
  context.insertFadeKeyframe(data,64);
  assert.equal(data.fade_keyframes[1].values[0],64);
  for(let level=1;data.fade_keyframes.length<64;level++)context.insertFadeKeyframe(data,level);
  assert.throws(()=>context.insertFadeKeyframe(data,100),/64/);
  assert.equal(context.insertFadeKeyframe(data,0),0);
});
test('output range conversion preserves curve proportions, colors and positions',()=>{
  const data={fade_type:'f',fade_curve:'smooth',fade_range:{min:0,max:1},fade_keyframes:[{level:0,values:[0,.5,1]},{level:255,values:[1,.25,0]}]};
  const bytes=context.rescaleFade(data,0,255),percent=context.rescaleFade(bytes,0,100),unit=context.rescaleFade(percent,0,1);
  assert.deepEqual(Array.from(bytes.fade_keyframes[0].values),[0,127.5,255]);
  assert.deepEqual(Array.from(percent.fade_keyframes[0].values),[0,50,100]);
  assert.deepEqual(Array.from(unit.fade_keyframes[0].values),[0,.5,1]);
  assert.deepEqual(Array.from(unit.fade_keyframes,f=>f.level),[0,255]);
  assert.equal(data.fade_keyframes[0].values[1],.5);
  const custom=context.rescaleFade(data,-10,10);
  assert.deepEqual(Array.from(custom.fade_keyframes[0].values),[-10,0,10]);
});
test('integer output becomes float when normalized and invalid ranges are rejected',()=>{
  const data={fade_type:'i',fade_curve:'linear',fade_range:{min:0,max:255},fade_keyframes:[{level:0,values:[128]},{level:255,values:[255]}]};
  const result=context.rescaleFade(data,0,1);
  assert.equal(result.fade_type,'f');assert.equal(result.fade_keyframes[0].values[0],128/255);
  for(const range of [[1,1],[1,0],[0,Infinity],[NaN,1]])assert.throws(()=>context.rescaleFade(data,...range));
});
test('input display conversions round-trip every DMX level without changing output',()=>{
  for(const mode of ['dmx','normalized','percent'])for(let level=0;level<=255;level++)assert.equal(context.fadeInputLevel(context.fadeInputValue(level,mode),mode),level);
  assert.equal(context.fadeInputLevel(.5,'normalized'),128);
  assert.equal(context.fadeInputLevel(50,'percent'),128);
});
test('OSC preview includes destination, address, type tags and float32 wire values',()=>{
  const data={fade_type:'f',fade_curve:'linear',fade_keyframes:[{level:0,values:[0,1,0]},{level:255,values:[1,0,1]}]};
  const preview=context.fadeOscPreview(data,128,{host:'127.0.0.1',port:8000,address:'/color'});
  assert.equal(preview.destination,'UDP → 127.0.0.1:8000');
  assert.match(preview.message,/^\/color ,fff /);
  const json=JSON.parse(preview.json);
  assert.equal(json.args[0].value,Math.fround(128/255));
  assert.equal(json.args[1].value,Math.fround(127/255));
  assert.equal(json.address,'/color');
});
test('OSC preview tracks integer output scale and endpoint levels',()=>{
  const data={fade_type:'i',fade_curve:'linear',fade_keyframes:[{level:0,values:[0]},{level:255,values:[255]}]};
  const destination={host:'192.168.1.20',port:9000,address:'/brightness'};
  assert.equal(context.fadeOscPreview(data,128,destination).message,'/brightness ,i 128');
  assert.equal(context.fadeOscPreview(data,255,destination).message,'/brightness ,i 255');
});
