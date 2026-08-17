const rpiForm = document.querySelector("#rpi-query-form");
const rpiMessage = document.querySelector("#rpi-query-message");
const rpiResults = document.querySelector("#rpi-query-results");
const rpiPagination = document.querySelector("#rpi-query-pagination");
const revistaList = document.querySelector("#rpi-revista-list");
const revistaPage = document.querySelector("#rpi-revista-page");
let revistaOffset = 0;
let revistaTotal = 0;
const rpiPageSize = 20;
let rpiOffset = 0;
const situacaoField = document.createElement("label");
situacaoField.className = "rpi-situacao-field";
situacaoField.innerHTML = '<span>Situação no INPI</span><select name="situacao_inpi"><option value="">Todas as situações</option><option value="em_tramitacao">Em tramitação</option><option value="exigencia">Exigência</option><option value="sobrestado">Sobrestado</option><option value="recurso">Recurso / 2ª instância</option><option value="deferido">Deferido</option><option value="registrado">Registro concedido</option><option value="indeferido">Indeferido</option><option value="encerrado">Arquivado / Extinto</option><option value="revisar">Não classificada</option></select>';
rpiForm.elements.numero_rpi.before(situacaoField);
situacaoField.querySelector("select").addEventListener("change", () => {
  if (!rpiForm.elements.numero_rpi.value) return;
  rpiOffset = 0;
  rpiLoad();
});
const rpiEsc = value => String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const rpiDate = value => value ? new Date(`${value}T12:00:00`).toLocaleDateString("pt-BR") : "-";
async function loadRevistas() {
  const response = await fetch(`/v1/admin/rpi/consulta/revistas?limite=10&deslocamento=${revistaOffset}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Não foi possível carregar as revistas RPI.");
  revistaTotal = data.total;
  revistaList.innerHTML = data.itens.length ? data.itens.map(item => `<button type="button" class="rpi-revista" data-rpi="${item.numero_rpi}"><strong>RPI ${item.numero_rpi}</strong><span>${item.registros_processados || 0} registros de marcas</span></button>`).join("") : "<p class=\"rpi-empty\">Nenhuma revista importada.</p>";
  revistaPage.textContent = `Página ${Math.floor(revistaOffset / 10) + 1} de ${Math.max(1, Math.ceil(data.total / 10))}`;
  document.querySelector("#rpi-revista-prev").disabled = revistaOffset === 0;
  document.querySelector("#rpi-revista-next").disabled = revistaOffset + 10 >= data.total;
}
async function rpiLoad() {
  const params = new URLSearchParams(new FormData(rpiForm));
  params.set("limite", rpiPageSize); params.set("deslocamento", rpiOffset);
  [...params.entries()].forEach(([key, value]) => { if (!String(value).trim()) params.delete(key); });
  try {
    const response = await fetch(`/v1/admin/rpi/consulta?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Não foi possível consultar a RPI.");
    rpiMessage.textContent = ""; rpiMessage.className = "status-message";
    document.querySelector("#rpi-query-count").textContent = data.total == null ? `Publicações da RPI ${rpiForm.elements.numero_rpi.value}` : `${data.total} publicação(ões)`;
    rpiResults.innerHTML = data.itens.length ? data.itens.map(item => `<article class="rpi-result"><div><span class="rpi-result-meta">RPI ${rpiEsc(item.numero_rpi)} · ${rpiDate(item.data_rpi)}${item.codigo_despacho ? ` · ${rpiEsc(item.codigo_despacho)}` : ""}</span><h3>${rpiEsc(item.descricao || "Publicação sem descrição")}</h3><p>${rpiEsc(item.titulo || "Marca sem título")} · ${rpiEsc(item.numero)}</p><small>${rpiEsc(item.procurador || "Procurador não informado")}</small></div><a class="secondary-button" href="/processos/${encodeURIComponent(item.numero)}" target="_blank" rel="noopener">Abrir processo</a></article>`).join("") : "<p class=\"rpi-empty\">Nenhuma publicação encontrada para os filtros informados.</p>";
    const totalPages = data.total == null ? null : Math.max(1, Math.ceil(data.total / rpiPageSize));
    document.querySelector("#rpi-query-page").textContent = totalPages ? `Página ${Math.floor(rpiOffset / rpiPageSize) + 1} de ${totalPages}` : `Página ${Math.floor(rpiOffset / rpiPageSize) + 1}`;
    document.querySelector("#rpi-query-prev").disabled = rpiOffset === 0;
    document.querySelector("#rpi-query-next").disabled = data.total == null ? !data.tem_mais : rpiOffset + rpiPageSize >= data.total;
    rpiPagination.hidden = data.total == null ? !data.itens.length : data.total <= rpiPageSize;
  } catch (error) { const message = error instanceof TypeError ? "Não foi possível conectar à API da Consulta RPI." : rpiEsc(error.message); rpiResults.innerHTML = `<p class="rpi-empty rpi-load-error">${message} <button type="button" id="rpi-retry" class="secondary-button">Tentar novamente</button></p>`; document.querySelector("#rpi-retry")?.addEventListener("click", () => rpiLoad()); rpiPagination.hidden = true; rpiMessage.textContent = ""; rpiMessage.className = "status-message"; }
}
rpiForm.addEventListener("submit", event => { event.preventDefault(); if (!rpiForm.elements.numero_rpi.value) return; rpiOffset = 0; rpiLoad(); });
document.querySelector("#rpi-query-clear").addEventListener("click", () => { rpiForm.reset(); rpiForm.elements.numero_rpi.value = ""; document.querySelector("#rpi-open-records").disabled = true; document.querySelectorAll(".rpi-revista.selected").forEach(item => item.classList.remove("selected")); rpiOffset = 0; rpiResults.replaceChildren(); rpiPagination.hidden = true; });
revistaList.addEventListener("click", event => { const button = event.target.closest(".rpi-revista"); if (!button) return; document.querySelectorAll(".rpi-revista.selected").forEach(item => item.classList.remove("selected")); button.classList.add("selected"); rpiForm.elements.numero_rpi.value = button.dataset.rpi; document.querySelector("#rpi-open-records").disabled = false; });
document.querySelector("#rpi-revista-prev").addEventListener("click", () => { revistaOffset = Math.max(0, revistaOffset - 10); loadRevistas().catch(error => { rpiMessage.textContent = error.message; rpiMessage.className = "status-message error"; }); });
document.querySelector("#rpi-revista-next").addEventListener("click", () => { revistaOffset += 10; loadRevistas().catch(error => { rpiMessage.textContent = error.message; rpiMessage.className = "status-message error"; }); });
document.querySelector("#rpi-query-prev").addEventListener("click", () => { rpiOffset = Math.max(0, rpiOffset - rpiPageSize); rpiLoad(); });
document.querySelector("#rpi-query-next").addEventListener("click", () => { rpiOffset += rpiPageSize; rpiLoad(); });
loadRevistas().catch(error => { rpiMessage.textContent = error.message; rpiMessage.className = "status-message error"; });
