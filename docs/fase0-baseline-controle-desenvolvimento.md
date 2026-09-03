# Fase 0 — Baseline e controle do desenvolvimento

Executada em 03/09/2026, logo após a conclusão da auditoria Leads/CRM (fases 1–10, ver `docs/matriz-prometido-implementado.md` se existir referência cruzada, ou o histórico de commits `ae63dc7`..`5f2df57`).

## 1. Baseline

- **Commit baseline: `5f2df57`** (`fix: inclui rpi-sync no default de docker/deploy.sh`).
- Não foi `145d580`: esse era um commit anterior, sem o ajuste do `rpi-sync`. `5f2df57` é o que estava de fato rodando nos 4 serviços (`api`, `worker`, `migrate`, `rpi-sync`) no momento em que esta fase começou — confirmado por comparação de hash SHA-256 de `app/models.py` entre o container em execução e o checkout do repositório.
- Tag estável criada a partir dele: `git tag baseline-fase0 5f2df57`.
- A partir de `docker/deploy.sh` (ver item 4 abaixo), toda imagem publicada carrega o commit exato como label OCI (`org.opencontainers.image.revision`), consultável via:
  ```bash
  docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' zeregistra-api:latest
  ```

## 2. Suíte de testes (item 6)

Antes desta fase, 2 testes falhavam de forma pré-existente (identificados durante a Fase 10 da auditoria Leads/CRM, quando pytest passou a rodar de verdade neste ambiente pela primeira vez). Investigados a fundo nesta fase — **nenhum dos dois era o que parecia**:

- `tests/test_trademark_learning.py::test_portfolio_titular_norm_respeita_a_data_de_referencia` — não era deriva de calendário. O teste tinha uma asserção logicamente impossível (`resultado == 2.0 and resultado <= 1.0`, quando a função sempre limita o retorno em `1.0`) — estava quebrado desde que foi escrito, nunca passou de verdade. Corrigido para testar o comportamento real (chave por `(titular_id, data_referencia)`, sem vazamento entre datas, e o teto de normalização em `1.0` testado à parte).
- `tests/test_consolidated_analysis.py::test_pdf_and_screen_share_conclusion_and_private_opinion_stays_internal` — este **era um bug de produto real**: o parecer do especialista humano (`parecer_humano.observacoes`) nunca era impresso em lugar nenhum do PDF gerado (`app/relatorios.py::gerar_pdf_relatorio`), nem na versão interna. A justificativa técnica escrita por quem valida uma análise ficava, na prática, invisível no relatório. Corrigido: agora aparece numa seção "Parecer do especialista", só na versão interna (`incluir_ocorrencias=True`), nunca na pública.

**Resultado: 565 testes, 0 falhas, 4 skipped** (suíte 100% verde pela primeira vez).

## 3. Deriva de migrations (item 7) — débito técnico registrado, não corrigido agora

`alembic check` acusa deriva real entre os modelos SQLAlchemy e o histórico de migrations em **dezenas de tabelas**, pré-existente e sem relação com o trabalho desta sessão (o próprio `ci.yml` já documentava isso desde antes, com o check marcado `continue-on-error: true`).

Decisão explícita: **não reconciliar agora**. O escopo (gerar migrations de ajuste tabela por tabela, com risco real em produção) é maior que o resto desta Fase 0 somado e merece uma fase dedicada, com validação cuidadosa achado por achado — não deve ser empacotado apressadamente aqui. O CI continua com esse check non-blocking até essa fase acontecer.

**Ação de acompanhamento sugerida:** criar uma "Fase 0.1 — Reconciliação de drift de migrations" antes de tornar `alembic check` obrigatório no CI (item 7 original).

## 4. Commit hash dentro das imagens (item 4)

`Dockerfile`, estágio `production`: `ARG GIT_SHA=unknown` + `LABEL org.opencontainers.image.revision="${GIT_SHA}"`. `docker/deploy.sh` passa `--build-arg GIT_SHA=$(git rev-parse HEAD)` em todo build.

## 5. Backup automático antes de migrations (item 8) e teste de restauração (item 9)

- `docker/backup-banco.sh` — equivalente em bash do `scripts/backup-banco.ps1` existente (mesmo formato `pg_dump -Fc`, mesma validação via `pg_restore -l`, mesma retenção configurável), pensado para rodar direto na VPS.
- `docker/deploy.sh` chama esse backup automaticamente **antes** de subir o serviço `migrate` — se uma migration der problema, existe um dump imediatamente anterior para restaurar.
- `docker/restaurar-banco.sh` — restaura um dump. Por padrão restaura no `db-test` (banco efêmero do perfil `test`, criado na Fase 10 da auditoria Leads/CRM) para validar o backup **sem qualquer risco ao banco real**. Só restaura em produção com a flag explícita `--producao` + confirmação digitada.
- Teste de restauração realizado nesta fase: backup gerado a partir do banco de produção, restaurado com sucesso em `db-test`, contagens de `leads`/`organizacoes` conferidas.

## 6. Política de branches (item 10) — adotada a partir desta fase

```
main:                    somente código homologado (nunca push direto)
uma branch por fase:     fase-N-descricao-curta
isolamento:              Claude e Codex não editam a mesma branch simultaneamente
commits:                 cada agente entrega commit isolado, mensagem descreve o "porquê"
revisão:                 diff revisado (humano ou /code-review) antes do merge
migrations:               toda migration tem upgrade() e downgrade(), testados
implantação:              somente com CI verde
```

A partir desta fase, o fluxo de trabalho muda: em vez de fast-forward direto na `main` a cada entrega (como foi feito nas fases 1–10 da auditoria Leads/CRM), cada fase abre uma branch e um Pull Request, com o CI rodando antes do merge.

## Critérios de conclusão

- [x] Todos os containers usam o mesmo commit (`5f2df57` → `<próximo commit desta fase>`, confirmado por hash de `app/models.py`).
- [x] CI completamente verde para tudo exceto a checagem de migration drift (deliberadamente non-blocking, ver item 3).
- [ ] Nenhuma migration drift — **não atingido de propósito** (ver item 3); fica como próxima fase dedicada.
- [x] Backup e restauração testados (`db-test`).
- [x] Rollback documentado (`docker/rollback.sh`, já existente da entrega anterior).
- [x] Working tree da VPS limpo (exceto 2 arquivos de benchmark não rastreados, sem relação com código).
