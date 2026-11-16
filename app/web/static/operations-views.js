// Local, structured preferences only. Contact search text is never persisted.
window.ZeLeadViews = (() => {
  const allowed = ['lead-status-filter', 'lead-owner-filter', 'lead-origin-filter', 'lead-date-start', 'lead-date-end', 'lead-marketing-filter', 'lead-risk-filter', 'lead-sort'];
  function scope(user) {
    const org = user.organizacao?.id;
    return Number.isInteger(user.id) && Number.isInteger(org) ? `zr_lead_views:v1:${org}:${user.id}` : null;
  }
  function sanitize(value) {
    const fields = Object.fromEntries(allowed.map(id => [id, String(value.fields?.[id] || '').slice(0, 40)]));
    return { fields, size: ['20', '50', '100', '200'].includes(value.size) ? value.size : '50',
      offset: Math.max(0, Math.min(10000, Number(value.offset) || 0)),
      priority: ['atrasadas', 'sem_responsavel', 'sem_proxima_acao'].includes(value.priority) ? value.priority : '',
      view: value.view === 'archived' ? 'archived' : 'researches' };
  }
  function read(storage, key) {
    const items = JSON.parse(storage.getItem(key) || '[]');
    if (!Array.isArray(items)) throw Error('Preferências inválidas.');
    return items.slice(0, 20).filter(x => typeof x.id === 'string' && typeof x.name === 'string')
      .map(x => ({ id: x.id, name: x.name.slice(0, 60), value: sanitize(x.value || {}) }));
  }
  async function init({ root, filters, capture, apply, reload, clearPriority, message }) {
    let key, views = [];
    const list = root.querySelector('[data-views-list]'), name = root.querySelector('[data-view-name]');
    const notify = text => { root.querySelector('[data-views-status]').textContent = text; };
    function render(selected = '') {
      list.replaceChildren(new Option('Selecione uma visão', ''));
      for (const view of views) list.add(new Option(view.name, view.id));
      list.value = selected;
    }
    function persist(next, selected = '') {
      try { localStorage.setItem(key, JSON.stringify(next)); views = next; render(selected); return true; }
      catch (_) { notify('Não foi possível salvar neste navegador. Suas visões anteriores foram preservadas.'); return false; }
    }
    try {
      const response = await fetch('/v1/auth/me');
      if (!response.ok) throw Error();
      key = scope(await response.json());
      if (!key) throw Error();
      views = read(localStorage, key); render();
    } catch (_) { root.querySelectorAll('input, select, button').forEach(el => { el.disabled = true; }); notify('Visões indisponíveis: conta não identificada ou armazenamento bloqueado. Os filtros continuam disponíveis.'); }
    root.addEventListener('click', event => {
      const action = event.target.closest('[data-view-action]')?.dataset.viewAction;
      if (!action || !key) return;
      const chosen = views.find(v => v.id === list.value);
      if (action === 'apply') {
        if (!chosen) return notify('Selecione uma visão.');
        apply(sanitize(chosen.value)); notify('Visão aplicada. O texto livre da busca não faz parte das visões.'); return;
      }
      if (action === 'delete') {
        if (!chosen) return notify('Selecione uma visão.');
        if (persist(views.filter(v => v.id !== chosen.id))) notify('Visão excluída.'); return;
      }
      const label = name.value.trim().slice(0, 60);
      if (!label) { notify('Informe um nome para a visão, sem dados de clientes.'); name.focus(); return; }
      if (views.some(v => v.name.toLocaleLowerCase() === label.toLocaleLowerCase() && (action !== 'rename' || v.id !== chosen?.id))) return notify('Já existe uma visão com esse nome.');
      if (action === 'rename') {
        if (!chosen) return notify('Selecione uma visão.');
        if (persist(views.map(v => v.id === chosen.id ? { ...v, name: label } : v), chosen.id)) notify('Visão renomeada.'); return;
      }
      if (views.length >= 20) return notify('Limite de 20 visões. Exclua uma antes de criar outra.');
      const id = [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
      if (persist([...views, { id, name: label, value: sanitize(capture()) }], id)) notify('Visão salva sem o texto livre da busca.');
    });
    list.addEventListener('change', () => { name.value = views.find(v => v.id === list.value)?.name || ''; });
    const chips = document.querySelector('#lead-active-filters');
    return function update(priority = '') {
      chips.replaceChildren();
      const ids = ['lead-search', ...allowed.filter(id => id !== 'lead-sort')];
      for (const id of ids) {
        const field = filters.querySelector(`#${id}`);
        if (!field?.value) continue;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary-button filter-chip';
        const label = field.closest('label')?.querySelector('span')?.textContent || id;
        const value = field.tagName === 'SELECT' ? field.selectedOptions[0]?.textContent : field.value;
        button.textContent = `${label}: ${value} ×`; button.setAttribute('aria-label', `Remover filtro ${label}: ${value}`);
        button.addEventListener('click', () => { field.value = ''; reload(); }); chips.append(button);
      }
      if (priority) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary-button';
        button.textContent = `Prioridade: ${priority.replaceAll('_', ' ')} ×`;
        button.addEventListener('click', () => { clearPriority(); reload(); }); chips.append(button);
      }
      if (!chips.children.length) chips.textContent = 'Nenhum filtro ativo.';
    };
  }
  return { scope, sanitize, read, init };
})();
