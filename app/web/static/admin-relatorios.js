(() => {
  "use strict";

  const normalizePermissions = (value) => new Set(Array.isArray(value) ? value : []);
  fetch("/v1/auth/me")
    .then((response) => {
      if (!response.ok) throw new Error("Sessão indisponível");
      return response.json();
    })
    .then((user) => {
      const permissions = normalizePermissions(user.permissoes);
      const unrestricted = user.perfil === "administrador" || user.superadmin;
      document.querySelectorAll("[data-report-permissions]").forEach((card) => {
        const required = card.dataset.reportPermissions.split(",").filter(Boolean);
        if (!unrestricted && !required.some((permission) => permissions.has(permission))) {
          card.remove();
          return;
        }
        card.querySelectorAll("[data-link-permission]").forEach((link) => {
          if (!unrestricted && !permissions.has(link.dataset.linkPermission)) link.remove();
        });
      });
    })
    .catch(() => {
      document.querySelectorAll("[data-report-permissions]").forEach((card) => card.remove());
    });
})();
