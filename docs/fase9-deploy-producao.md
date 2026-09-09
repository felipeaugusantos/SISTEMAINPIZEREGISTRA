# Fase 9 — Deploy (ordem recomendada, produção)

Runbook oficial de deploy em produção. Cada passo abaixo já tem
ferramenta própria (a maioria construída nas Fases 0-8 desta mesma
sessão) — aqui fica documentado o que é **automático** (roda sozinho
dentro de `docker/deploy.sh`), o que é **opcional mas recomendado**, e o
que é **decisão humana** (nunca deveria ser automatizado às cegas).

## 1. Backup validado

- **Automático**: `docker/backup-banco.sh` — `pg_dump` formato custom +
  valida o arquivo gerado (achado 04/09/2026), roda no máximo 1x/dia
  (`FORCAR=1` ignora o limite), só quando há migration pendente
  (`docker/deploy.sh` compara `alembic current` com `alembic heads` antes).
- **Opcional, recomendado para deploy com migration**:
  `VALIDAR_BACKUP=1 ./docker/deploy.sh` — roda `docker/simulado-
  restauracao.sh` logo após o backup, **antes** de aplicar a migration:
  restaura o dump recém-criado no `db-test` efêmero e confere que as
  tabelas centrais bateram com produção (dentro de tolerância). Se a
  restauração falhar ou alguma tabela vier vazia/muito menor, o deploy
  inteiro aborta (`set -eu`) antes de tocar no banco real. Desligado por
  padrão porque é um `pg_restore` completo — pode levar minutos com o
  banco em dezenas de GB; ligar para qualquer deploy com migration em
  produção, não é preciso para deploy só de código/frontend.
- Existe também um simulado de restauração **periódico** (cron, fora do
  fluxo de deploy) — ver `scripts/instalar-simulado-restauracao.ps1` —
  que detecta backup corrompido mesmo em dias sem deploy.

## 2. Confirmar commits da API, worker e rpi-sync

- **Automático**: `docker/deploy.sh` builda os três (`GIT_SHA` como
  build-arg, promovido a `ENV` no `Dockerfile` desde a Fase 7 — os
  processos conseguem ler o próprio commit em runtime) e, ao final,
  compara o label `org.opencontainers.image.revision` das três imagens
  (`SHAS_DIVERGENTES`) — aborta se divergirem (achado F0-3 da auditoria
  original).
- **Ao vivo, pós-deploy**: `GET /v1/admin/observabilidade/painel-tecnico`
  (Fase 7) mostra versão + commit de cada processo numa tela só, sem
  precisar de SSH.

## 3. Aplicar migration

- **Automático**: `docker compose up -d migrate` + `docker compose wait
  migrate` — propaga o código de saída (`set -eu` já derruba o script se
  a migration falhar).
- **Antes de chegar aqui** (não é parte de `deploy.sh`, é o passo que
  todo commit desta sessão passou antes do merge): ciclo completo
  `alembic upgrade head` → `downgrade -1` → `upgrade head` em banco de
  teste efêmero (`docker compose --profile test run --rm test uv run
  alembic ...`) — se o `downgrade()` não funcionar de verdade, o commit
  nem chega a `main`. Todo migration deste projeto documenta seu próprio
  procedimento de rollback dessa forma (ver `docs/fase7-observabilidade-
  rollback.md`, seção "Migration: procedimento documentado").

## 4. Subir backend com todas as flags desligadas

- **Por desenho, não precisa de ação no deploy**: toda `FeatureFlag` nova
  nasce com `estado_padrao="desligado"` (Fase 4) — o código novo já sobe
  inerte, sem precisar "lembrar" de desligar nada na hora do deploy. Uma
  flag só liga porque alguém decide explicitamente avançar o estágio
  depois (`PATCH /v1/admin/feature-flags/{codigo}/rollout`, Fase 5).
- Migrations nunca dependem do estado de uma flag (regra de produto
  desde a Fase 4) — o schema muda incondicionalmente, então "subir com
  tudo desligado" nunca deixa o banco num estado inconsistente com o
  código.

## 5. Validar endpoints e permissões

- **Antes do deploy** (gate, não pós-deploy): a suíte de regressão da
  Fase 8 (`tests/test_permissions.py`, `tests/test_fase8_*`, e os testes
  específicos de cada módulo) roda inteira na validação Docker antes do
  merge — permissões, isolamento entre organizações, ausência de
  segredos.
- **Pós-deploy, imediato**: `docker/deploy.sh` já verifica `/health`
  (`api saudavel (200)`) ao final. Para checagem manual mais ampla,
  `GET /v1/admin/observabilidade` (saúde de banco/fila/RPI) e o painel
  técnico da Fase 7.

## 6. Liberar a central para administradores

- **Decisão humana, ação de um clique**: avançar a flag da funcionalidade
  nova para o estágio `administradores` (Fase 5) —
  `PATCH /v1/admin/feature-flags/{codigo}/rollout` com
  `estagio_rollout: "administradores"`. Libera pra qualquer usuário
  administrador/superadmin, em qualquer organização, antes de qualquer
  cliente ver.

## 7. Ativar uma funcionalidade em organização-piloto

- **Decisão humana**: `POST /v1/admin/feature-flags/{codigo}/organizacoes/
  {organizacao_id}/ativar` (override por organização, Fase 4) — ou
  avançar o estágio global para `organizacoes_piloto` (Fase 5, que na
  prática é o mesmo mecanismo de override, documentado em
  `docs/fase5-liberacao-gradual.md`).

## 8. Monitorar

- **Painel técnico** (`GET /v1/admin/observabilidade/painel-tecnico`,
  Fase 7): erros por versão, organizações afetadas nas últimas 24h,
  status dos três processos.
- **Por flag especificamente**: `GET /v1/admin/feature-flags/{codigo}/
  monitoramento` (Fase 5) — uso/erros/falhas de integração por grupo de
  rollout, tempo de resposta médio.
- **Circuito automático**: se `limite_taxa_erro` estiver configurado na
  flag, o worker reavalia a cada hora e recua o estágio sozinho se a taxa
  de erro estourar (Fase 5) — não depende de alguém estar olhando o
  painel no momento exato do problema.
- **Contenção imediata, se algo der errado**:
  `POST /v1/admin/feature-flags/{codigo}/desligar` (Fase 7) corta a flag
  pra todas as organizações na hora, sem esperar o circuito automático.

## 9. Expandir gradualmente

- **Decisão humana, mesmo endpoint do passo 6**: avançar o estágio —
  `percentual_limitado` (com `percentual_rollout` crescente: 10 → 50 →
  100, por exemplo) e por fim `liberacao_geral`. Cada avanço é um
  `PATCH .../rollout` e fica auditado (`FLAG_ROLLOUT`).

## 10. Registrar versão e resultado do deploy

- **Hábito adotado nesta sessão para todo deploy**: publicar uma entrada
  em `VersaoSistema` (`POST /v1/admin/versoes-sistema` +
  `.../{id}/publicar`) com o commit real, a migration aplicada (se
  houver) e um resumo das evidências de teste — é o que alimenta a
  Central de Atualizações (confirmação de leitura, Fase 3) e o "último
  deploy" do painel técnico (Fase 7).
- **Se precisar reverter depois**: arquivar a versão problemática
  (`POST .../{id}/arquivar`, endpoint existente desde a Fase 1) — gera
  auditoria (`ARQUIVAR_RELEASE`) e documenta o motivo. Rollback de
  aplicação em si continua manual/restrito (SSH + git + `deploy.sh`) —
  ver `docs/fase7-observabilidade-rollback.md`.

## Resumo executável

```bash
# 1-3: backup validado + build + migration (script único)
VALIDAR_BACKUP=1 ssh <vps> "cd /opt/zeregistra && ./docker/deploy.sh"

# 2 (conferência ao vivo) + 5: painel técnico
curl -s https://app.zeregistra.com.br/v1/admin/observabilidade/painel-tecnico | jq .processos

# 6: liberar pra administradores
curl -X PATCH .../v1/admin/feature-flags/<codigo>/rollout -d '{"estagio_rollout":"administradores", ...}'

# 7: piloto
curl -X POST .../v1/admin/feature-flags/<codigo>/organizacoes/<org_id>/ativar

# 8: monitorar
curl -s .../v1/admin/feature-flags/<codigo>/monitoramento

# 9: expandir
curl -X PATCH .../v1/admin/feature-flags/<codigo>/rollout -d '{"estagio_rollout":"percentual_limitado","percentual_rollout":25, ...}'
# ... repetir aumentando o percentual, depois "liberacao_geral"

# 10: registrar
curl -X POST .../v1/admin/versoes-sistema -d '{...}'
curl -X POST .../v1/admin/versoes-sistema/<id>/publicar -d '{"confirmar_publicacao":true}'
```
