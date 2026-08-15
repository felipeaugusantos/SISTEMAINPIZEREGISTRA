const observabilityMessage = document.querySelector("#observability-message");
const serviceTarget = document.querySelector("#observability-services");
const metricsTarget = document.querySelector("#observability-metrics");
const rpiTarget = document.querySelector("#observability-rpi");
const updatedTarget = document.querySelector("#observability-updated");

function escapeText(value) { return String(value ?? "—"); }
function statusClass(status) { return ["ok", "saudavel", "healthy"].includes(String(status).toLowerCase()) ? "positive" : "negative"; }
function metric(value, label, detail = "") { return `<article><strong>${escapeText(value)}</strong><span>${escapeText(label)}</span>${detail ? `<small>${escapeText(detail)}</small>` : ""}</article>`; }
function bytes(value) { const n = Number(value || 0); if (!n) return "—"; const units = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = n; while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; } return `${v.toFixed(i ? 1 : 0)} ${units[i]}`; }

async function carregarObservabilidade() {
  observabilityMessage.hidden = false; observabilityMessage.className = "status-message loading"; observabilityMessage.textContent = "Atualizando indicadores…";
  try {
    const response = await fetch("/v1/admin/observabilidade");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Falha ao carregar (${response.status})`);
    serviceTarget.innerHTML = Object.entries(data.servicos).map(([key, value]) => `<article class="observability-service"><span class="observability-dot ${statusClass(value.status)}"></span><strong>${escapeText(key.replace("rpi_sync", "RPI Sync"))}</strong><small>${escapeText(value.status)}</small></article>`).join("");
    metricsTarget.innerHTML = [metric(data.api_24h.requisicoes, "Requisições (24h)"), metric(data.api_24h.erros, "Erros (24h)", `${(Number(data.api_24h.taxa_erros) * 100).toFixed(2)}% de erro`), metric(`${Number(data.api_24h.duracao_media_ms).toFixed(0)} ms`, "Latência média"), metric(data.servicos.banco.conexoes, "Conexões do banco", `${bytes(data.servicos.banco.tamanho_bytes)} usados`), metric(data.pool.em_uso, "Pool em uso", `${data.pool.tamanho ?? "—"} conexões`), metric(data.servicos.redis.pendentes, "Jobs pendentes", `${data.servicos.redis.falhas ?? "—"} falhas`)].join("");
    const rpi = data.rpi;
    rpiTarget.innerHTML = [metric(rpi.status, "Status"), metric(rpi.ultima_rpi, "Última RPI"), metric(rpi.status_integridade, "Integridade"), metric(rpi.idade_horas == null ? "—" : `${Number(rpi.idade_horas).toFixed(1)} h`, "Idade dos dados"), metric(rpi.erros_acumulados, "Falhas acumuladas"), metric((rpi.anomalias || []).length, "Anomalias")].join("");
    updatedTarget.textContent = `Atualizado ${new Date(data.gerado_em).toLocaleTimeString("pt-BR")}`;
    observabilityMessage.hidden = true;
  } catch (error) { observabilityMessage.className = "status-message error"; observabilityMessage.textContent = error.message; }
}
document.querySelector("#observability-refresh").addEventListener("click", carregarObservabilidade);
carregarObservabilidade();
