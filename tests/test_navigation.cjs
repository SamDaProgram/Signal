const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
for(const [file,key] of [['preshow.js','config'],['config-controls.js','preshow']]){
  const source=fs.readFileSync(path.join(__dirname,'../static',file),'utf8');
  const code=source.slice(source.lastIndexOf("fetch('/api/navigation')"));
  async function run(ok,data){
    const link={href:'http://127.0.0.1/'};
    await vm.runInNewContext(code,{$:()=>link,configElement:()=>link,URL,location:{href:'http://127.0.0.1:8081/'},fetch:async()=>({ok,json:async()=>data})});
    return link.href;
  }
  test(`${file}: missing endpoint and invalid payload preserve the fallback link`,async()=>{
    for(const [ok,data] of [[false,{error:'Not found'}],[true,{}],[true,{[key]:null}],[true,{[key]:''}],[true,{[key]:'javascript:alert(1)'}]]){
      assert.equal(await run(ok,data),'http://127.0.0.1/');
    }
  });
  test(`${file}: valid navigation supports custom ports`,async()=>{
    assert.equal(await run(true,{[key]:'http://localhost:8080/'}),'http://localhost:8080/');
  });
}
