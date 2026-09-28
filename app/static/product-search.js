// Buscar sin recargar la página; solo se muestra la respuesta más reciente.
(() => {
  const form = document.querySelector('#product-search-form');
  const input = document.querySelector('#product-search');
  const results = document.querySelector('#product-results');
  const status = document.querySelector('#product-search-status');
  let timer, controller, revision = 0;

  async function refresh(currentRevision) {
    const url = new URL(form.action, window.location.href);
    url.searchParams.set('q', input.value.trim());
    url.searchParams.set('partial', '1');
    controller = new AbortController();
    try {
      const response = await fetch(url, {signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error('Search failed');
      const html = await response.text();
      if (currentRevision !== revision) return;
      results.innerHTML = html;
      status.textContent = '';
    } catch (error) {
      if (currentRevision !== revision || error.name === 'AbortError') return;
      status.textContent = 'No se pudo actualizar la búsqueda. Presione Buscar para reintentar.';
      // Evitar mostrar resultados de una búsqueda anterior como si fueran actuales.
      results.replaceChildren();
    } finally {
      if (currentRevision === revision) {
        results.removeAttribute('aria-busy');
        results.inert = false;
      }
    }
  }

  function search(immediate = false) {
    clearTimeout(timer);
    if (controller) controller.abort();
    const currentRevision = ++revision;
    results.setAttribute('aria-busy', 'true');
    results.inert = true;
    status.textContent = 'Buscando…';
    if (immediate) refresh(currentRevision);
    else timer = setTimeout(() => refresh(currentRevision), 150);
  }

  input.addEventListener('input', () => search());
  form.addEventListener('submit', event => {
    event.preventDefault();
    search(true);
    input.focus();
    input.select(); // La próxima lectura del escáner reemplaza el código anterior.
  });
  input.focus();
  input.select();
})();
