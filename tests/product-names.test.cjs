const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(id, textContent = '') {
  return {
    id, textContent, value: '', hidden: false, listeners: {}, attributes: {},
    addEventListener(type, callback) { this.listeners[type] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    focus() { this.listeners.focus?.(); },
    scrollIntoView() {},
  };
}

function setup(names = ['Bulón largo', 'Destornillador', 'Tornillos']) {
  const input = element('product-name'), toggle = element('product-name-toggle');
  const list = element('product-name-options'), picker = element('product-name-picker');
  const options = names.map((name, index) => element(`option-${index}`, name));
  list.hidden = true;
  list.querySelectorAll = () => options;
  picker.contains = node => [input, toggle, list, ...options].includes(node);
  const nodes = Object.fromEntries([input, toggle, list, picker].map(node => [`#${node.id}`, node]));
  vm.runInNewContext(fs.readFileSync('app/static/product-names.js', 'utf8'), {
    document: {querySelector: selector => nodes[selector]},
  });
  return {
    input, toggle, list, picker, options,
    type(value) { input.value = value; input.listeners.input(); },
    key(key) {
      const event = {key, prevented: false, preventDefault() { this.prevented = true; }};
      input.listeners.keydown(event);
      return event.prevented;
    },
    visible() { return list.hidden ? [] : options.filter(option => !option.hidden).map(option => option.textContent); },
  };
}

test('filters names while typing, ignoring case and accents, and restores all on clearing', () => {
  const s = setup();
  s.toggle.listeners.click();
  assert.deepEqual(s.visible(), ['Bulón largo', 'Destornillador', 'Tornillos']);
  s.type('TOR');
  assert.deepEqual(s.visible(), ['Destornillador', 'Tornillos']);
  s.type('largo bulon');
  assert.deepEqual(s.visible(), ['Bulón largo']);
  s.type('');
  assert.equal(s.visible().length, 3);
});

test('mouse selection fills the name and closes the dropdown without submitting', () => {
  const s = setup();
  s.type('tor');
  let prevented = false;
  s.options[2].listeners.pointerdown({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  s.options[2].listeners.click();
  assert.equal(s.input.value, 'Tornillos');
  assert.equal(s.input.attributes['aria-expanded'], 'false');
  assert.equal(s.list.hidden, true);
});

test('arrows and Enter choose a matching name; Escape and leaving the field close the list', () => {
  const s = setup();
  s.type('tor');
  assert.equal(s.key('ArrowDown'), true);
  assert.equal(s.input.attributes['aria-activedescendant'], 'option-1');
  s.key('ArrowDown');
  assert.equal(s.input.attributes['aria-activedescendant'], 'option-2');
  assert.equal(s.key('Enter'), true);
  assert.equal(s.input.value, 'Tornillos');
  s.type('tor');
  s.key('ArrowUp');
  assert.equal(s.input.attributes['aria-activedescendant'], 'option-2');
  assert.equal(s.key('Escape'), true);
  assert.equal(s.list.hidden, true);
  s.input.focus();
  s.picker.listeners.focusout({relatedTarget: null});
  assert.equal(s.list.hidden, true);
});

test('new names and an empty catalog remain editable without forcing a suggestion', () => {
  for (const s of [setup(), setup([])]) {
    s.type('Arandela nueva');
    assert.equal(s.list.hidden, true);
    assert.equal(s.key('Enter'), false);
    s.key('ArrowDown');
    assert.equal(s.input.value, 'Arandela nueva');
    assert.equal(s.input.attributes['aria-activedescendant'], undefined);
  }
});
