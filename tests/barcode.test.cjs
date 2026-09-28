const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const template=fs.readFileSync('app/templates/sale_new.html','utf8');
function setup(){
  const input={value:'',focus(){this.focused=true},select(){}},status={textContent:''};
  const product={dataset:{id:'1',code:'00123',name:'Martillo',boxes:'1',perBox:'3',loose:'0',unit:'100'}};
  const context={items:[],products:[product],document:{querySelector:s=>s==='#find'?input:status},draw(){}};
  vm.createContext(context);
  for(const [start,end] of [['function addUnitOnlyProduct','function selectProduct'],['function filterProducts','document.querySelector(\'#find\').onkeydown']])vm.runInContext(template.slice(template.indexOf(start),template.indexOf(end)),context);
  return {context,input,status,product,scan(code){input.value=code;context.addByCode()}};
}
test('scans preserve leading zeros, match the single code and increment units',()=>{
 const s=setup();s.scan('00123');s.scan('00123');assert.equal(s.context.items.length,1);assert.equal(s.context.items[0].qty,2);assert.equal(s.context.items[0].presentation,'UNIDAD');assert.equal(s.input.value,'');assert.equal(s.input.focused,true);
});
test('scanning respects stock already reserved as boxes',()=>{
 const s=setup();s.context.items.push({id:'1',presentation:'CAJA',qty:1});s.scan('00123');assert.equal(s.context.items.length,1);assert.match(s.status.textContent,/Sin stock/);
});
test('unknown, blank and ambiguous scans do not add products',()=>{
 const s=setup();s.scan('');s.scan('missing');assert.equal(s.context.items.length,0);s.context.products.push({dataset:{...s.product.dataset,id:'2',code:'00123'}});s.scan('00123');assert.equal(s.context.items.length,0);assert.match(s.status.textContent,/varios productos/);
});
test('repeated scans cannot exceed stock',()=>{
 const s=setup();for(let i=0;i<4;i++)s.scan('00123');assert.equal(s.context.items[0].qty,3);assert.match(s.status.textContent,/Sin stock/);
});

test('the single search filters names and codes and resets after a scan',()=>{
 const s=setup();s.context.products.push({dataset:{...s.product.dataset,id:'2',code:'00222',name:'Tornillo'}});
 s.input.value='mart';s.context.filterProducts();assert.equal(s.product.hidden,false);assert.equal(s.context.products[1].hidden,true);
 s.context.addByCode();assert.equal(s.context.items.length,0);assert.equal(s.input.value,'mart');
 s.scan('00123');assert.equal(s.context.items.length,1);assert.equal(s.input.value,'');assert.equal(s.context.products[1].hidden,false);
 s.input.value='00222';s.context.filterProducts();assert.equal(s.product.hidden,true);assert.equal(s.context.products[1].hidden,false);
});
