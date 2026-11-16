window.ZeNiceSelector = (() => {
  const normalize = text => String(text).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().trim();
  function matches(code, title, query) {
    const q = normalize(query);
    return !q || String(code).padStart(2, '0').includes(q) || normalize(title).includes(q);
  }
  function enhance(select) {
    if (select.dataset.enhanced) { select.dispatchEvent(new Event('nice-refresh')); return; }
    select.dataset.enhanced = 'true';
    const fieldset = document.createElement('fieldset'); fieldset.className = 'nice-selector field-full';
    const legend = document.createElement('legend'); legend.textContent = 'Classes Nice pretendidas';
    const hint = document.createElement('p'); hint.textContent = 'Opcional. Nenhuma selecionada pesquisa em todas as classes.';
    const searchLabel = document.createElement('label'); searchLabel.textContent = 'Buscar classe por número ou descrição';
    const search = document.createElement('input'); search.type = 'search'; search.maxLength = 150; searchLabel.append(search);
    const count = document.createElement('p'); count.setAttribute('role', 'status');
    const chosen = document.createElement('div'); chosen.className = 'nice-chosen';
    const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'secondary-button'; clear.textContent = 'Limpar classes';
    const list = document.createElement('div'); list.className = 'nice-options'; list.setAttribute('role', 'group'); list.setAttribute('aria-label', 'Classes Nice');
    const empty = document.createElement('p'); empty.textContent = 'Nenhuma classe encontrada.'; empty.hidden = true;
    select.parentElement.replaceWith(fieldset); select.hidden = true;
    fieldset.append(legend, hint, select, searchLabel, count, chosen, clear, list, empty);
    function selectedSummary() {
      chosen.replaceChildren();
      const values = [...select.selectedOptions]; count.textContent = `${values.length} classe(s) selecionada(s)`; clear.disabled = !values.length;
      for (const option of values) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary-button';
        button.textContent = `${option.textContent} ×`; button.setAttribute('aria-label', `Remover ${option.textContent}`);
        button.addEventListener('click', () => { option.selected = false; render(); search.focus(); }); chosen.append(button);
      }
    }
    function render() {
      list.replaceChildren();
      for (const option of select.options) {
        if (!matches(option.value, option.textContent, search.value)) continue;
        const label = document.createElement('label'), checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.value = option.value; checkbox.checked = option.selected;
        checkbox.addEventListener('change', () => { option.selected = checkbox.checked; selectedSummary(); select.dispatchEvent(new Event('change', { bubbles: true })); });
        label.append(checkbox, document.createTextNode(option.textContent)); list.append(label);
      }
      empty.hidden = Boolean(list.children.length); selectedSummary();
    }
    clear.addEventListener('click', () => { for (const option of select.options) option.selected = false; render(); search.focus(); });
    search.addEventListener('input', render); select.addEventListener('nice-refresh', render);
    select.form?.addEventListener('reset', () => queueMicrotask(render)); render();
  }
  return { normalize, matches, enhance };
})();
