const PAGE_SIZE = 10;

const form = document.querySelector("#search-form");
const input = document.querySelector("#search-input");
const resultsSection = document.querySelector("#results-section");
const resultsList = document.querySelector("#results-list");
const resultsCount = document.querySelector("#results-count");
const statusMessage = document.querySelector("#status-message");
const pagination = document.querySelector("#pagination");
const previousButton = document.querySelector("#previous-page");
const nextButton = document.querySelector("#next-page");
const pageLabel = document.querySelector("#page-label");
const initialNote = document.querySelector("#initial-note");
const submitButton = form.querySelector("button[type='submit']");

const leadSection = document.querySelector("#lead-section");
const leadCta = document.querySelector("#lead-cta");
const leadCtaTerm = document.querySelector("#lead-cta-term");
const openLeadFormButton = document.querySelector("#open-lead-form");
const leadForm = document.querySelector("#lead-form");
const leadTerm = document.querySelector("#lead-term");
const leadSubject = document.querySelector("#lead-subject");
const leadStatus = document.querySelector("#lead-status");
const leadName = document.querySelector("#lead-name");
const leadEmail = document.querySelector("#lead-email");
const leadPhone = document.querySelector("#lead-phone");
const leadWebsite = document.querySelector("#lead-website");
const privacyConsent = document.querySelector("#privacy-consent");
const leadSubmitButton = leadForm.querySelector("button[type='submit']");

let currentQuery = "";
let currentType = "";
let currentOffset = 0;

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

function formatDate(value) {
  if (!value) return "Data não informada";
  return new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

function ownersLabel(owners) {
  if (!owners?.length) return "Titular não informado";
  const names = owners.map((owner) => owner.nome);
  if (names.length <= 2) return names.join(" · ");
  return `${names.slice(0, 2).join(" · ")} e mais ${names.length - 2}`;
}

function resultCard(item) {
  const title = item.titulo || "Título não informado nesta publicação";
  const typeLabel = item.tipo === "marca" ? "Marca" : "Patente";
  return `
    <a class="result-card" href="/processos/${encodeURIComponent(item.numero)}${location.search}">
      <div>
        <div class="result-topline">
          <span class="type-badge ${escapeHtml(item.tipo)}">${typeLabel}</span>
          <span class="process-number">${escapeHtml(item.numero)}</span>
        </div>
        <h3 class="result-title">${escapeHtml(title)}</h3>
        <p class="result-owner">${escapeHtml(ownersLabel(item.titulares))}</p>
      </div>
      <div class="result-meta">
        <p class="result-status">${escapeHtml(item.situacao || "Situação não informada")}</p>
        <p class="result-date">Depósito: ${formatDate(item.data_deposito)}</p>
      </div>
    </a>
  `;
}

function updateUrl() {
  const params = new URLSearchParams({ nome: currentQuery });
  if (currentType) params.set("tipo", currentType);
  if (currentOffset) params.set("pagina", String(currentOffset / PAGE_SIZE + 1));
  history.replaceState(null, "", `/?${params.toString()}`);
}

function revealLeadCta() {
  leadTerm.textContent = currentQuery || "sua marca";
  leadCtaTerm.textContent = currentQuery || "sua marca";
  leadSubject.value = currentQuery;
  leadCta.hidden = false;
  leadSection.hidden = true;
  leadStatus.textContent = "";
}

async function registerLead() {
  const response = await fetch("/v1/leads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nome: leadName.value.trim(),
      email: leadEmail.value.trim(),
      telefone: leadPhone.value.trim(),
      marca: currentQuery,
      processo_numero: null,
      origem: "resultados",
      tipo_interesse: currentType || null,
      aceite_privacidade: privacyConsent.checked,
      website: leadWebsite.value,
    }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = Array.isArray(data?.detail) ? data.detail[0]?.msg : data?.detail;
    throw new Error(detail || "Não foi possível registrar a solicitação.");
  }
  sessionStorage.setItem(
    "inpiLeadContact",
    JSON.stringify({ nome: leadName.value, email: leadEmail.value, telefone: leadPhone.value }),
  );
}

async function search({ scroll = false } = {}) {
  const params = new URLSearchParams({
    nome: currentQuery,
    limite: String(PAGE_SIZE),
    deslocamento: String(currentOffset),
  });
  if (currentType) params.set("tipo", currentType);

  resultsSection.hidden = false;
  initialNote.hidden = true;
  leadCta.hidden = true;
  leadSection.hidden = true;
  resultsList.innerHTML = "";
  resultsCount.textContent = "";
  pagination.hidden = true;
  statusMessage.className = "status-message loading";
  statusMessage.textContent = "Consultando a base do INPI...";
  submitButton.disabled = true;
  updateUrl();

  try {
    const response = await fetch(`/v1/processos?${params.toString()}`);
    if (!response.ok) throw new Error("Não foi possível concluir a consulta.");
    const data = await response.json();

    statusMessage.className = "status-message";
    statusMessage.textContent = data.total
      ? ""
      : "Nenhum processo foi encontrado. Tente outro nome ou altere o tipo de processo.";
    resultsCount.textContent = `${data.total} ${data.total === 1 ? "resultado" : "resultados"}`;
    resultsList.innerHTML = data.itens.map(resultCard).join("");

    const currentPage = Math.floor(data.deslocamento / data.limite) + 1;
    const totalPages = Math.max(1, Math.ceil(data.total / data.limite));
    previousButton.disabled = data.deslocamento === 0;
    nextButton.disabled = data.deslocamento + data.limite >= data.total;
    pageLabel.textContent = `Página ${currentPage} de ${totalPages}`;
    pagination.hidden = data.total <= data.limite;

    revealLeadCta();
    if (scroll) resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    statusMessage.className = "status-message error";
    statusMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  currentQuery = input.value.trim();
  currentType = new FormData(form).get("tipo") || "";
  currentOffset = 0;
  if (currentQuery.length >= 2) search({ scroll: true });
});

openLeadFormButton.addEventListener("click", () => {
  leadSection.hidden = false;
  leadCta.hidden = true;
  leadSection.scrollIntoView({ behavior: "smooth", block: "center" });
  leadName.focus({ preventScroll: true });
});

previousButton.addEventListener("click", () => {
  currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
  search({ scroll: true });
});

nextButton.addEventListener("click", () => {
  currentOffset += PAGE_SIZE;
  search({ scroll: true });
});

leadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  leadStatus.className = "status-message loading";
  leadStatus.textContent = "Enviando sua solicitação...";
  leadSubmitButton.disabled = true;
  try {
    await registerLead();
    leadStatus.className = "status-message";
    leadStatus.textContent = "Solicitação enviada. Em breve um especialista entrará em contato.";
    privacyConsent.checked = false;
  } catch (error) {
    leadStatus.className = "status-message error";
    leadStatus.textContent = error.message;
  } finally {
    leadSubmitButton.disabled = false;
  }
});

const savedContact = JSON.parse(sessionStorage.getItem("inpiLeadContact") || "null");
if (savedContact) {
  leadName.value = savedContact.nome || "";
  leadEmail.value = savedContact.email || "";
  leadPhone.value = savedContact.telefone || "";
}

const initialParams = new URLSearchParams(location.search);
const initialQuery = initialParams.get("nome")?.trim();
if (initialQuery?.length >= 2) {
  const initialType = initialParams.get("tipo") || "";
  const initialPage = Math.max(1, Number(initialParams.get("pagina")) || 1);
  input.value = initialQuery;
  const typeRadio = form.querySelector(`input[name="tipo"][value="${CSS.escape(initialType)}"]`);
  if (typeRadio) typeRadio.checked = true;
  currentQuery = initialQuery;
  currentType = initialType;
  currentOffset = (initialPage - 1) * PAGE_SIZE;
  search();
}
