const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app/static/product-search.js','utf8');
function setup(){
 const listeners={},pending=[];
 const input={value:'',focus(){},select(){},addEventListener:(event,fn)=>listeners[event]=fn};
 const form={action:'http://localhost/products',addEventListener:(event,fn)=>listeners[event]=fn};
 const results={innerHTML:'initial',setAttribute(){},removeAttribute(){},replaceChildren(){this.innerHTML=''}};
 const status={textContent:''};let timer;
 const elements={'#product-search-form':form,'#product-search':input,'#product-results':results,'#product-search-status':status};
 vm.runInNewContext(source,{document:{querySelector:s=>elements[s]},window:{location:{href:form.action}},URL,AbortController,
 setTimeout:fn=>{timer=fn},clearTimeout:()=>{timer=null},fetch:(url)=>new Promise(resolve=>pending.push({url,resolve}))});
 return {input,results,status,pending,type(value){input.value=value;listeners.input()},flush(){timer()},submit(){listeners.submit({preventDefault(){}})}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const reply=(request,html)=>request.resolve({ok:true,redirected:false,text:async()=>html});
test('typing searches automatically and clearing searches all products',async()=>{
 const s=setup();s.type('mar');s.flush();assert.equal(s.pending[0].url.searchParams.get('q'),'mar');
 reply(s.pending[0],'Martillo');await settle();assert.equal(s.results.innerHTML,'Martillo');
 s.type('');s.flush();assert.equal(s.pending[1].url.searchParams.get('q'),'');
 reply(s.pending[1],'All products');await settle();assert.equal(s.results.innerHTML,'All products');
});
test('old responses cannot replace a newer search and Enter searches immediately',async()=>{
 const s=setup();s.type('m');s.flush();s.type('mart');s.submit();
 reply(s.pending[1],'Latest');await settle();reply(s.pending[0],'Old');await settle();assert.equal(s.results.innerHTML,'Latest');
});
test('failed searches clear stale results and allow retry',async()=>{
 const s=setup();s.type('mart');s.flush();s.pending[0].resolve({ok:false});await settle();
 assert.equal(s.results.innerHTML,'');assert.match(s.status.textContent,/reintentar/);assert.equal(s.results.inert,false);
 s.submit();reply(s.pending[1],'Recovered');await settle();assert.equal(s.results.innerHTML,'Recovered');
});
