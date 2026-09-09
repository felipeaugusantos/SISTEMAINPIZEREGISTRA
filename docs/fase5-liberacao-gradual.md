# Fase 5 — Liberação gradual (rollout por grupo de implantação)

Continuação da Fase 4 (feature flags, `docs/fase4-feature-flags.md`).
Adiciona um "dial" fino de audiência para uma flag em `estado_padrao ==
"ligado"`, monitoramento por grupo, e interrupção manual/automática do
rollout sem derrubar o sistema.

## Grupos de implantação (`FeatureFlag.estagio_rollout`)

Cumulativos — cada estágio inclui as audiências dos anteriores:

1. **`ambiente_interno`** — só a(s) organização(ões) marcada(s) com
   `Organizacao.ambiente_interno = true` (ver `PATCH
   /v1/admin/saas/organizacoes/{id}`, campo `ambiente_interno`).
2. **`administradores`** — + qualquer usuário administrador/superadmin,
   em qualquer organização.
3. **`organizacoes_piloto`** — na prática, já é o mecanismo de override
   por organização da Fase 4 (`FeatureFlagOrganizacao`), que continua
   valendo em **qualquer** estágio (é assim que dá pra testar com um
   cliente antes de mexer no estágio global). Este nome de estágio é
   sobretudo documentacional/ordinal.
4. **`percentual_limitado`** — + um percentual (`percentual_rollout`,
   0–100) das organizações. Seleção **determinística** por hash estável
   de `codigo da flag + organizacao_id` (`sha256`, nunca `hash()` nativo
   do Python — instável entre processos) — uma organização não "pisca"
   entrando e saindo a cada request.
5. **`liberacao_geral`** — todo mundo. **Padrão de toda flag nova** —
   preserva o comportamento de sempre (Fase 4: `estado_padrao == "ligado"`
   = ligado pra todo mundo) pra quem não usa rollout gradual.

Avançar (ou recuar) estágio: `PATCH /v1/admin/feature-flags/{codigo}/rollout`.

## Monitoramento por grupo

`GET /v1/admin/feature-flags/{codigo}/monitoramento?horas=24`:

- **Uso, erros, falhas de integração, tempo de resposta médio** — por
  grupo, vindos de `FeatureFlagEvento`. "Uso" é registrado
  **automaticamente** por `flag_ativa_para_organizacao` sempre que libera
  a flag pra alguém (zero instrumentação extra por flag). "Erro"/"falha de
  integração" são **opcionais**, reportados pelo próprio código atrás da
  flag via `app.feature_flags.registrar_resultado_flag(...)` depois de
  tentar a operação de verdade — ver `app/ia_sombra.py` (flag
  `rag-local-ia-sombra`) para o exemplo de referência.
- **Reclamações** — proxy via `ProblemaVersaoSistema` (relato livre do
  operador na Central de Atualizações) cujo `modulo` bate com
  `FeatureFlag.modulos_envolvidos`, desde `data_ativacao`. Não é uma ação
  atômica ligada à flag (reclamação não referencia flag nenhuma), então é
  aproximação, não contagem exata.
- **Impacto nos módulos existentes** — não tem uma métrica isolada própria
  nesta fase; o proxy é olhar os mesmos módulos em
  `/v1/admin/observabilidade/eventos` (erros gerais daquele componente) e
  comparar antes/depois de avançar o estágio. Isolar causalidade
  (esta flag especificamente, não outra mudança concorrente) ficou fora
  do escopo — ver "Fora de escopo" abaixo.

## Interrupção do rollout — critério de aceite

> Uma funcionalidade problemática pode ter sua expansão interrompida sem
> retirar o sistema do ar.

`interromper_rollout()` (`app/feature_flags.py`), chamada tanto pela
interrupção manual (`POST /{codigo}/interromper`, superadmin) quanto pela
automática:

- **Recua um estágio** (ex.: `percentual_limitado` → `organizacoes_piloto`)
  — reduz a exposição sem tirar quem já tinha acesso num estágio mais
  seguro.
- Se já estiver no estágio mínimo (`ambiente_interno`) e mesmo assim
  seguir com erro, **desativa a flag inteira** (`ativo = false`,
  kill-switch já existente da Fase 4) — não há pra onde recuar.
- Sempre grava `pausado_em`/`pausado_motivo`/`pausado_por` e um
  `EventoAuditoria`. `POST /{codigo}/retomar` limpa a marca — não
  readianta o estágio sozinho, é decisão explícita de quem retoma.

### Circuito automático (`avaliar_circuito_flags`)

Rodado a cada hora pelo worker (tarefa `feature_flags.avaliar_circuito`,
mesma cadência de `TAREFAS_MANUTENCAO_HORARIA`). Para cada flag ativa com
`limite_taxa_erro` configurado e ainda não pausada: olha a janela móvel
dos últimos `JANELA_CIRCUITO_MINUTOS` (60min — cobre o intervalo
continuamente mesmo rodando só 1x/hora); com amostra mínima de
`limite_eventos_minimo` (evita reagir a ruído estatístico de poucos
eventos), interrompe se `(erro + falha_integracao) / total > limite_taxa_erro`.

## Fora de escopo (registrado)

- Isolar causalidade de "impacto nos módulos existentes" (esta flag
  especificamente, controlando por outras mudanças concorrentes) — o
  proxy é comparação manual via observabilidade geral.
- Circuito automático com granularidade menor que 1h — depende da
  cadência única de tarefas periódicas que já existe
  (`TAREFAS_MANUTENCAO_HORARIA`); um scheduler mais fino ficou fora do
  escopo desta fase.
- UI dedicada para marcar `Organizacao.ambiente_interno` — feito hoje via
  `PATCH /v1/admin/saas/organizacoes/{id}` direto (mesmo padrão de
  qualquer outro campo de `OrganizacaoUpdate`); um toggle na tela de SaaS
  fica pra quando precisar trocar com frequência.
