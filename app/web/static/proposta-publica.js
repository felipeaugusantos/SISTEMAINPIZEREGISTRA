// Página pública da proposta (/propostas/{token}).
// Achado 18.7 da auditoria fina de Propostas (29/09/2026): abrir o link (GET)
// já marcava a proposta como "visualizada" -- antivírus e pré-visualizadores
// de e-mail abrem os links sozinhos e inflavam a métrica. A marcação passou
// a ser um POST disparado só quando a página roda no navegador de verdade.
const alvoVisualizacao = document.querySelector("[data-marcar-visualizada]");
if (alvoVisualizacao) {
  fetch(alvoVisualizacao.dataset.marcarVisualizada, { method: "POST", credentials: "same-origin", keepalive: true }).catch(() => {});
}
