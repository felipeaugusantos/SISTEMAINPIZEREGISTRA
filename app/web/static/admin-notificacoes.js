function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value ?? "";
  return element.innerHTML;
}

const state = { itens: [], apenasPendentes: true };
const list = document.querySelector("#notif-page-list");
const message = document.querySelector("#notif-page-message");
const filtroTodas = document.querySelector("#notif-filter-todas");

function severidadeClasse(item) {
  return { info: "info", aviso: "warn", critico: "danger", critica: "danger" }[item.severidade] || "info";
}

// Achado do usuário (21/09/2026): "mensagem_portal" (mensagem não lida do
// cliente) virava "Sistema" aqui -- o rótulo era um binário jurídico/sistema
// que nunca previa uma terceira fonte.
const FONTE_LABELS = { juridico: "Jurídico", sistema: "Sistema", mensagem_portal: "Mensagem" };
function linha(item) {
  const sev = severidadeClasse(item);
  const fonte = FONTE_LABELS[item.fonte] || "Sistema";
  const acaoLabel = item.lida ? "Marcar como não lida" : "Marcar como lida";
  const acaoAlvo = item.lida ? "nao-lida" : "lida";
  return `<li class="${item.lida ? "notif-lida" : ""}">
    <a href="${item.url || "#"}" data-nav="1">
      <span class="notif-dot sev-${sev}" aria-hidden="true"></span>
      <div>
        <strong>${escapeHtml(item.titulo)}</strong>
        <p>${escapeHtml(item.mensagem)}</p>
        <small>${fonte} · ${formatDate(item.criado_em)}${item.lida ? " · Lida" : ""}</small>
      </div>
    </a>
    ${item.fonte === "mensagem_portal" && item.lida ? "" : `<button type="button" class="secondary-button notif-toggle" data-fonte="${item.fonte}" data-id="${item.id}" data-alvo="${acaoAlvo}">${acaoLabel}</button>`}
  </li>`;
}

function render() {
  list.innerHTML = state.itens.length
    ? state.itens.map(linha).join("")
    : `<li class="notif-empty">${state.apenasPendentes ? "Nenhuma notificação pendente. 🎉" : "Nenhuma notificação encontrada."}</li>`;
}

async function carregar() {
  message.textContent = "Carregando notificações…";
  message.className = "status-message loading";
  try {
    const resposta = await fetch(`/v1/admin/notificacoes?todas=${!state.apenasPendentes}&limite=200`);
    if (!resposta.ok) throw new Error("Falha ao carregar notificações");
    const dados = await resposta.json();
    state.itens = dados.itens;
    render();
    if (dados.total_itens === 0) {
      message.textContent = state.apenasPendentes ? "Nenhuma notificação pendente. 🎉" : "Nenhuma notificação encontrada.";
    } else if (state.apenasPendentes) {
      message.textContent = `${dados.total} notificaç${dados.total === 1 ? "ão" : "ões"} pendente(s).`;
    } else {
      message.textContent = `${dados.total_itens} notificaç${dados.total_itens === 1 ? "ão" : "ões"} no total, ${dados.total} pendente(s).`;
    }
    message.className = "status-message success";
  } catch (erro) {
    message.textContent = erro.message;
    message.className = "status-message error";
  }
}

let acaoPendente = false;
async function alterarNotificacoes(url) {
  if (acaoPendente) return;
  acaoPendente = true;
  const controles = [...document.querySelectorAll('.notif-toggle, #notif-marcar-todas-lidas, #notif-marcar-todas-nao-lidas, #notif-filter-todas')]
    .map(elemento => ({ elemento, disabled: elemento.disabled }));
  controles.forEach(({ elemento }) => { elemento.disabled = true; });
  message.textContent = "Atualizando notificações…";
  message.className = "status-message loading";
  try {
    const resposta = await fetch(url, { method: "POST" });
    if (!resposta.ok) {
      const dados = await resposta.json().catch(() => ({}));
      throw new Error(typeof dados.detail === "string" ? dados.detail : "Não foi possível atualizar as notificações.");
    }
    await carregar();
  } catch (erro) {
    message.textContent = erro.message || "Não foi possível atualizar as notificações.";
    message.className = "status-message error";
  } finally {
    acaoPendente = false;
    controles.forEach(({ elemento, disabled }) => { elemento.disabled = disabled; });
  }
}

list.addEventListener("click", async (event) => {
  const botao = event.target.closest(".notif-toggle");
  if (botao) {
    event.preventDefault();
    const { fonte, id, alvo } = botao.dataset;
    await alterarNotificacoes(`/v1/admin/notificacoes/${encodeURIComponent(fonte)}/${encodeURIComponent(id)}/${alvo}`);
    return;
  }
  const link = event.target.closest('a[data-nav="1"]');
  if (link && link.getAttribute("href") && link.getAttribute("href") !== "#") {
    window.location.href = link.getAttribute("href");
  }
});

filtroTodas.addEventListener("change", () => {
  state.apenasPendentes = !filtroTodas.checked;
  carregar();
});

document.querySelector("#notif-marcar-todas-lidas").addEventListener("click", async () => {
  await alterarNotificacoes("/v1/admin/notificacoes/marcar-todas?lida=true");
});

document.querySelector("#notif-marcar-todas-nao-lidas").addEventListener("click", async () => {
  await alterarNotificacoes("/v1/admin/notificacoes/marcar-todas?lida=false");
});

carregar();
