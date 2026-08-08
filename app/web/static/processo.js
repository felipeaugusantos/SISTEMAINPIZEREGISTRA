const loading = document.querySelector("#detail-loading");
const errorSection = document.querySelector("#detail-error");
const errorMessage = document.querySelector("#detail-error-message");
const detail = document.querySelector("#process-detail");
const backLink = document.querySelector("#back-link");

function text(selector, value) {
  document.querySelector(selector).textContent = value;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function formatDate(value) {
  if (!value) return "Não informada";
  return new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

function formatDateTime(value) {
  if (!value) return "Não informada";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "long",
    timeZone: "America/Sao_Paulo",
  }).format(new Date(value));
}

function renderHolders(holders) {
  if (!holders?.length) {
    return '<p class="empty-detail">Nenhum titular informado nas publicações importadas.</p>';
  }
  return holders
    .map(
      (holder) => `
        <div class="holder-card">
          <span class="holder-initial" aria-hidden="true">${escapeHtml(holder.nome.charAt(0))}</span>
          <div>
            <strong>${escapeHtml(holder.nome)}</strong>
            <small>${escapeHtml(holder.pais || "País não informado")}</small>
          </div>
        </div>
      `,
    )
    .join("");
}

function renderMovements(movements) {
  if (!movements?.length) {
    return '<li class="empty-detail">Nenhuma movimentação encontrada nas RPIs importadas.</li>';
  }
  return movements
    .map(
      (movement, index) => `
        <li class="timeline-item ${index === 0 ? "current" : ""}">
          <div class="timeline-marker" aria-hidden="true"></div>
          <div class="timeline-content">
            <div class="timeline-meta">
              <time datetime="${escapeHtml(movement.data_rpi)}">${formatDate(movement.data_rpi)}</time>
              <span>RPI ${escapeHtml(String(movement.numero_rpi))}</span>
              ${movement.codigo_despacho ? `<span>Código ${escapeHtml(movement.codigo_despacho)}</span>` : ""}
            </div>
            <strong>${escapeHtml(movement.descricao)}</strong>
          </div>
        </li>
      `,
    )
    .join("");
}

function valueOrNotInformed(value) {
  return value || "Não informado nesta publicação";
}

function renderTrademarkDetails(item) {
  const section = document.querySelector("#trademark-details");
  if (item.tipo !== "marca") return;

  section.hidden = false;
  text("#detail-presentation", valueOrNotInformed(item.apresentacao));
  text("#detail-nature", valueOrNotInformed(item.natureza));
  text("#detail-word-element", valueOrNotInformed(item.elemento_nominativo));
  text("#detail-attorney", valueOrNotInformed(item.procurador));

  const image = document.querySelector("#detail-brand-image");
  const placeholder = document.querySelector("#brand-image-placeholder");
  if (item.imagem_url) {
    image.src = item.imagem_url;
    image.hidden = false;
    placeholder.hidden = true;
  }

  const classifications = item.classificacoes || [];
  const vienna = classifications.filter((entry) => entry.sistema === "vienna");
  const nice = classifications.filter((entry) => entry.sistema === "nice");
  const classificationsSection = document.querySelector("#classifications");

  if (vienna.length) {
    document.querySelector("#vienna-section").hidden = false;
    document.querySelector("#vienna-list").innerHTML = vienna
      .map(
        (entry) => `<span>${escapeHtml(entry.codigo)}${entry.edicao ? ` · ${escapeHtml(entry.edicao)}ª edição` : ""}</span>`,
      )
      .join("");
  }

  if (nice.length) {
    document.querySelector("#nice-section").hidden = false;
    document.querySelector("#nice-list").innerHTML = nice
      .map(
        (entry) => `
          <article class="nice-card">
            <div class="nice-card-heading">
              <strong>Classe ${escapeHtml(entry.codigo)}</strong>
              ${entry.status ? `<span>${escapeHtml(entry.status)}</span>` : ""}
            </div>
            <p>${escapeHtml(entry.especificacao || "Especificação não informada nesta publicação")}</p>
          </article>
        `,
      )
      .join("");
  }

  classificationsSection.hidden = !vienna.length && !nice.length;
}

function renderProcess(item) {
  const typeLabel = item.tipo === "marca" ? "Marca" : "Patente";
  const badge = document.querySelector("#detail-type");
  badge.textContent = typeLabel;
  badge.classList.add(item.tipo);

  text("#detail-number", item.numero);
  text("#detail-title", item.titulo || "Título não informado nesta publicação");
  text("#detail-status", item.situacao || "Situação não informada");
  text("#detail-source", `Fonte mais recente: ${item.fonte}`);
  text("#detail-deposit-date", formatDate(item.data_deposito));
  text("#detail-type-label", typeLabel);
  text("#detail-updated-at", formatDateTime(item.atualizado_em));
  text("#detail-number-copy", item.numero);
  text(
    "#movements-count",
    `${item.movimentacoes.length} ${item.movimentacoes.length === 1 ? "movimentação" : "movimentações"}`,
  );
  document.querySelector("#holders-list").innerHTML = renderHolders(item.titulares);
  document.querySelector("#movements-list").innerHTML = renderMovements(item.movimentacoes);
  renderTrademarkDetails(item);
  document.title = `${item.titulo || item.numero} — Zé Registra`;
}

async function loadProcess() {
  const number = decodeURIComponent(location.pathname.split("/").filter(Boolean).at(-1) || "");
  backLink.href = location.search ? `/${location.search}` : "/";

  try {
    const response = await fetch(`/v1/processos/${encodeURIComponent(number)}`);
    if (response.status === 404) throw new Error("O processo solicitado não está na base importada.");
    if (!response.ok) throw new Error("Não foi possível consultar o processo neste momento.");
    const item = await response.json();
    renderProcess(item);
    detail.hidden = false;
  } catch (error) {
    errorMessage.textContent = error.message;
    errorSection.hidden = false;
  } finally {
    loading.hidden = true;
  }
}

loadProcess();
