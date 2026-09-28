// Nombres reutilizables con filtro y navegación por teclado; admite nombres nuevos.
(() => {
  const picker = document.querySelector('#product-name-picker');
  const input = document.querySelector('#product-name');
  const toggle = document.querySelector('#product-name-toggle');
  const list = document.querySelector('#product-name-options');
  const options = [...list.querySelectorAll('[role="option"]')];
  const normalize = value => value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase().trim();
  let matches = [], activeIndex = -1;

  function activate(index) {
    activeIndex = index;
    options.forEach(option => option.setAttribute('aria-selected', 'false'));
    input.removeAttribute('aria-activedescendant');
    if (index < 0) return;
    const option = matches[index];
    option.setAttribute('aria-selected', 'true');
    input.setAttribute('aria-activedescendant', option.id);
    option.scrollIntoView({block: 'nearest'});
  }

  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-expanded', 'false');
    activate(-1);
  }

  function open() {
    const terms = normalize(input.value).split(/\s+/).filter(Boolean);
    matches = options.filter(option => {
      const name = normalize(option.textContent);
      option.hidden = !terms.every(term => name.includes(term));
      return !option.hidden;
    });
    activate(-1);
    list.hidden = matches.length === 0;
    input.setAttribute('aria-expanded', String(!list.hidden));
    toggle.setAttribute('aria-expanded', String(!list.hidden));
  }

  function choose(option) {
    input.value = option.textContent;
    input.focus();
    close();
  }

  input.addEventListener('input', open);
  input.addEventListener('focus', open);
  toggle.addEventListener('click', () => {
    const wasOpen = !list.hidden;
    input.focus();
    if (wasOpen) close();
    else open();
  });
  options.forEach(option => {
    option.addEventListener('pointerdown', event => event.preventDefault());
    option.addEventListener('click', () => choose(option));
  });
  picker.addEventListener('focusout', event => {
    if (!picker.contains(event.relatedTarget)) close();
  });
  input.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (list.hidden) open();
      if (!matches.length) return;
      const next = event.key === 'ArrowDown'
        ? (activeIndex + 1) % matches.length
        : (activeIndex <= 0 ? matches.length - 1 : activeIndex - 1);
      activate(next);
    } else if (event.key === 'Enter' && !list.hidden && activeIndex >= 0) {
      event.preventDefault();
      choose(matches[activeIndex]);
    } else if (event.key === 'Escape' && !list.hidden) {
      event.preventDefault();
      close();
    }
  });
})();
