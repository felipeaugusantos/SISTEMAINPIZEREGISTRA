# Auditoria técnica — Atendimento comercial, CRM, Leads (10/09/2026)

**Escopo total (três rodadas):** ambiente de teste + Hipóteses 1 a 12 (lead público,
Kanban, descarte, conversão, indicadores, duplicidade, registro de contato, permissões,
cadências, automações, isolamento) + implementação do backlog P1. Nas duas primeiras
rodadas (investigação), nenhuma correção de produção foi aplicada — apenas reprodução,
evidência e doze novos arquivos de teste de regressão isolados. Numa terceira rodada, com
autorização explícita, os quatro achados P1 (Hipóteses 3, 4, 7 e 9) foram corrigidos,
validados e implantados em produção — ver "Status de implementação" logo abaixo.

**Resumo executivo:** de 12 hipóteses investigadas, **5 foram confirmadas como achados
reais** (H1.1/H1.3 já documentadas antes, H3 kanban sem coluna "Perdidos", H4 descarte sem
motivo, H7 leads convertidos/descartados mutáveis por reenvio público, H8 endpoint de
contato sem alternativa para lead sem pesquisa, H9 perfil comercial com acesso amplo a
jurídico/financeiro) e **3 hipóteses foram refutadas** (H1.2 próxima ação, H2 política do
Kanban, H5 conversão antecipada) — nesses casos o código já continha a correção, com
comentários de achados anteriores confirmando isso. As demais (H6, H10, H11, H12) tiveram
resultado misto: comportamento majoritariamente correto, com uma lacuna residual pontual
documentada em cada uma. Nenhum achado é crítico a ponto de bloquear operação hoje; os
dois com maior impacto potencial (H7 e H9) são de **integridade de dados**/**segurança
por privilégio excessivo**, não de disponibilidade.

**Ambiente usado:** o repositório não tem `.venv` local nem `uv`/`python` instalados nesta
máquina. A suíte foi executada na imagem de teste Docker isolada já usada neste projeto
(Postgres + Redis reais, `uv run pytest`), sincronizada com o HEAD do worktree a cada
rodada. Nenhum dado de produção foi tocado; `zeregistra-local.dump` não foi lido.

## Status de implementação (atualizado em 10/09/2026)

Todos os quatro itens do backlog P1 (Hipóteses 3, 4, 7 e 9) foram **implementados,
validados e implantados em produção** no mesmo dia, com autorização explícita do
solicitante para corrigir código de produção nesta segunda etapa:

| Hipótese | O que mudou | Commit | Versão publicada |
| --- | --- | --- | --- |
| H4 | `motivo_perda` passou a ser obrigatório (e validado) ao descartar um lead | `f438ad3` | 1.0.113 |
| H3 | Nova coluna "Perdidos" no Kanban para leads descartados; arrasto direto recusado | `f438ad3` | 1.0.113 |
| H7 | Reenvio público não sobrescreve mais lead convertido; lead descartado é reaberto | `f438ad3`, `5d62fa1` | 1.0.113 |
| H9 | Perfil "comercial" reduzido a visualização em financeiro/jurídico/carteira | `2d59ee5` | 1.0.114 |

Os testes de regressão que documentavam o comportamento antigo (bug) foram atualizados
para validar o comportamento corrigido — ver detalhamento de cada hipótese e a seção
"Testes atualizados na implementação" abaixo. As duas contas já existentes com perfil
"comercial" (Felipe Santos, Leticia Ferreira) foram sincronizadas manualmente ao novo
escopo restrito, já que a mudança no perfil-padrão não retroage sozinha sobre permissões
já gravadas por usuário — ambas foram avisadas por e-mail. Hipóteses 6 e 8 (P2) e a
decisão sobre desmembrar ainda mais o perfil comercial (H9, ver pergunta 1 na seção
"Pontos que dependem de decisão do CEO") permanecem em aberto.

## Tabela de resultados

| # | Hipótese | Resultado | Severidade | Status | Evidência (arquivo:linha) |
| --- | --- | --- | --- | --- | --- |
| 1.1 | Lead público fica sem responsável | Confirmado | Baixa (por desenho) | Aceito como está | `app/api/leads.py:539-632` (`criar_lead`) |
| 1.2 | Lead público fica sem próxima ação | **Não confirmado** (já corrigido) | — | — | `app/api/leads.py:519-530` |
| 1.3 | "Atribuir ao operador" funciona no form público | Confirmado que não funciona | Média (expectativa) | Aceito como está | `app/crm.py:149-163` |
| 1.4 | Dashboard conta lead sem responsável | Confirmado (correto) | — | — | `app/api/leads.py:899-923`, `1388-1431` |
| 2 | Kanban ignora política ao avançar | **Não confirmado** | — | — | `app/api/leads.py:1259-1267` |
| 3 | Descartado sem coluna própria no Kanban | Confirmado | Média | **Corrigido (1.0.113)** | `app/api/leads.py:924-948` |
| 4 | Descarte aceito sem motivo | Confirmado | Média | **Corrigido (1.0.113)** | `app/api/leads.py:1705-1716` |
| 5 | proposta_aceita converte antecipadamente | **Não confirmado** (já corrigido) | — | — | `app/crm.py` (`MAPA_FASE_STATUS`); `app/api/leads.py:3461` |
| 6 | Tempo até proposta usa atualizado_em | Parcialmente confirmado (fallback) | Baixa | Pendente (P2) | `app/api/leads.py:1296-1315` |
| 7 | Duplicidade não protege leads fechados | Confirmado | **Alta** | **Corrigido (1.0.113)** | `app/api/leads.py:576-632` |
| 8 | Registro de contato exige pesquisa_id sempre | Confirmado (lacuna de fluxo) | Média | Pendente (P2) | `app/api/leads.py:1894-1898`, `1953-1977` |
| 9 | Perfil comercial com privilégio excessivo | Confirmado | **Alta** | **Corrigido (1.0.114)** | `app/permissions.py:139-161` |
| 10 | Cadências (idempotência, sem passos, sem responsável) | Confirmado correto | — | — | `app/crm.py:607-678` |
| 11 | Automações (prazo, idempotência, dias=0) | Confirmado correto | — | — | `app/crm.py:532-604` |
| 12 | Isolamento por organização e mascaramento de PII | Confirmado correto (endpoints testados) | — | — | múltiplos |

## Hipóteses 4 a 12 — detalhamento

### Hipótese 4 — Descarte sem motivo
**Resultado:** Confirmado. **Evidência:** `app/api/leads.py:1711` — o bloco que valida e
grava `motivo_perda`/`motivo_perda_detalhe` só executa
`if "motivo_perda" in dados.model_fields_set`. Se o campo não é enviado, é pulado por
inteiro. **Teste:** `test_descartar_sem_motivo_perda_e_aceito_pelo_backend`,
`test_descartar_com_motivo_perda_invalido_e_rejeitado`,
`test_descartar_com_motivo_outro_sem_detalhe_e_aceito` (3 passed). **Observado:** descarte
sem motivo → 200, `motivo_perda=None`; motivo inválido → 422; motivo "outro" sem detalhe →
200, aceito sem exigir texto. **Impacto operacional:** relatório de "perdas por motivo"
(usado no dashboard) fica com uma fatia "não informado" que pode crescer sem limite,
esvaziando o valor analítico do indicador. **Severidade:** Média. **Recomendação:** exigir
`motivo_perda` no schema quando `status="descartado"` (validação de schema, mudança
pequena e localizada) — decisão de produto sobre se deve ser bloqueante ou só um aviso na
interface.

**Status: CORRIGIDO em 10/09/2026 (versão 1.0.113, commit `f438ad3`).** Decisão tomada:
bloqueante. `atualizar_status_lead` (`app/api/leads.py:1705-1716`) agora rejeita com 422
("Motivo de perda é obrigatório ao descartar a oportunidade") sempre que o status muda
para `descartado` sem `motivo_perda`, e rejeita separadamente ("Motivo de perda inválido")
um valor fora de `MOTIVOS_PERDA`. Os casos "outro" sem detalhe continuam aceitos (não fazia
parte da correção pedida). Testes atualizados:
`test_descartar_sem_motivo_perda_e_rejeitado` (novo),
`test_descartar_com_motivo_perda_valido_e_aceito` (novo) —
`test_descartar_sem_motivo_perda_e_aceito_pelo_backend`, que documentava o bug, foi
substituído.

### Hipótese 5 — Conversão antecipada
**Resultado:** Não confirmado — já corrigido (achado CRM-1/CRM-11 documentado no próprio
código). **Evidência:** `app/crm.py`, comentário sobre `MAPA_FASE_STATUS` e
`app/api/leads.py:3461` (conversão só ocorre ao registrar o **protocolo** no INPI, depois
de pagamento e recebimento jurídico). **Teste:**
`test_avancar_para_proposta_aceita_nao_converte_nem_marca_resultado` (1 passed, mais a
cobertura já existente de "ganho" em `test_crm_conversao.py`). **Observado:** mover para
`proposta_aceita` não altera `status` nem marca `resultado="ganho"`. O dashboard já separa
`taxa_aceite` (propostas aceitas), `taxa_pagamento` (pagas) e `taxa_protocolo`
(protocoladas) como métricas distintas — não confunde aceite com cliente pago.
**Ressalva não testada:** um operador pode arrastar manualmente um card do Kanban direto
para "Ganho" (`forcar=True`), convertendo sem passar pelo protocolo — é uma flexibilidade
manual existente, não um efeito automático de `proposta_aceita`; fica registrado como
ponto de atenção de processo, não de código. **Severidade:** — (não é achado).

### Hipótese 6 — Tempo médio até proposta
**Resultado:** Parcialmente confirmado. **Evidência:** `app/api/leads.py:1296-1315`
(`_tempo_medio_ate_proposta_dias`), já corrigida pelo achado L10 para usar
`HistoricoFaseLead.entrou_em` como fonte primária — mas com fallback explícito para
`lead.atualizado_em` quando não há entrada de histórico. **Teste:**
`test_com_historico_de_fase_edicao_posterior_nao_altera_o_indicador` (confirma o caminho
corrigido), `test_sem_historico_de_fase_o_fallback_ainda_usa_atualizado_em` (confirma que o
bug original sobrevive no fallback), `test_lead_fora_das_fases_pos_proposta_nao_entra_na_media`
(3 passed). **Observado:** com histórico real, uma edição 40 dias depois não muda o
indicador (4.0 dias, correto). Sem histórico, a mesma edição desloca o indicador de 2.0
para 12.0 dias. **Impacto operacional:** só afeta leads cuja transição para
"proposta_enviada" não passou por `avancar_fase_lead` (ex.: fase setada por outro caminho
que não gera `HistoricoFaseLead`) — caso hoje minoritário, mas sem uma auditoria de dados
para confirmar quantos leads reais estão nessa situação. **Severidade:** Baixa.
**Recomendação:** ao encontrar um lead sem entrada de histórico, preferir excluí-lo da
média (como já é feito para `criado_em` ausente) em vez de usar `atualizado_em` como
substituto — decisão de produto sobre a troca "métrica mais rara e correta" vs. "métrica
mais completa e às vezes distorcida".

### Hipótese 7 — Duplicidade por e-mail/telefone
**Resultado:** Confirmado — o achado de maior impacto desta rodada. **Evidência:**
`app/api/leads.py:551-567` (`consulta_existente` não filtra `status`, só
`arquivado_em.is_(None)`). **Teste:**
`test_lead_convertido_reenviando_o_formulario_tem_dados_sobrescritos_mas_status_preservado`,
`test_lead_descartado_reenviando_o_formulario_tem_dados_sobrescritos_e_continua_descartado`,
`test_lead_arquivado_reenviando_o_formulario_cria_lead_novo_sem_nenhum_vinculo` (3 passed).
Os cenários "mesmo e-mail e nova marca" e "duas marcas simultâneas" já tinham cobertura
existente (`tests/test_leads.py::test_upsert_publico_marca_diferente_cria_novo_lead_e_preserva_o_antigo`);
"mesmo telefone e novo e-mail" também
(`::test_upsert_publico_telefone_com_mascara_diferente_reconhece_o_mesmo_lead`).
**Observado:** um lead **já convertido** (negócio ganho) ou **descartado** que reenvia o
formulário com a mesma marca tem `nome`/`email`/`telefone` sobrescritos silenciosamente,
mas o `status` permanece o antigo — o cliente convertido continua "convertido" mesmo
recebendo dados novos; o descartado continua invisível ao funil ativo mesmo demonstrando
novo interesse. Um lead **arquivado** nunca é encontrado pela busca de duplicidade — um
reenvio cria um registro totalmente novo e desconectado do histórico anterior.
**Impacto operacional:** (a) risco de corrupção silenciosa de dados de clientes já
fechados; (b) leads reengajados após descarte ficam invisíveis à equipe comercial sem
nenhum alerta; (c) fragmentação de identidade para contatos que foram arquivados e
voltam. **Severidade:** Alta. **Recomendação:** decisão de produto sobre cada caso — (1)
convertido: não sobrescrever dados de contato automaticamente, ou criar uma nova
oportunidade em vez de mutar o negócio fechado; (2) descartado: reabrir o lead (voltar a
`status=novo`) ao reconhecer um reenvio genuíno, com auditoria; (3) arquivado: decidir se
o comportamento atual (ignorar e criar novo) é intencional (arquivamento como "esquecer de
propósito") ou se deveria haver um jeito de religar o histórico.

**Status: CORRIGIDO em 10/09/2026 (versão 1.0.113, commits `f438ad3`/`5d62fa1`).** Decisões
tomadas: (1) **convertido** — o reenvio não sobrescreve mais nenhum dado; só registra um
evento operacional (`crm.lead_reenvio_ignorado`) e devolve o lead como estava; (2)
**descartado** — o lead é reaberto automaticamente (`status=novo`, `resultado` e
`motivo_perda` limpos, evento `crm.lead_reaberto` registrado, volta a receber
`proxima_acao_em`); (3) **arquivado** — mantido como estava (decisão explícita de não
mexer agora). Testes atualizados:
`test_lead_convertido_reenviando_o_formulario_nao_tem_dados_sobrescritos` e
`test_lead_descartado_reenviando_o_formulario_e_reaberto` substituem os dois testes que
documentavam o comportamento antigo; o teste do cenário arquivado não mudou.

### Hipótese 8 — Registro de atendimento sem pesquisa
**Resultado:** Confirmado. **Evidência:** `app/api/leads.py:1898`
(`ContatoInput.pesquisa_id: str = Field(min_length=36, max_length=36)`, sem default) e
`1962-1975` (busca filtra `lead_id` e `organizacao_id` simultaneamente). **Teste:** 5
testes (`test_lead_com_pesquisa_registra_contato_normalmente`,
`test_lead_geral_sem_pesquisa_nao_consegue_usar_este_endpoint`,
`test_pesquisa_id_de_outro_lead_e_rejeitada`,
`test_pesquisa_id_de_outra_organizacao_e_rejeitada`,
`test_pesquisa_id_invalido_mal_formado_e_rejeitado_pela_validacao`; 5 passed).
**Observado:** os 3 casos de pesquisa "errada" (de outro lead, de outra organização,
malformada) colapsam no mesmo erro 422 seguro, sem vazar informação entre tenants. Lead
sem nenhuma pesquisa não tem como usar este endpoint (campo obrigatório sem valor
possível). **Existe outro fluxo?** Sim — `PATCH /v1/admin/leads/{lead_id}` com
`registrar_contato=true` (mesmo arquivo, linhas 1833-1875) cria um `ContatoLead` sem exigir
`pesquisa_id` explícito (resolve a pesquisa mais recente automaticamente, ou usa `None` se
não houver nenhuma). **A interface permite esse cenário?** Achado adicional por leitura de
`admin-leads.js` (não testado automaticamente): o diálogo dedicado "Registrar atendimento"
monta um `<select required>` só com as pesquisas do lead — para um lead sem nenhuma
pesquisa, o `<select>` fica vazio e `required` bloqueia o envio pelo navegador. Esse
diálogo específico é, na prática, inutilizável para um lead geral; o caminho que funciona é
outro formulário (edição do lead com a caixa "registrar contato"). **Impacto
operacional:** confusão de interface — a ação mais óbvia ("Registrar atendimento") falha
silenciosamente para leads gerais, embora exista uma alternativa funcional em outro
lugar da tela. **Severidade:** Média. **Recomendação:** o diálogo dedicado poderia cair
para o fluxo de `registrar_contato` quando o lead não tem pesquisas, ou pelo menos mostrar
uma mensagem explicando o caminho alternativo.

### Hipótese 9 — Permissões do perfil comercial
**Resultado:** Confirmado. **Evidência:** `app/permissions.py:139-161`. **Teste:** 4 testes
incluindo um snapshot exato do conjunto de permissões (4 passed). **Mapeamento
confirmado por teste:**

| Ação | Comercial pode? |
| --- | --- |
| Gerenciar carteira (`portfolio.manage`) | **Sim** |
| Criar/alterar dados jurídicos (`legal.manage`) | **Sim** |
| Criar/alterar dados financeiros (`finance.manage`) | **Sim** |
| Exportar dados financeiros (`finance.export`) | **Sim** |
| Visualizar dados pessoais (`leads.pii.view`) | **Sim** |
| Arquivar leads (`leads.delete`) | Não |
| Administrar usuários (`users.manage`) | Não |

**Impacto operacional:** o perfil "comercial" — pensado para atendimento/vendas — tem
acesso de gerenciamento total a dois módulos inteiramente fora desse escopo (jurídico e
financeiro), além de PII irrestrita. Pelo princípio do menor privilégio, isso é
desproporcional ao que a função de negócio exige. **Severidade:** Alta (risco de
segurança/governança, não de disponibilidade). **Recomendação — decisão do CEO/liderança,
não só técnica:** avaliar se esse acúmulo é intencional (equipe pequena, um único perfil
comercial cobre múltiplas funções na prática) ou se deveria virar dois perfis distintos
(comercial puro vs. comercial+financeiro/jurídico para quem realmente precisa). Nenhuma
permissão foi alterada nesta auditoria.

**Status: CORRIGIDO em 10/09/2026 (versão 1.0.114, commit `2d59ee5`).** Decisão tomada
(confirmada com o negócio): ninguém no perfil comercial de fato edita financeiro/
jurídico/carteira no dia a dia, só consulta para dar contexto no atendimento — não houve
necessidade de um segundo perfil. `portfolio.manage`, `legal.manage`, `finance.manage` e
`finance.export` foram removidas do perfil; a visualização (`portfolio.view`,
`legal.view`, `finance.view`) foi mantida, junto com todas as permissões de leads/CRM/
prospecção. **Achado adicional durante a implementação:** alterar o dicionário `PERFIS`
não retroage sobre permissões já gravadas por usuário (`usuario_permissoes` é um
snapshot feito na criação/edição da conta, não recalculado a partir do perfil a cada
acesso) — as duas contas existentes com perfil comercial (Felipe Santos, Leticia Ferreira)
precisaram ser resincronizadas manualmente via `PATCH /v1/admin/usuarios/{id}`, e foram
avisadas por e-mail sobre a mudança. Teste atualizado:
`test_permissoes_exatas_do_perfil_comercial` (snapshot revisado),
`test_comercial_nao_pode_mais_gerenciar_carteira_juridico_e_financeiro` e
`test_comercial_mantem_visualizacao_de_carteira_juridico_e_financeiro` substituem o teste
que documentava o acesso amplo antigo.

### Hipótese 10 — Cadências
**Resultado:** Confirmado correto nos pontos testados. **Evidência:** `app/crm.py:607-678`.
**Teste:** 5 testes (idempotência, sem passos, sem responsável, canal e-mail agenda envio
real, filtro de cadência ativa na query; 5 passed). **Observado:** reaplicar a mesma
cadência não duplica tarefas nem envios (constraint de idempotência); cadência sem passos
não falha, só não cria nada; lead sem responsável recebe a tarefa mesmo assim (sem
bloqueio); passo de canal "e-mail" cria também um `EnvioCadenciaEmail` agendado — cadência
não é só lembrete interno, ela agenda envio real (processado depois por um worker).
**Documentado por leitura de código (não testado, exigiria Postgres real para ter valor
além da leitura):** mudar o responsável do lead **depois** de criadas as tarefas não
atualiza `LembreteCRM.responsavel_id` retroativamente (o valor é copiado no momento da
criação, não é uma referência viva); excluir uma `Cadencia` não apaga
`LembreteCRM`/`EnvioCadenciaEmail` já criados (não há FK entre eles, só uma
`idempotency_key` em string) — tarefas já geradas sobrevivem à exclusão da cadência que as
originou; cancelamento/conclusão de tarefas é feito por outro endpoint (atualização de
`LembreteCRM.status`), fora desta função. **Severidade:** — (nenhum achado de bug; o ponto
"responsável não atualiza retroativamente" é um comportamento a documentar para a equipe,
não uma falha).

### Hipótese 11 — Automações
**Resultado:** Confirmado correto. **Evidência:** `app/crm.py:532-604`. **Teste:** 5 testes
(prazo/prioridade da regra, override desativado bloqueia, idempotência, `dias=0` cria
lembrete imediato, lead sem responsável ainda recebe a tarefa; 5 passed). **Observado:**
todas as 4 automações citadas (follow-up de proposta, cobrança ao aceitar, acompanhar
protocolo, reengajar sem retorno) seguem o mesmo motor único (`REGRAS_AUTOMACAO` +
`RegraAutomacao` para override por organização) — testado com a regra genérica
`followup_proposta`, que exercita exatamente o mesmo código das outras três.
**Severidade:** — (nenhum achado).

### Hipótese 12 — Isolamento e segurança
**Resultado:** Confirmado correto nos endpoints auditados nesta rodada (kanban, mover
pesquisa, resposta de lead). Cobertura genérica de permissões por perfil já existe em
`tests/test_permissions.py`; isolamento RLS multi-tenant com Postgres real já existe em
`tests/test_saas_rls_postgres.py` (não duplicados aqui). **Teste:** 4 testes (máscara de
PII com/sem permissão, lead de outra organização não encontrado no kanban, lead de destino
de outra organização rejeitado ao mover pesquisa; 4 passed). **Observado:** `_lead_response`
mascara e-mail/telefone/documento corretamente sem `leads.pii.view`; os dois endpoints
novos desta auditoria (kanban, mover-lead) filtram `organizacao_id` tanto no lead de
origem quanto no de destino, devolvendo 404/422 sem vazar a existência do registro de
outra organização. **Severidade:** — (nenhum achado nos pontos testados).

## Testes adicionados (12 arquivos, 43 testes novos)

| Arquivo | Testes | Resultado |
| --- | --- | --- |
| `tests/test_audit_hipotese1_lead_publico.py` | 3 | 3 passed |
| `tests/test_audit_hipotese2_kanban_politica.py` | 4 | 4 passed |
| `tests/test_audit_hipotese3_kanban_descartado.py` | 3 | 3 passed |
| `tests/test_audit_hipotese4_descarte_sem_motivo.py` | 3 | 3 passed |
| `tests/test_audit_hipotese5_conversao_antecipada.py` | 1 | 1 passed |
| `tests/test_audit_hipotese6_tempo_medio_proposta.py` | 3 | 3 passed |
| `tests/test_audit_hipotese7_duplicidade_lead_publico.py` | 3 | 3 passed |
| `tests/test_audit_hipotese8_contato_sem_pesquisa.py` | 5 | 5 passed |
| `tests/test_audit_hipotese9_permissoes_comercial.py` | 4 | 4 passed |
| `tests/test_audit_hipotese10_cadencias.py` | 5 | 5 passed |
| `tests/test_audit_hipotese11_automacoes.py` | 5 | 5 passed |
| `tests/test_audit_hipotese12_isolamento.py` | 4 | 4 passed |
| **Total** | **43** | **43 passed** |

Nenhum arquivo de produção foi criado ou alterado. Nenhum teste existente foi modificado.

## Comandos executados

```bash
# suíte solicitada inicialmente
uv run pytest -q tests/test_leads.py tests/test_crm.py tests/test_crm_history.py \
  tests/test_permissions.py tests/test_phase4_proposals.py

# ruff nos arquivos novos
uv run ruff check tests/test_audit_hipotese*.py

# testes novos (hipóteses 1-12)
uv run pytest -q tests/test_audit_hipotese1_lead_publico.py \
  tests/test_audit_hipotese2_kanban_politica.py tests/test_audit_hipotese3_kanban_descartado.py \
  tests/test_audit_hipotese4_descarte_sem_motivo.py tests/test_audit_hipotese5_conversao_antecipada.py \
  tests/test_audit_hipotese6_tempo_medio_proposta.py tests/test_audit_hipotese7_duplicidade_lead_publico.py \
  tests/test_audit_hipotese8_contato_sem_pesquisa.py tests/test_audit_hipotese9_permissoes_comercial.py \
  tests/test_audit_hipotese10_cadencias.py tests/test_audit_hipotese11_automacoes.py \
  tests/test_audit_hipotese12_isolamento.py

# suíte completa (regressão)
uv run pytest -q
```

Executados na imagem de teste Docker isolada (Postgres + Redis reais), não localmente —
ver nota de ambiente no topo deste documento.

## Saída resumida do pytest

- Suíte solicitada (5 arquivos): **141 passed**, 0 failed.
- 12 arquivos novos desta auditoria: **43 passed**, 0 failed (após ajustes de setup dos
  próprios testes — ver seção "Erros do próprio setup dos testes" abaixo).
- Suíte completa do projeto: **1232 passed, 1 failed (pré-existente e não relacionado —
  `test_ci_usa_postgres_com_pgvector`, arquivo `.github/workflows/ci.yml` não existe dentro
  da imagem de teste, só no repositório completo/CI real), 9 skipped**.

### Erros do próprio setup dos testes (não são achados do sistema)
Durante a escrita, 3 dos 43 testes novos falharam por engano no próprio teste (não no
sistema), todos corrigidos antes do resultado final acima:
- `Lead(...)` construído diretamente em teste não aplica `server_default` de
  `criado_em`/`atualizado_em` (gotcha já conhecido deste projeto) — corrigido setando os
  dois campos manualmente.
- Uma asserção esperava `"não pertence"` (acentuado); a mensagem real do backend é
  `"nao pertence"` (sem acento, por sanitização de texto) — corrigida a asserção.
  Descoberto que o comparador estava certo o tempo todo é considerado um teste bem
  desenhado, então preservamos a asserção pelo texto correto.

## Riscos encontrados (resumo)

1. ~~**Alta — Hipótese 7:** leads convertidos/descartados têm dados de contato
   sobrescritos por reenvios públicos, sem reabrir o status correspondente.~~
   **Corrigido em 10/09/2026 (v1.0.113).**
2. ~~**Alta — Hipótese 9:** perfil "comercial" acumula `finance.manage`, `legal.manage`,
   `portfolio.manage` e `leads.pii.view` — muito além do menor privilégio para uma função
   comercial.~~ **Corrigido em 10/09/2026 (v1.0.114)** — `leads.pii.view` foi mantida
   (decisão do negócio: é usada no atendimento comercial), as outras três removidas.
3. ~~**Média — Hipótese 3:** lead descartado fica preso numa coluna ativa do Kanban,
   contado junto com oportunidades abertas, sem coluna "Perdidos".~~ **Corrigido em
   10/09/2026 (v1.0.113).**
4. ~~**Média — Hipótese 4:** descarte sem motivo é aceito, esvaziando o indicador de
   "perdas por motivo" ao longo do tempo.~~ **Corrigido em 10/09/2026 (v1.0.113).**
5. **Média — Hipótese 8 (aberto):** o diálogo de "Registrar atendimento" é inutilizável
   para leads sem pesquisa vinculada (achado de interface, não testado automaticamente).
6. **Baixa — Hipótese 6 (aberto):** `tempo_medio_ate_proposta_dias` ainda é sensível a
   edições tardias para leads sem `HistoricoFaseLead` (caminho residual do achado L10, já
   corrigido no caso comum).

## Backlog recomendado

**P0 — bloqueia operação ou segurança:** nenhum achado desta rodada se qualifica como P0.
Todos os sistemas continuam operacionais; os achados são de integridade de dados e de
amplitude de permissão, não de indisponibilidade ou vazamento direto entre organizações.

**P1 — prejudica conversão, controle ou métricas — todos implementados em 10/09/2026:**
- ~~Hipótese 7: proteger leads convertidos/descartados de sobrescrita silenciosa pelo
  formulário público.~~ **Feito** (v1.0.113) — convertido não sobrescreve mais dados;
  descartado é reaberto automaticamente; arquivado mantido como estava.
- ~~Hipótese 9: decidir se o perfil "comercial" deve continuar com acesso total a
  jurídico/financeiro ou se deve ser desmembrado.~~ **Feito** (v1.0.114) — reduzido a
  view-only nesses módulos; nenhum desmembramento em perfis foi necessário.
- ~~Hipótese 3: dar a `descartado` uma fase/coluna própria no Kanban ("Perdidos").~~
  **Feito** (v1.0.113).
- ~~Hipótese 4: exigir `motivo_perda` no schema quando o status vira `descartado`.~~
  **Feito** (v1.0.113) — bloqueante (422).

**P2 — melhoria de experiência ou manutenção (ainda em aberto):**
- Hipótese 8: dar ao diálogo "Registrar atendimento" um caminho para leads sem pesquisa
  (ou uma mensagem explicando a alternativa já existente).
- Hipótese 6: decidir se o fallback residual de `tempo_medio_ate_proposta_dias` deve
  excluir o lead da média em vez de usar `atualizado_em`.
- Hipótese 2 (nota de manutenção, não bug): a etapa `proposta_aceita` do Kanban só é
  bloqueada por não zerar o status anterior, não por uma regra explícita — vale revisar se
  o funil crescer.

## Pontos que dependem de decisão do CEO (ou de quem define política comercial/jurídica)

Todas as quatro decisões abaixo foram tomadas em 10/09/2026 e já estão implementadas —
mantidas aqui como registro histórico da pergunta original e da resposta dada.

1. **Hipótese 9** — o perfil "comercial" deveria mesmo gerenciar jurídico e financeiro, ou
   isso é acúmulo histórico que merece separação de perfis?
   **Decidido:** nenhum dos dois — ninguém no perfil de fato editava esses dados;
   reduzido a visualização, sem necessidade de separar em perfis.
2. **Hipótese 7** — qual o comportamento correto para cada situação de reenvio: cliente já
   convertido deveria virar uma nova oportunidade, ou os dados do negócio fechado devem
   ficar congelados (somente leitura)? Um lead descartado que volta deveria reabrir
   automaticamente, ou isso precisa de decisão humana?
   **Decidido:** convertido fica congelado (reenvio não sobrescreve nada); descartado
   reabre automaticamente; arquivado mantido como estava.
3. **Hipótese 4** — motivo de perda deveria ser obrigatório (bloqueante) ou só recomendado
   (aviso não bloqueante) na interface?
   **Decidido:** bloqueante (422 no backend).
4. **Hipótese 3** — vale o esforço de criar uma coluna "Perdidos" no Kanban agora, ou isso
   fica para quando o volume de leads descartados justificar?
   **Decidido:** vale a pena agora — coluna criada.

## Arquivos criados ou alterados nesta auditoria

**Criados na rodada de investigação (todos em `tests/`, exceto o relatório):**
`test_audit_hipotese1_lead_publico.py` … `test_audit_hipotese12_isolamento.py` (12
arquivos) e este relatório (`docs/auditoria-crm-leads-2026-09-10.md`).

**Alterados na rodada de investigação:** nenhum arquivo de produção. Nenhum teste
existente.

## Testes atualizados na implementação (P1, 10/09/2026)

Com a correção autorizada, os testes que documentavam os comportamentos antigos (bug)
foram atualizados para validar o comportamento corrigido — nenhum teste foi removido sem
substituição, e nenhum teste de outras hipóteses (não corrigidas) foi tocado.

**Produção alterada:**
- `app/api/leads.py` — H3 (`KANBAN_ETAPAS`, `_kanban_etapa`, `mover_lead_kanban`), H4
  (`atualizar_status_lead`), H7 (`criar_lead`).
- `app/web/static/admin-leads.js` — H3 (rótulo e cálculo client-side da etapa "Perdidos",
  espelhando `_kanban_etapa`).
- `app/permissions.py` — H9 (perfil `comercial`).

**Testes atualizados:**
- `tests/test_audit_hipotese3_kanban_descartado.py`, `test_audit_hipotese4_descarte_sem_motivo.py`,
  `test_audit_hipotese7_duplicidade_lead_publico.py`, `test_audit_hipotese9_permissoes_comercial.py`
  — reescritos para validar a correção (ver "Status" em cada hipótese acima).
- `tests/test_financeiro.py` — `test_perfil_comercial_recebe_operacao_financeira_sem_aprovacao`
  substituído por `test_perfil_comercial_so_visualiza_financeiro_sem_gerenciar_ou_aprovar`
  (mesmo achado H9, teste pré-existente que também documentava o acesso amplo antigo).

**Validação:** suíte completa em Docker isolado após cada mudança — 1234 passed (H3/H4/H7)
e 1235 passed (H9), 9 skipped, 1 falha pré-existente e não relacionada
(`test_ci_usa_postgres_com_pgvector`), ruff limpo em ambas as rodadas.

**Implantação:** `git push` para `main` seguido de `docker/deploy.sh` na VPS de produção,
com verificação de saúde da API e consistência de commit entre os serviços `api`/`worker`/
`rpi-sync`; changelog registrado e publicado em Atualizações (versões 1.0.113 e 1.0.114).
Correção adicional de operação: as contas já existentes com perfil comercial (Felipe
Santos, Leticia Ferreira) foram resincronizadas manualmente ao novo escopo, já que a
alteração do perfil-padrão não retroage sobre permissões já gravadas por usuário — ambas
avisadas por e-mail.

**Ambiente usado:** o repositório não tem `.venv` local nem `uv`/`python` instalados nesta
máquina. A suíte foi executada na imagem de teste Docker isolada já usada neste projeto
(Postgres + Redis reais, `uv run pytest`), sincronizada com o HEAD atual do worktree
## Detalhamento — Hipóteses 1 a 3 (registrado na primeira rodada)

### Hipótese 1 — Novo lead público sem responsável

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

### Hipótese 2 — Kanban ignora a política ao avançar sem responsável/próxima ação?

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

### Hipótese 3 — Lead descartado não tem fase/coluna própria no Kanban

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

**Status: CORRIGIDO em 10/09/2026 (versão 1.0.113, commit `f438ad3`).** Decisão tomada:
sim, vale criar a coluna agora. Nova entrada `"perdidos"` em `KANBAN_ETAPAS`
(`app/api/leads.py`); `_kanban_etapa()` passou a checar `status == DESCARTADO` antes de
olhar a fase — o card vai para "Perdidos" independentemente de onde estava congelado antes
do descarte. Arrastar um card diretamente para essa coluna é recusado (422): descartar
continua exigindo o fluxo com `motivo_perda` (Hipótese 4), não um simples arrasto. Testes
atualizados: `test_lead_descartado_ainda_estagio_qualificado_vai_para_coluna_perdidos`,
`test_lead_descartado_nao_e_mais_contado_junto_com_a_coluna_qualificado` e
`test_mover_kanban_recusa_a_etapa_perdidos` substituem os dois testes que documentavam a
assimetria antiga.

Testes das Hipóteses 1-3 (10 testes, 3 arquivos) — ver tabela consolidada em "Testes
adicionados" e recomendações em "Backlog recomendado", no topo deste documento.
