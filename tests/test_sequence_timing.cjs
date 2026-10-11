// Run with: node --test tests/test_sequence_timing.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/sequences.js'),'utf8'),context);
const delayAt=context.sequenceDelayAt;
const schedule=context.sequenceSchedule;
const steps=[{delay_ms:100},{delay_ms:500},{delay_ms:1500}];
test('dragging a middle step preserves subsequent gaps',()=>{
  const edited=steps.map(s=>({...s}));edited[1].delay_ms=delayAt(edited,1,900,100);
  assert.deepEqual(Array.from(schedule(edited),s=>s.at_ms),[100,900,2400]);
  assert.equal(edited[2].delay_ms,1500);
});
test('dragging cannot cross the previous step',()=>{
  assert.equal(delayAt(steps,1,-200,100),0);
  assert.equal(delayAt(steps,0,-200,100),0);
});
test('timeline limit includes the remaining steps',()=>{
  assert.equal(delayAt(steps,1,700000,100),598400);
  assert.equal(delayAt([{delay_ms:600000},{delay_ms:0}],1,700000),0);
});
test('absolute grid snapping and millisecond precision',()=>{
  assert.equal(delayAt(steps,1,754,100),700);
  assert.equal(delayAt(steps,1,754,1),654);
  assert.equal(delayAt([{delay_ms:201},{delay_ms:500}],1,701,100),499);
});
test('zero-delay steps retain their saved order',()=>{
  const zero=[{name:'one',delay_ms:0},{name:'two',delay_ms:0}];
  assert.deepEqual(Array.from(schedule(zero),s=>[s.name,s.at_ms]),[['one',0],['two',0]]);
});
