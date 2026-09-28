const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function element(data={}){return {dataset:data,value:'',hidden:false,textContent:'',innerHTML:'',listeners:{},attributes:{},addEventListener(type,fn){this.listeners[type]=fn},setAttribute(k,v){this.attributes[k]=v},focus(){this.focused=true}}}
function setup(){
 const ids=['customer','customer-panel','customer-search','cart-customer','cart-customer-name','cart-customer-detail','customer-empty','remove-customer','change-customer','find','cart','subtotal','total','discount-row','discount-total','presentation-dialog','presentation','add-item','add-payment','confirm'];
 const nodes=Object.fromEntries(ids.map(id=>['#'+id,element()]));nodes['#customer'].dataset.discount='0';
 const options=[element({id:'1',name:'Álvarez, María',dni:'12345',discount:'10'}),element({id:'2',name:'Pérez, Juan',dni:'98765',discount:'0'})];
 const choices=[element(),element()];choices[0].value='no';choices[1].value='yes';
 const context={document:{querySelector:s=>nodes[s],querySelectorAll:s=>s==='.product'?[]:s==='.customer-option'?options:choices}};
 vm.createContext(context);
 const template=fs.readFileSync('app/templates/sale_new.html','utf8');
 vm.runInContext(template.split('<script>')[1].split('</script>')[0].replace(/\{\{url_for\('sale_create'\)\}\}/g,'/sales'),context);
 vm.runInContext(fs.readFileSync('app/static/sale-customers.js','utf8'),context);
 return {nodes,options,choices,context};
}
test('customer search filters accents, multiple name terms and DNI without changing product search',()=>{
 const s=setup(),search=s.nodes['#customer-search'];s.nodes['#find'].value='martillo';
 s.choices[1].listeners.change();assert.equal(s.nodes['#customer-panel'].hidden,false);assert.equal(search.focused,true);
 search.value='maria alva';search.listeners.input();assert.equal(s.options[0].hidden,false);assert.equal(s.options[1].hidden,true);
 search.value='987';search.listeners.input();assert.equal(s.options[0].hidden,true);assert.equal(s.options[1].hidden,false);
 search.value='nadie';search.listeners.input();assert.equal(s.nodes['#customer-empty'].hidden,false);
 search.value='';search.listeners.input();assert.ok(s.options.every(option=>!option.hidden));assert.equal(s.nodes['#find'].value,'martillo');
});
test('selection shows customer in cart, recalculates discount and clears it when removed',()=>{
 const s=setup();vm.runInContext("items.push({id:'1',name:'Martillo',presentation:'UNIDAD',price:100,qty:2});draw()",s.context);
 s.options[0].listeners.click();assert.equal(s.nodes['#customer'].value,'1');assert.equal(s.nodes['#cart-customer'].hidden,false);assert.equal(s.nodes['#customer-panel'].hidden,true);
 assert.equal(s.nodes['#cart-customer-name'].textContent,'Álvarez, María');assert.equal(s.nodes['#total'].textContent,'$ 180,00');assert.equal(s.options[0].attributes['aria-pressed'],'true');
 s.nodes['#customer-search'].value='maria';s.nodes['#change-customer'].listeners.click();assert.equal(s.nodes['#customer-panel'].hidden,false);assert.equal(s.nodes['#customer-search'].value,'');assert.equal(s.nodes['#customer'].value,'1');
 s.options[1].listeners.click();assert.equal(s.nodes['#customer'].value,'2');assert.equal(s.nodes['#total'].textContent,'$ 200,00');assert.equal(s.options[0].attributes['aria-pressed'],'false');
 s.options[0].listeners.click();s.nodes['#remove-customer'].listeners.click();assert.equal(s.nodes['#customer'].value,'');assert.equal(s.nodes['#cart-customer'].hidden,true);assert.equal(s.nodes['#total'].textContent,'$ 200,00');assert.equal(s.choices[0].checked,true);
});
test('choosing No removes the selected customer and discount',()=>{
 const s=setup();s.options[0].listeners.click();s.choices[0].listeners.change();assert.equal(s.nodes['#customer'].value,'');assert.equal(s.nodes['#customer'].dataset.discount,'0');assert.equal(s.nodes['#customer-panel'].hidden,true);assert.equal(s.nodes['#cart-customer'].hidden,true);
});
