const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(checked, editing = false) {
  const ids = ['has-box', 'box-fields', 'units-per-box', 'closed-boxes', 'calculated-units',
    'loose-units-row', 'minimum-stock-label', 'product-image', 'image-preview', 'image-preview-wrap'];
  const nodes = Object.fromEntries(ids.map(id => [`#${id}`, {value:'', hidden:false, textContent:''}]));
  nodes['#has-box'].checked = checked;
  nodes['#units-per-box'].value = '10';
  nodes['#closed-boxes'].value = '4';
  const boxInputs = [{required:false}, {required:false}];
  nodes['#box-fields'].querySelectorAll = () => boxInputs;
  if (editing) {
    nodes['#closed-boxes'] = null;
    nodes['#calculated-units'] = null;
    nodes['#loose-units-row'] = null;
  }
  const template = fs.readFileSync('app/templates/product_form.html', 'utf8');
  vm.runInNewContext(template.split('<script>')[1].split('</script>')[0], {
    document: {querySelector: selector => nodes[selector], querySelectorAll: () => []},
  });
  return {nodes, boxInputs, toggle(value) { nodes['#has-box'].checked = value; nodes['#has-box'].onchange(); }};
}

test('checking box sales changes the minimum label to boxes and unchecking restores units', () => {
  const s = setup(false);
  assert.equal(s.nodes['#minimum-stock-label'].textContent, 'Stock mínimo (unidades)');
  s.toggle(true);
  assert.equal(s.nodes['#minimum-stock-label'].textContent, 'Stock mínimo (cajas)');
  assert.equal(s.nodes['#box-fields'].hidden, false);
  assert.equal(s.nodes['#loose-units-row'].hidden, true);
  assert.equal(s.nodes['#calculated-units'].textContent, 'Unidades iniciales por cajas: 40');
  assert.ok(s.boxInputs.every(input => input.required));
  s.toggle(false);
  assert.equal(s.nodes['#minimum-stock-label'].textContent, 'Stock mínimo (unidades)');
  assert.equal(s.nodes['#box-fields'].hidden, true);
  assert.ok(s.boxInputs.every(input => !input.required));
});

test('editing a box product starts with a minimum in boxes even without initial stock fields', () => {
  const s = setup(true, true);
  assert.equal(s.nodes['#minimum-stock-label'].textContent, 'Stock mínimo (cajas)');
  s.toggle(false);
  assert.equal(s.nodes['#minimum-stock-label'].textContent, 'Stock mínimo (unidades)');
});
