(() => {
  function create({ id, previous, next, container, sizes = [20, 50, 100], get, set, load }) {
    const prev = document.querySelector(previous), following = document.querySelector(next);
    const nav = container ? document.querySelector(container) : prev?.closest('nav, footer') || prev?.parentElement;
    if (!nav || !prev || !following) return null;
    const summary = nav.querySelector('span');
    nav.classList.add('admin-pagination');
    const initial = get();
    const availableSizes = [...new Set([initial.limit, ...sizes])].sort((a, b) => a - b);
    const controls = document.createElement('div');
    controls.className = 'admin-pagination-controls';
    controls.innerHTML = `<label>Itens por página <select id="${id}-page-size">${availableSizes.map(n => `<option value="${n}">${n}</option>`).join('')}</select></label><button id="${id}-first" type="button" class="secondary-button">Primeira</button><label>Página <input id="${id}-page-number" type="number" min="1" value="1" inputmode="numeric"></label><span id="${id}-page-count" aria-live="polite">de 1</span><button id="${id}-go" type="button" class="secondary-button">Ir</button><button id="${id}-last" type="button" class="secondary-button">Última</button>`;
    nav.prepend(controls);
    const notice = document.createElement('span');
    notice.className = 'admin-pagination-error'; notice.setAttribute('role', 'alert'); notice.hidden = true;
    nav.append(notice);
    const size = controls.querySelector('select'), number = controls.querySelector('input');
    const first = controls.querySelector(`#${id}-first`), last = controls.querySelector(`#${id}-last`), go = controls.querySelector(`#${id}-go`);
    if (summary) controls.firstElementChild.after(summary);
    last.before(prev, following);
    let total = 0, more = false, pending = false, revision = 0;
    const pages = () => total == null ? null : Math.max(1, Math.ceil(total / get().limit));
    function draw() {
      const current = get(), page = Math.floor(current.offset / current.limit) + 1, count = pages();
      size.value = String(current.limit); number.value = String(page);
      if (count == null) number.removeAttribute('max'); else number.max = String(count);
      controls.querySelector(`#${id}-page-count`).textContent = count == null ? 'total não informado' : `de ${count}`;
      size.disabled = number.disabled = go.disabled = pending;
      first.disabled = prev.disabled = pending || page <= 1;
      last.disabled = pending || count == null || page >= count;
      following.disabled = pending || (count == null ? !more : page >= count);
      nav.setAttribute('aria-busy', String(pending));
    }
    async function move(page, limit = get().limit) {
      if (pending) return;
      if (!availableSizes.includes(limit)) { draw(); return; }
      const previousState = { ...get() }, oldRevision = revision;
      const count = total == null ? Infinity : Math.max(1, Math.ceil(total / limit));
      const requested = Number(page);
      const selected = Math.min(count, Math.max(1, Number.isFinite(requested) ? Math.trunc(requested) || 1 : 1));
      const offset = (selected - 1) * limit;
      if (offset === previousState.offset && limit === previousState.limit) { draw(); return; }
      pending = true; notice.hidden = true;
      set({ offset, limit }); draw();
      try {
        await load();
        // Algumas telas apresentam o erro por conta própria e não propagam a rejeição.
        if (revision === oldRevision) throw new Error('Não foi possível mudar de página. Tente novamente.');
      } catch (error) {
        set(previousState); notice.textContent = error instanceof TypeError ? 'Não foi possível conectar. Tente novamente.' : error.message;
        notice.hidden = false;
      } finally {
        pending = false; draw();
        const countNow = pages();
        if (notice.hidden && countNow != null && get().offset >= countNow * get().limit) move(countNow);
      }
    }
    size.addEventListener('change', () => move(1, Number(size.value)));
    first.addEventListener('click', () => move(1));
    last.addEventListener('click', () => { if (pages() != null) move(pages()); });
    go.addEventListener('click', () => move(number.value));
    number.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); move(number.value); } });
    // Usa o mesmo carregador da tela e impede que os handlers antigos dupliquem a consulta.
    [prev, following].forEach((button, index) => button.addEventListener('click', event => {
      event.preventDefault(); event.stopImmediatePropagation();
      if (!button.disabled) move(Math.floor(get().offset / get().limit) + (index ? 2 : 0));
    }, true));
    draw();
    return { update(value, hasMore = !following.disabled) {
      total = value == null ? null : Math.max(0, Number(value) || 0); more = hasMore; revision++; draw();
      if (!pending && pages() != null && get().offset >= pages() * get().limit) setTimeout(() => move(pages()), 0);
    } };
  }
  window.ZRPagination = { create };
})();
