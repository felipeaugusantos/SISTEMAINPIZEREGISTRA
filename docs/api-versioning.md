# Versionamento da API

Todas as APIs públicas e administrativas usam o prefixo `/v1`. Alterações
incompatíveis devem ser publicadas em `/v2`, mantendo `/v1` durante o período
de depreciação documentado.

Regras:

- adicionar campos é compatível;
- remover ou renomear campos exige nova versão;
- endpoints devem declarar o domínio no OpenAPI;
- mudanças de contrato devem incluir teste de compatibilidade;
- o changelog deve registrar a data de descontinuação;
- webhooks devem aceitar apenas versões declaradas no payload.
