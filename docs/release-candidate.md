# Release Candidate — Checklist final

Versão documental: **RC2 / Fase 13**

## Gates de aceite

- [ ] Tenant e objeto: cliente A não acessa dados de B; IDs cruzados retornam 403/404.
- [ ] RPI: reimportação não duplica e vínculo usa número oficial do INPI.
- [ ] Integridade: arquivo vazio, parcial ou corrompido não contamina a base.
- [ ] Propostas: histórico, versão, aceite, IP/data e SLA calculável.
- [ ] CRM: toda interação está na timeline; oportunidade aberta tem responsável e próxima ação.
- [ ] Documentos: pendência obrigatória bloqueia avanço; assinatura é verificável.
- [ ] Financeiro: pagamento vinculado a proposta/oportunidade/processo e sem duplicidade concorrente.
- [ ] Busca/risco: score é explicável e nunca substitui revisão jurídica humana; regressão de recall bloqueia publicação.
- [ ] Interface: telas principais responsivas, sem corte de ações, encoding UTF-8 e paginação funcional.
- [ ] Observabilidade: `/health`, `/health/db`, `/health/rpi`, `/health/queue` e `/metrics` respondem conforme esperado.
- [ ] Segurança: sessões revogáveis, MFA preparado, segredos versionados e permissões por módulo.
- [ ] Auditoria: ações críticas possuem ator, tenant, data, request ID e resultado.
- [ ] Continuidade: backup restaurado em banco descartável e rollback ensaiado.
- [ ] Testes: suíte automatizada verde, incluindo PostgreSQL quando `TEST_DATABASE_URL` estiver configurado.
- [ ] Documentação: README, changelog, instalação, deploy, suporte e matriz de permissões atualizados.

## Evidências mínimas

Anexe ao registro da release o resultado do `pytest -q`, `alembic current`, relatório de smoke tests, hash da imagem/commit, backup validado, evidência de restauração e aprovação visual/homologação.

## Rollback rápido

1. Bloquear novas alterações e preservar logs.
2. Identificar commit/imagem anterior.
3. Restaurar aplicação anterior e validar `/health`.
4. Se necessário, restaurar backup em banco descartável, conferir integridade e somente então executar a restauração produtiva.
5. Reabrir tráfego após smoke tests e registrar o incidente.
