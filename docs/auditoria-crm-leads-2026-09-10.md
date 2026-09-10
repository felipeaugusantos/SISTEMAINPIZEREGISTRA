# Auditoria técnica — Atendimento comercial, CRM, Leads (10/09/2026)

**Escopo desta rodada:** ambiente de teste (suíte especificada) + Hipóteses 1 a 3 (lead
público sem responsável; política ignorada pelo Kanban; lead descartado sem coluna
própria). Nenhuma correção de produção foi aplicada nesta etapa — apenas reprodução,
evidência e três novos arquivos de teste de regressão isolados.

**Ambiente usado:** o repositório não tem `.venv` local nem `uv`/`python` instalados nesta
máquina. A suíte foi executada na imagem de teste Docker isolada já usada neste projeto
(Postgres + Redis reais, `uv run pytest`), sincronizada com o HEAD atual do worktree
(`7ca7a4f`). Nenhum dado de produção foi tocado; `zeregistra-local.dump` não foi lido.

## 1. Execução da suíte solicitada

```
tests/test_leads.py tests/test_crm.py tests/test_crm_history.py
tests/test_permissions.py tests/test_phase4_proposals.py
```

**Resultado: 141 passed, 0 failed, 1 warning (StarletteDeprecationWarning, pré-existente,
não relacionado a este código).**

Nenhuma falha para investigar nesta rodada inicial.

## 2. Hipótese 1 — Novo lead público sem responsável

### Pergunta original
> O endpoint público `POST /v1/leads` aparentemente cria um lead sem responsável e sem
> próxima ação, sem aplicar imediatamente a política do CRM.

### Trilha de código revisada
- `app/api/leads.py::criar_lead` (POST `/v1/leads`)
- `app/api/leads.py::_garantir_proxima_acao_padrao`
- `app/crm.py::aplicar_politica_oportunidade`, `obter_politica_crm`, `distribuir_lead_automaticamente`
- `app/models.py::Lead` (defaults de `status`, `fase`, `responsavel_id`)
- `app/api/leads.py::resumo_crm_leads`, `dashboard_funil_produtividade`
- `app/emailing.py::enviar_alerta_novo_lead` (contexto/confirma o comportamento esperado)

### Achados

#### 2.1 — O lead fica sem responsável?
**CONFIRMADO** (código + teste automatizado novo).

`criar_lead` chama `_garantir_proxima_acao_padrao(session, lead)`, que chama
`aplicar_politica_oportunidade(session, lead)` **sem** `operador_id` (não existe operador
autenticado num POST público). Sem nenhuma `PoliticaCRM` cadastrada para a organização —
o caso mais comum —, `obter_politica_crm` devolve um objeto default em memória com
`atribuir_ao_operador=False` e `distribuicao_automatica_ativa=False`. Nenhuma das duas
alavancas liga sozinha, então `lead.responsavel_id` permanece `None`.

Isso **não é um bug silencioso**: `enviar_alerta_novo_lead` é disparado logo depois
exatamente para esse caso, com o texto literal *"ainda sem responsável"* — é um
comportamento **reconhecido e instrumentado**, não uma omissão.

Teste: `test_lead_publico_sem_politica_configurada_fica_sem_responsavel_mas_com_proxima_acao`
(novo arquivo, ver seção 3).

#### 2.2 — O lead fica sem próxima ação?
**REFUTADO pelo código atual** (a premissa da hipótese está desatualizada).

Existe um comentário explícito em `app/api/leads.py:519-523` registrando que isso *já foi*
um achado de auditoria anterior e *já foi corrigido*: `_garantir_proxima_acao_padrao`
aplica um fallback fixo de `DIAS_PROXIMA_ACAO_CAPTACAO_PADRAO = 2` dias sempre que a
política também não define `dias_proxima_acao_padrao`. Na prática, todo lead público sai
da criação com `proxima_acao_em` preenchido — nunca `None`.

Teste: mesmo teste da seção 2.1, que verifica `proxima_acao_em` no intervalo
`[agora+2d, agora+2d+1min]`.

#### 2.3 — "Atribuir ao operador" poderia ser aplicado numa requisição pública?
**CONFIRMADO: não, por desenho estrutural — e isso é um ponto que merece decisão de
negócio, não é claramente certo nem claramente errado.**

`aplicar_politica_oportunidade` só atribui ao "operador logado" se **ambos**
`lead.responsavel_id is None` **e** `operador_id` (parâmetro) forem verdadeiros. Como
`_garantir_proxima_acao_padrao` sempre chama a função sem `operador_id`, essa política —
mesmo ligada (`atribuir_ao_operador=True`) — é **inerte** para o formulário público. Ela só
tem efeito real em fluxos onde existe um operador autenticado registrando o lead em nome
de alguém (ex.: `app/api/consulta.py`, atendimento presencial/telefônico).

Isso é semanticamente correto (não existe "operador logado" numa visita anônima ao site) —
mas o nome da política ("atribuir ao operador") pode levar a equipe a configurá-la
esperando que também cubra o formulário público, sem cobrir. **Não é um bug de código; é
uma lacuna de expectativa/documentação que vale confirmar com quem define a política.**

Teste: `test_lead_publico_nao_aplica_atribuir_ao_operador_mesmo_com_politica_ligada`.

#### 2.4 — O dashboard contabiliza corretamente esse lead?
**CONFIRMADO POR LEITURA DE CÓDIGO — não por execução automatizada.** As duas rotas abaixo
usam `func.count().filter(...)`/`GROUP BY` agregados em SQL real; `FakeSession` (usada nos
testes deste projeto) não executa SQL de verdade, então esta parte foi validada por
inspeção, não por teste isolado. Para uma confirmação por execução seria necessário um
teste com Postgres real (padrão já usado em `tests/test_saas_rls_postgres.py`), fora do
escopo desta rodada.

- `resumo_crm_leads` (`GET` usado no card "Sem responsável" da Visão Geral): filtra
  `Lead.responsavel_id.is_(None)` E `status not in (CONVERTIDO, DESCARTADO)`. Um lead novo
  (`status=NOVO`) com `responsavel_id=None` **é contado** no bucket `sem_responsavel`.
- `dashboard_funil_produtividade`: agrupa por `Lead.responsavel_id` via `GROUP BY` — SQL
  agrupa `NULL` como um grupo próprio, e o código já rotula esse grupo explicitamente como
  `"Sem responsável"` (`app/api/leads.py:1417`). O lead também entra no funil (`fase`) e no
  cálculo de aberto/ganho/perdido (`resultado is None` → bucket "aberto").

Não foi encontrada nenhuma cláusula que exclua leads sem responsável dessas contagens.

#### 2.5 — Qual seria a regra de negócio mais segura?
**Decisão de negócio, não conclusão técnica.** Três caminhos existem hoje no código, cada
um com um trade-off diferente; nenhum é "o certo" sem uma escolha do time:

1. **Manter como está** (sem responsável até alguém puxar manualmente ou rodar o rodízio em
   lote — `POST /v1/admin/leads/distribuir`, `app/api/leads.py`). Risco: lead pode ficar
   invisível se ninguém olhar o card "Sem responsável" ou o kanban regularmente.
2. **Ligar `distribuicao_automatica_ativa`** na `PoliticaCRM` da organização. É a única
   alavanca que **já funciona** para o formulário público (não depende de `operador_id`) —
   round-robin entre usuários `perfil="comercial"` ativos (`distribuir_lead_automaticamente`,
   `app/crm.py`). Existe, é testável, só precisa ser ligada.
3. **Estender `aplicar_politica_oportunidade`** para também poder atribuir por outro
   critério quando não há operador (ex.: sempre cair no rodízio automático,
   independentemente de `atribuir_ao_operador`) — isso seria uma mudança de código, não
   coberta nesta rodada (instrução explícita: nenhuma correção de produção agora).

Nenhuma dessas opções foi aplicada; ficam registradas para decisão.

## 3. Hipótese 2 — Kanban ignora a política ao avançar sem responsável/próxima ação?

### Pergunta original
> O endpoint `POST /v1/admin/leads/{lead_id}/kanban` pode permitir avançar uma oportunidade
> sem responsável e sem próxima ação para `aguardando_contato_nosso`,
> `aguardando_retorno_cliente`, `proposta_enviada`, `proposta_aceita`.

### Achado: **REFUTADO — a política é respeitada nas 4 etapas testadas.**

`mover_lead_kanban` chama `aplicar_politica_oportunidade(session, lead, usuario.id)` e
levanta `422` se `faltando` não estiver vazio, **sempre que o status resultante não for
`CONVERTIDO`/`DESCARTADO`** — isso cobre as 4 etapas pedidas:

| Etapa | Status atribuído pelo endpoint | Passa pela checagem de política? |
| --- | --- | --- |
| `aguardando_contato_nosso` | `EM_CONTATO` (explícito) | Sim — já coberto por teste existente |
| `aguardando_retorno_cliente` | `SEM_RETORNO` (explícito) | Sim |
| `proposta_enviada` | `PROPOSTA_ENVIADA` (via `MAPA_FASE_STATUS`) | Sim |
| `proposta_aceita` | **nenhum** — `proposta_aceita` não está em `MAPA_FASE_STATUS`, o status permanece o que já era | Sim, mas por uma razão distinta: o status anterior também não é `CONVERTIDO`/`DESCARTADO` |

A etapa `proposta_aceita` merece nota: ela não define um novo status, então o bloqueio
funciona "por acidente" — se algum dia essa etapa passar a ser alcançável a partir de um
status `CONVERTIDO`/`DESCARTADO` (hoje não é o caso), o comportamento mudaria. Não é um
bug hoje, mas é um ponto frágil de manutenção a observar se o funil for alterado.

Dois testes já existiam cobrindo `aguardando_contato_nosso`
(`tests/test_leads.py::test_mover_kanban_bloqueia_oportunidade_aberta_sem_proxima_acao` e
`::test_mover_kanban_permite_oportunidade_aberta_com_proxima_acao`). Os 4 testes novos
estendem a mesma verificação às outras etapas pedidas.

## 4. Hipótese 3 — Lead descartado não tem fase/coluna própria no Kanban

### Pergunta original
> O status `descartado` não parece possuir fase ou coluna própria. Em qual coluna aparece?
> Continua contado? Deveria ir para "Perdidos"? O mesmo ocorre com convertidas?

### Achado: **CONFIRMADO — é uma assimetria real, específica de `descartado`.**

- **Em qual coluna aparece:** nenhuma. `MAPA_STATUS_FASE` (`app/crm.py`) não tem entrada
  para `"descartado"` — `sincronizar_fase_por_status` (chamada em `atualizar_status_lead`
  ao mudar o status) não altera `lead.fase` nesse caso. O lead **permanece na última fase
  em que estava antes de ser descartado** (ex.: descartado enquanto "Qualificado" continua
  aparecendo, para sempre, na coluna "Qualificado").
- **Continua contado:** sim. `listar_leads_kanban` (`GET /v1/admin/leads-kanban`) filtra
  só `organizacao_id` e `arquivado_em IS NULL` — **não exclui `status in (CONVERTIDO,
  DESCARTADO)`**, diferente do dashboard principal (`dashboard_funil_produtividade` usa
  `Lead.status.not_in((CONVERTIDO, DESCARTADO))` para o que conta como "aberta"). O lead
  descartado entra no `cards` e no total exibido no cabeçalho da coluna, misturado com
  oportunidades genuinamente abertas.
- **Deveria ir para "Perdidos":** isso é uma decisão de produto, não uma conclusão técnica.
  O sistema já modela perda de forma estruturada em outro lugar (`Lead.resultado="perdido"`
  + `Lead.motivo_perda`, usados no dashboard) — só não existe uma projeção disso no Kanban.
- **O mesmo ocorre com convertidas? Não.** `"convertido"` **tem** entrada em
  `MAPA_STATUS_FASE` (→ `"ganho"`), e existe uma etapa `"ganho"` própria em
  `KANBAN_ETAPAS`. Leads convertidos são corretamente separados das oportunidades abertas
  numa coluna terminal distinta. A lacuna é específica de `descartado`, que não tem
  nenhuma fase/coluna terminal equivalente.

## 5. Testes de regressão criados

Três arquivos novos, isolados e determinísticos (usam `FakeSession`,
`tests/conftest.py`, sem tocar banco real). Todos commitados nesta rodada.

```
tests/test_audit_hipotese1_lead_publico.py .....................  3 passed
tests/test_audit_hipotese2_kanban_politica.py ......................  4 passed
tests/test_audit_hipotese3_kanban_descartado.py ...................  3 passed
```

`ruff check` limpo nos três arquivos.

- `test_audit_hipotese1_lead_publico.py` (3 testes) — ver seção 2.
- `test_audit_hipotese2_kanban_politica.py` (4 testes) — bloqueio da política nas 4
  etapas pedidas, mais um contraponto (`proposta_aceita` aceita quando responsável e
  próxima ação estão preenchidos).
- `test_audit_hipotese3_kanban_descartado.py` (3 testes) — coluna herdada por lead
  descartado, contagem junto com abertos, e o contraste correto com lead convertido.

## 6. Próximos passos sugeridos (não executados)

- Confirmar com o time se a lacuna da seção 2.3 (Hipótese 1) é aceitável ou se
  `atribuir_ao_operador` deveria também cobrir o formulário público de alguma forma.
- Se for decidido seguir a opção 2 da seção 2.5 (Hipótese 1), isso é uma mudança de
  **dado** (configuração da política), não de código.
- Decidir se `descartado` merece uma coluna "Perdidos" própria no Kanban (Hipótese 3) —
  mudança de código e de UX, não feita nesta rodada.
- Continuar a auditoria pelas próximas hipóteses do usuário (cadências, permissões,
  indicadores) quando fornecidas.
