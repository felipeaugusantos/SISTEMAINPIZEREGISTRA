# Relatório técnico — 08/09/2026

Resumo das mudanças de infraestrutura e produto entregues no dia: correções e
melhorias no Radar de Prospecção, correção da aba Situação INPI e novo
relatório em PDF nos Processos Monitorados, listagem de eventos operacionais
na tela de Observabilidade, RAG local (pgvector) na IA em sombra, e migração
de fuso horário da VPS de produção.

## 1. Radar de Prospecção — melhorias e correções subsequentes

### Melhorias entregues (lista de 4 pedidos do usuário)

1. **`ze.registra` não abria o Prospect** — não era bug de código: o plano
   SaaS da organização não incluía o módulo "prospeccao". Corrigido direto
   no banco de produção, com autorização do usuário, sem mudança de código.
2. **"Aprovar" não navegava para o cadastro de Leads** — "Aprovar" e
   "Converter em lead" continuam deliberadamente separados (achado FASE5-9
   da auditoria: evita lead criado sem querer), mas agora, após "Converter
   em lead" ter sucesso, a tela navega para a página do lead recém-criado.
3. **Selecionar vários estados/cidades** — o filtro de listagem de
   prospects e o critério de campanha de coleta agora aceitam múltiplas UFs
   (`select multiple`) e cidades (separadas por vírgula), no backend
   (`app/api/prospeccao.py`, `app/schemas.py`, `app/worker.py`) e no
   frontend (`admin-prospeccao.html/js/css`). A base de dados (cache
   CNPJ/RFB) já era nacional — só o filtro limitava a 1 valor.
4. **Verificar se o prospect já tem marca registrada no INPI** (não só a
   marca especificamente pesquisada) — nova classificação de triagem
   `POSSUI_OUTRA_MARCA_REGISTRADA` (`app/prospeccao_triagem.py`), checada em
   duas fontes:
   - **Base local**: `Titular.nome_normalizado` (coluna nova, indexada,
     mesmo padrão de `EmpresaCRM.nome_normalizado`; backfill via `UPDATE`
     único, ~2,4M linhas em produção). `app/rpi/importer.py` passa a
     preencher a coluna em toda importação semanal.
   - **Busca ao vivo por CNPJ** no site público do INPI (pePI aceita
     CNPJ/CPF como chave, mais precisa que nome normalizado) —
     `app/inpi_titular_live.py`, desligada por padrão
     (`settings.prospeccao_titularidade_inpi_ao_vivo_enabled`), best-effort:
     qualquer erro (pePI é um sistema legado, devolveu 502/timeout em teste
     manual) cai em silêncio no fallback da base local, nunca quebra a
     triagem.

### Correções encontradas após o deploy inicial

O deploy da feature acima expôs uma sequência de bugs reais, todos
corrigidos no mesmo dia:

- **Botão "Converter em lead" aparecia cedo demais**: exibido tanto para
  status "novo" quanto "aprovado", mas o backend só aceita converter quem já
  está "aprovado" — clique em prospect "novo" sempre dava erro 422. Corrigido
  para só mostrar o botão quando a conversão é de fato possível. Também foi
  corrigido o layout do `select multiple` de UF (grid do formulário
  redefinido para 5 colunas, altura compacta) e o alinhamento vertical do
  formulário de filtro.
- **`ck_leads_origem_valida` desatualizada**: `converter_prospect_em_lead`
  grava `origem="prospeccao"` desde a criação do endpoint, mas esse valor
  nunca entrou na constraint do banco — toda conversão falhava com 500
  (`CheckViolationError`), mascarado como se fosse bug de navegação/422.
  Confirmado em produção: 2 prospects (624, 626) falharam sem deixar dado
  órfão (rollback funcionou); migration `ke86z1g7s408` corrige a constraint.
- **`ck_prospect_triagens_classificacao_valida` /
  `ck_prospects_triagem_marca_status_valido` desatualizadas**: o novo valor
  `POSSUI_OUTRA_MARCA_REGISTRADA` do item 4 não tinha entrado nas duas
  constraints que validam esse campo — job de triagem falhava ao
  reprocessar. Migration `lf97a2h8t519`.
- **Leads convertidos da prospecção sumiam da tela de Leads**: a visão "Por
  pesquisa" gera uma linha por `PesquisaMarca` de cada lead; um lead sem
  nenhuma pesquisa formal associada (todo lead vindo do Radar, cuja triagem
  é só indicativa) nunca produzia linha — explicava também o "bug de
  paginação" relatado (página cheia de leads sem pesquisa = tabela vazia).
  Corrigido em duas etapas: linha-placeholder para lead sem pesquisa, e
  depois `converter_prospect_em_lead` passou a criar a `PesquisaMarca`
  automaticamente a partir da triagem (ou, na ausência de triagem, da mesma
  extração de marca candidata que a triagem usaria) — estado `DRAFT`, com
  revisão humana obrigatória como qualquer pesquisa manual.
- **`ck_leads_fase_valida` desatualizada** (achado por auditoria
  sistemática cruzando todos os `StrEnum` do projeto com as constraints
  reais do banco — mesmo padrão dos 3 bugs acima): o enum `FaseLead` foi
  expandido de 7 para 10 valores em 04/09/2026, mas a constraint nunca
  acompanhou. 4 valores usados em produção
  (`aguardando_pagamento`, `pagamento_confirmado`, `ganho`, entre outros)
  ficavam fora da lista aceita. Migration `mg08b3i9u620` corrige e remove o
  valor morto `pagamento_realizado` (confirmado sem uso em produção antes
  da remoção).

Todas as correções de constraint seguiram o mesmo padrão de causa raiz:
`StrEnum` do Python ganha valor novo, a `CHECK CONSTRAINT` do Postgres nunca
é atualizada — invisível nos testes porque usam `FakeSession` (não valida
constraints reais), só aparece em produção. Cada uma ganhou um teste de
integração contra Postgres real para não regressão.

## 2. Processos Monitorados — aba Situação INPI e relatório em PDF

### Bug relatado

Usuário reportou "a busca dos processos monitorados não tá resultando".
Investigação mostrou que não era bug de busca: a aba inteira "Situação INPI"
(`listar_kanban_inpi`) estava quebrada com 500 Internal Server Error, com ou
sem busca.

### Causa e correção

A query de contagem por grupo de situação selecionava só uma expressão
derivada de `Processo` (não uma coluna de `ProcessoMonitorado`) sem
`select_from(ProcessoMonitorado)` explícito — o SQLAlchemy não conseguia
inferir a base do `FROM` para o `join` seguinte (`InvalidRequestError`).
Corrigido adicionando `select_from` explícito, mesmo padrão já usado em
`listar_carteira` para a query de resumo por situação. Teste de integração
novo (`test_carteira_kanban_inpi.py`) contra Postgres real, já que
`FakeSession` não executa SQL de verdade e não pegaria esse tipo de bug.

Uma auditoria sistemática dedicada, feita em seguida, confirmou que esse era
o único caso desse padrão (`select()` com coluna derivada + `join` sem
`select_from`) no restante do código — mais de 60 queries revisadas em
`app/api/*.py`.

### Melhoria entregue junto

Relatório de acompanhamento em PDF por processo monitorado, para enviar ao
cliente: mostra situação atual no INPI, fase no escritório e histórico de
movimentações da RPI, com campo de observações digitado na hora (não
persiste no cadastro interno do processo).
`app/relatorios.py::gerar_pdf_processo_monitorado` reaproveita os
estilos/cabeçalho já usados nos outros PDFs do sistema. Novo endpoint
`POST /v1/admin/carteira/{id}/relatorio-pdf`; botão "Gerar relatório" em
cada processo, com modal para o campo de observações.

## 3. Listagem de eventos operacionais (Observabilidade)

### Linha de base comprovada

- `EventoOperacional` é gravado a cada requisição pelo middleware
  (`app/observability.py`) desde o início: componente, operação, `request_id`,
  sucesso, duração, status HTTP, código de erro e detalhes em JSON.
- Os únicos consumidores existentes (`obter_observabilidade`, painel executivo
  em `app/main.py`, painel de produção) só usavam `func.count()` / `func.avg()`
  / `func.percentile_cont()` — nenhum endpoint listava as linhas individuais
  para investigar qual erro aconteceu. Mesmo padrão de achado já visto em
  `EnvioCadenciaEmail` (dado gravado, nunca lido).

### Implementação

- Novo endpoint `GET /v1/admin/observabilidade/eventos`
  (`app/api/observabilidade.py`), com filtros por componente, código de erro,
  período (1h/24h/7d/30d) e "só erros", paginado (limite/deslocamento).
- Acesso restrito a `superadmin`/`administrador`/`tech`
  (`_exigir_acesso_tech`) — `EventoOperacional` não tem `organizacao_id`
  (log técnico cross-tenant), diferente das telas com escopo por organização.
- Nova seção "Eventos recentes" em `admin-observabilidade.html` com tabela,
  filtros e paginação (`admin-observabilidade.js`).
- Sem migration: nenhuma mudança de schema, só leitura de uma tabela
  existente.

### Testes e deploy

- 2 testes novos em `test_observabilidade.py` (formato da resposta, exigência
  de acesso técnico). Suíte completa: 1025 passed / 5 skipped.
- Deploy: commit `a8c60d1`, consistente em api/worker/rpi-sync, `/health` 200.

## 4. RAG local (pgvector) na IA em sombra

### Contexto

Análise do repositório aberto DeskcommCRM (referência do usuário) identificou
"RAG por tenant" como um recurso portável de forma barata usando a IA em
sombra local já existente (Ollama, sem custo de API). Escopo escolhido pelo
usuário: enriquecer as sugestões já existentes (sem criar tela de chat nova).

### Implementação

- **Infraestrutura**: Postgres trocado de `postgres:16-alpine` para
  `pgvector/pgvector:pg16` em `compose.yaml` (produção e teste) — mesma versão
  major do Postgres, mesmo volume de dados, sem necessidade de dump/restore.
- **Migration** `nh30d4k1w842`: `CREATE EXTENSION vector`, tabela
  `embeddings_lead` (RLS por organização, índice `ivfflat` para distância de
  cosseno).
- **Modelo** `EmbeddingLead` (`app/models.py`): um registro por lead com
  resultado conhecido (ganho/perdido), com embedding, resumo indexado e
  resultado.
- **`app/ia_sombra.py`**:
  - `gerar_embedding_ollama`: gera embedding via Ollama (`/api/embeddings`,
    modelo `nomic-embed-text`), mesmo servidor local do modelo de geração
    (`qwen2.5:7b-instruct-q4_K_M`).
  - `indexar_lead_para_rag` / `indexar_embeddings_leads_pendentes`: indexa
    (upsert) leads com resultado conhecido; job periódico novo
    (`leads.indexar_rag`), mesmo par de flags de kill-switch dos outros jobs
    da IA em sombra (`settings.ia_sombra_enabled` +
    `PoliticaCRM.ia_sombra_ativa`).
  - `buscar_leads_similares` / `_contexto_casos_semelhantes`: busca por
    distância de cosseno e injeta "casos semelhantes já concluídos" no prompt
    de `gerar_sugestao_lead`, antes da chamada ao modelo de geração.
  - Indisponibilidade do modelo de embeddings ou do pgvector nunca derruba a
    sugestão principal — degrada para "sem precedentes" (mesmo princípio de
    tolerância a falha já usado nas outras frentes da IA em sombra).
- `compose.yaml`: novo serviço `ollama-pull-embedding` (baixa
  `nomic-embed-text`), nova env `IA_SOMBRA_EMBEDDING_MODELO` em api/worker.
- Dependência nova: `pgvector` (Python), com `numpy` transitivo; `uv.lock`
  regenerado.

### Testes e deploy

- 12 testes novos em `test_ia_sombra.py` (indexação, upsert, busca por
  similaridade, degradação sem erro, inclusão de precedentes no prompt).
  Suíte isolada: 36/36 passed.
- Migration validada com ciclo completo `upgrade` → `downgrade -1` →
  `upgrade` antes do deploy.
- Cadeia de migrations rebaseada sobre `h19r4t0n731` (governança de
  retenção), que chegou ao `main` em paralelo por outra sessão — evitado
  merge de duas heads.
- Suíte completa: 1061 passed / 5 skipped / **1 falha pré-existente e não
  relacionada** (`test_serie_temporal_leads_agrega_criacoes_e_funil_por_dia`,
  bug de fuso no dashboard de leads, virou tarefa separada — ver seção 6).
- Deploy: container `db` de produção recriado com `pgvector/pgvector:pg16`
  (dados confirmados intactos antes/depois — 53 leads). Commit final `6ffc7ea`
  consistente em api/worker/rpi-sync, `/health` 200.

## 5. Migração de fuso horário da VPS (UTC → America/Sao_Paulo)

### Motivação e risco avaliado

Pedido do usuário. Risco identificado antes da execução: `cron` do sistema
operacional já tinha horários calculados manualmente para bater com Brasília
em UTC (comentários no crontab confirmando isso) — trocar o fuso sem ajustar
esses horários faria reinícios automáticos e backup diário disparar 3h mais
tarde (em horário real de Brasília) do que disparam hoje. A aplicação em si
(Docker/Python) não é afetada, pois já usa `datetime.now(UTC)` explícito em
vez de ler o relógio do sistema.

### Execução

1. `timedatectl set-timezone America/Sao_Paulo` — RTC permanece em UTC
   (`RTC in local TZ: no`), sem salto de horário, só reinterpretação.
2. **Crontab do root**: 4 horários recalculados (-3h cada) para manter o
   mesmo instante real de Brasília — 2 reinícios automáticos (06:30/19:00),
   backup diário (02:00), simulado de restauração semanal (01:00 de domingo).
3. **`/etc/cron.d`** (certbot, docker-builder-prune, docker-image-prune,
   e2scrub_all, monarx-update, sysstat): horários ajustados por edição direta
   dos arquivos. Ressalva: são conffiles mantidos por pacotes com atualização
   automática ativa (`apt-daily-upgrade.timer`) — podem ser sobrescritos numa
   futura atualização de pacote, sem aviso.
4. **Timers do systemd** (10 unidades: `apt-daily-upgrade`, `certbot`,
   `apt-daily`, `man-db`, `dpkg-db-backup`, `logrotate`, `sysstat-summary`,
   `e2scrub_all`, `fstrim`, `update-notifier-motd`): ajustados via
   `systemctl edit` (drop-in em `/etc/systemd/system/<unidade>.timer.d/
   override.conf`), sobrevive a atualização de pacote. Timers hora-agnósticos
   (`sysstat-collect`) ou relativos (`OnUnitActiveSec`,
   `systemd-tmpfiles-clean`, `update-notifier-download`) não precisaram de
   ajuste.
5. Verificado via `systemctl show` que os horários-base ficaram corretos;
   variações observadas em `systemctl list-timers` (ex.: certbot, man-db) são
   `RandomizedDelaySec` de fábrica (12h/1h), não erro de configuração.

### Pendência conhecida

Duas unidades (`certbot.timer`, `e2scrub_all.timer`) já eram as reais
controladoras dessas tarefas antes da mudança — os arquivos equivalentes em
`/etc/cron.d` tinham uma guarda (`test -e /run/systemd/system`) que os torna
inertes neste host. Foram ajustados de qualquer forma por consistência.

## 6. Tarefa separada registrada

- **Bug de fuso no dashboard de leads** (`task_07d58312`): endpoint
  `/v1/admin/leads-dashboard/serie-temporal` calcula "hoje" de forma
  divergente do teste (`datetime.now(UTC).date()`), causando falha
  reprodutível logo após a virada de dia em UTC. Não relacionado ao RAG nem
  à migração de fuso — encontrado incidentalmente durante a validação da
  seção 4. Ainda não corrigido.

## 7. Itens fora de escopo (decisão do usuário)

- Inbox de WhatsApp integrado ao CRM (Meta Cloud API): analisado e
  orçado (~R$ 33–56/mês no volume atual de leads/prospects), mas **abortado**
  pelo usuário antes de qualquer implementação — nenhuma mudança de código.
