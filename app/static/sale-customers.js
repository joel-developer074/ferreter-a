// Selección de cliente independiente del buscador de productos.
(() => {
  const field = document.querySelector('#customer');
  const panel = document.querySelector('#customer-panel');
  const search = document.querySelector('#customer-search');
  const options = [...document.querySelectorAll('.customer-option')];
  const summary = document.querySelector('#cart-customer');
  const choices = [...document.querySelectorAll('input[name=has_customer]')];
  const normalize = text => text.toLocaleLowerCase('es').normalize('NFD').replace(/[\u0300-\u036f]/g, '');

  function filterCustomers() {
    const terms = normalize(search.value).trim().split(/\s+/).filter(Boolean);
    options.forEach(option => {
      const text = normalize(option.dataset.name + ' ' + option.dataset.dni);
      option.hidden = !terms.every(term => text.includes(term));
    });
    document.querySelector('#customer-empty').hidden = options.some(option => !option.hidden);
  }

  function selectCustomer(option) {
    field.value = option ? option.dataset.id : '';
    field.dataset.discount = option ? option.dataset.discount : '0';
    summary.hidden = !option;
    document.querySelector('#cart-customer-name').textContent = option ? option.dataset.name : '';
    document.querySelector('#cart-customer-detail').textContent = option
      ? 'DNI ' + (option.dataset.dni || '—') + ' · Descuento: ' + Number(option.dataset.discount) + '%'
      : '';
    options.forEach(item => item.setAttribute('aria-pressed', String(item === option)));
    draw();
  }

  choices.forEach(choice => choice.addEventListener('change', () => {
    const enabled = choice.value === 'yes';
    panel.hidden = !enabled;
    search.value = '';
    filterCustomers();
    if (!enabled) selectCustomer(null);
    (enabled ? search : document.querySelector('#find')).focus();
  }));
  search.addEventListener('input', filterCustomers);
  options.forEach(option => option.addEventListener('click', () => {
    selectCustomer(option);
    panel.hidden = true;
    document.querySelector('#find').focus();
  }));
  document.querySelector('#change-customer').addEventListener('click', () => {
    panel.hidden = false;
    search.value = '';
    filterCustomers();
    search.focus();
  });
  document.querySelector('#remove-customer').addEventListener('click', () => {
    choices.forEach(choice => choice.checked = choice.value === 'no');
    panel.hidden = true;
    search.value = '';
    filterCustomers();
    selectCustomer(null);
    document.querySelector('#find').focus();
  });
  filterCustomers();
})();
