(() => {
  const api = async (url, options = {}) => {
    options.headers = { ...(options.headers || {}), "Content-Type": "application/json" };
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Não foi possível gerar o acesso");
    return payload;
  };
  const adicionarBotoes = () => document.querySelectorAll("#org-list .user-row").forEach((row) => {
    if (row.querySelector("[data-access]") || !row.dataset.orgId) return;
    row.querySelector("div:last-child")?.insertAdjacentHTML("afterbegin", `<button class="secondary-button" data-access="${row.dataset.orgId}">Gerar acesso</button>`);
  });
  const observer = new MutationObserver(() => {
    document.querySelectorAll("#org-list .user-row").forEach((row) => {
      if (!row.dataset.orgId) row.dataset.orgId = row.querySelector("[data-key]")?.dataset.key || "";
    });
    adicionarBotoes();
  });
  observer.observe(document.querySelector("#org-list"), { childList: true });
  document.querySelector("#org-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-access]");
    if (!button) return;
    try {
      const result = await api(`/v1/admin/saas/organizacoes/${button.dataset.access}/acesso`, { method: "POST" });
      prompt(`Link: ${location.origin}${result.login_path}\nUsuário: ${result.usuario}\nEnvie a senha por canal seguro:`, result.senha_temporaria);
    } catch (error) { alert(error.message); }
  });
})();
