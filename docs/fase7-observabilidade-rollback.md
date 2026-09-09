# Fase 7 — Observabilidade e rollback

## Painel técnico (`GET /v1/admin/observabilidade/painel-tecnico`, Tech)

| Pedido | De onde vem |
| --- | --- |
| Versão da API, worker e rpi-sync | `settings.app_version` (já exportado como `APP_VERSION` pelo `docker/deploy.sh`, igual pros três serviços) |
| Commit em execução | `settings.git_sha` — novo: `ARG GIT_SHA` (já existia, usado só no `LABEL` da imagem) promovido a `ENV GIT_SHA` no `Dockerfile`, lido pelo processo em runtime. Os três serviços sempre deployam do mesmo commit ao mesmo tempo (checado por `docker/deploy.sh`), então API/worker/rpi-sync mostram o mesmo valor — não é auto-relatado individualmente. |
| Migration atual | `SELECT version_num FROM alembic_version` direto no banco |
| Saúde dos containers | API: responde = viva. Banco/Redis: já existiam em `GET /v1/admin/observabilidade`. RPI Sync: `RpiSyncEstado.heartbeat_em` (já existia). Worker: **novo** `ProcessoHeartbeat` — o worker não tem endpoint HTTP próprio, então grava heartbeat a cada 30s no loop de manutenção (`app/worker.py::_loop_manutencao`). Sem acesso ao socket do Docker em nenhum momento — tudo por sinal de vida escrito no próprio banco. |
| Erros por versão | Janela de tempo entre `implantada_em` de cada versão publicada e a próxima (ou agora, pra mais recente), contra `EventoOperacional`. Mesma limitação documentada em `docs/fase5-liberacao-gradual.md` ("impacto nos módulos existentes"): proxy por janela de tempo, não uma marcação direta de "qual versão gerou este erro". |
| Feature flags ativas | `FeatureFlag` onde `ativo=true` (Fase 4/5) |
| Organizações afetadas | `FeatureFlagEvento` (Fase 5) tipo erro/falha_integração, últimas 24h, agrupado por organização |
| Resultado do último deploy | Última `VersaoSistema` publicada — versão, commit, migration, quem publicou, evidências de teste aprovadas |

## Rollback

### Feature flag desligada imediatamente

`POST /v1/admin/feature-flags/{codigo}/desligar` (Tech, não exige superadmin
nem motivo — é a ação de emergência). Kill-switch direto
(`FeatureFlag.ativo=False`, mecanismo da Fase 4), corta a flag para
**todas** as organizações na hora. Gera `EventoAuditoria` (`FLAG_DESLIGAR`).

Diferente de `POST /v1/admin/feature-flags/{codigo}/interromper` (Fase 5):
aquele recua só um estágio e exige motivo — para quando dá tempo de
registrar o porquê. Este endpoint é o botão de pânico.

### Rollback de aplicação — restrito à equipe técnica (por design)

Não existe (nem deveria existir) um botão de "reverter a aplicação inteira"
self-service na interface — a Fase 7 pede explicitamente que isso
**continue restrito**. O procedimento é manual, via SSH + git, e usa a
mesma ferramenta de sempre:

```bash
ssh <vps> "cd /opt/zeregistra && git checkout <commit-anterior> && ./docker/deploy.sh"
```

(ou `git revert` do commit problemático seguido de `deploy.sh`, preferível
a `checkout` quando o objetivo é manter o histórico linear). Só quem tem a
chave SSH da VPS (equipe Tech) consegue rodar isso — a restrição já é
garantida pelo controle de acesso à infraestrutura, não por código da
aplicação.

**Auditoria da reversão**: como esse rollback roda fora da aplicação
(direto no shell da VPS), a aplicação não pode detectá-lo sozinha. A
ação que registra a reversão é **arquivar a versão problemática**
(`POST /v1/admin/versoes-sistema/{id}/arquivar`, endpoint já existente
desde a Fase 1) — gera `EventoAuditoria` (`ARQUIVAR_RELEASE`) com o
motivo do arquivamento, e o painel técnico deixa de contar aquela versão
como "última implantada" assim que uma nova é publicada. O painel tem um
link direto pra central de atualizações pra fazer isso.

### Migration: procedimento documentado

Toda migration deste projeto já segue um padrão obrigatório, verificado
neste mesmo fluxo de trabalho antes de qualquer deploy (não é só uma
recomendação — é testado em Docker isolado a cada mudança):

1. `upgrade()` e `downgrade()` sempre implementados os dois, nunca só um.
2. Validação em ciclo completo antes do deploy: `alembic upgrade head` →
   `alembic downgrade -1` → `alembic upgrade head` num banco de teste
   efêmero (`docker compose --profile test run --rm test uv run alembic
   ...`) — se o downgrade não funcionar de verdade, o deploy não
   acontece.
3. Migrations desta fase (e das Fases 4-6) só **adicionam** colunas/
   tabelas com `server_default` seguro — nunca removem ou renomeiam algo
   que dados existentes dependem, o que torna o downgrade trivialmente
   seguro (só desfaz a adição, não apaga dado de negócio).

### Dados criados pela versão precisam ser preservados quando possível

Política já em prática nas migrations desta sessão (Fases 4-7): colunas
novas com `server_default`, sem `DROP`/`ALTER TYPE` destrutivo em dado
existente. Um `downgrade()` que remove uma coluna nova (ex.:
`estagio_rollout`) não perde dado de negócio -- só reverte a própria
adição. Nenhuma migration desta fase apaga linha nem trunca tabela.
Quando uma migration futura precisar de uma mudança genuinamente
destrutiva, o procedimento é: backup antes (já automático, ver
`docker/deploy.sh`, "backup antes da migration") + migration em duas
etapas (aditiva num deploy, remoção só num deploy seguinte, depois de
confirmar que nada mais lê a coluna antiga).

## Critério de aceite

"A equipe identifica rapidamente uma regressão e consegue limitar seu
impacto": painel técnico numa tela só (versão/commit/migration/saúde/
erros por versão/flags ativas/organizações afetadas) + duas ações de
contenção imediata (desligar flag, arquivar versão problemática) sem
sair da tela de observabilidade.
