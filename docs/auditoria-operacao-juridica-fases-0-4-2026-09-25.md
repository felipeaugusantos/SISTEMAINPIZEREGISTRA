# Operação Jurídica — auditoria e implantação das Fases 0–4

Data da revisão: 25/09/2026

Base local e `origin/main` no início: `355692e`

Migration base: `g9b0c1d2e3f4`

Nova migration: `h0c1d2e3f4g5`

## Fase 0 — inventário e riscos comprovados

| Capacidade | Estado anterior | Evidência | Risco/lacuna comprovada |
|---|---|---|---|
| Origem RPI e sugestão automática | Implementada | `app/api/juridico.py`, `_classificar_despacho` e `executar_motor_organizacao` | Regras específicas por despacho não possuíam catálogo homologado com vigência, fonte, checklist e evidência. |
| Criação manual e confirmação humana | Implementada | rotas `POST /prazos` e `PATCH /prazos/{id}` | Ao confirmar item `historico`/`duplicado`, o fluxo genérico mudava o status para `pendente`, reabrindo referência encerrada. |
| Cálculo de vencimento | Implementado | `calcular_vencimento` | Feriados nacionais eram tratados, mas não havia cadastro auditável de suspensão/indisponibilidade oficial extraordinária. |
| Prazo interno de segurança | Ausente | modelo `PrazoJuridico` tinha apenas `vencimento_em` | Não era possível antecipar a operação sem adulterar o prazo legal. |
| Responsável, escalonamento e alertas | Implementados | `PrazoJuridico`, `_notificar` e motor horário | Política de responsável/checklist não era configurável nem explícita na tela jurídica. |
| Checklist | Implementado/parcial | rotas `/checklist` e templates `CHECKLIST_PADRAO` | Checklist da regra aplicável não nascia automaticamente a partir de uma fonte homologada. |
| Entrega, protocolo e documentos | Implementados | `/prazos/{id}/entregas` e `DocumentoEntregaJuridico` | Hash era persistido, mas tamanho do arquivo não era guardado. |
| Trilha de auditoria | Implementada | `EventoJuridico` e `EventoAuditoria` | Não havia ledger próprio de cada execução do motor, sucesso/falha e resultado. |
| Lista, kanban e calendário | Implementados | `admin-juridico.html/js` | Vencimento legal e marco operacional não podiam ser distinguidos. |
| Isolamento por organização | Implementado nas tabelas existentes | filtros por `organizacao_id` e RLS das migrations jurídicas | A nova observabilidade também precisa de RLS tenant; catálogos globais devem ter leitura geral e escrita exclusiva de superadmin. |
| Worker e periodicidade | Implementados | `app/worker.py`, tarefa `juridico.executar_motor` no loop independente | Falhas iam para retry/DLQ, mas não eram consultáveis pelo painel jurídico. |
| Paginação/lotes | Implementados | agenda paginada e motor limitado a 2.000, mais marca de movimentação avaliada | O limite é sinalizado por `backlog_no_limite`; não foi removido para evitar execução sem limite. |

Decisão: preservar o classificador existente como fallback. O catálogo novo só prevalece quando a regra estiver ativa, homologada, dentro da vigência e ligada ao código estruturado do despacho. Nenhuma regra jurídica foi inventada ou pré-carregada.

## Fase 1 — fonte de verdade dos prazos

- Criado catálogo global `regras_prazo_juridico`, com vigência, fonte legal, aprovador, contagem, checklist, evidências e confiança.
- Escrita limitada ao superadministrador; leitura disponível aos operadores jurídicos.
- Uma nova versão fecha somente o fim da vigência aberta anterior; demais sobreposições são rejeitadas.
- O motor prefere regra homologada vigente e mantém o classificador anterior como fallback seguro.
- A regra homologada gera automaticamente os itens de checklist e registra a origem da classificação no evento.

## Fase 2 — calendário e margem operacional

- Criado catálogo global de exceções oficiais de calendário, com período, tipo, fonte e aprovador.
- O cálculo aceita suspensões/indisponibilidades sem transformar ponto facultativo em feriado por suposição.
- Criado `vencimento_operacional_em`, separado de `vencimento_em`.
- A margem é configurável entre 0 e 30 dias úteis; o padrão é `0`, portanto o deploy não antecipa prazos existentes sem decisão humana.
- Ao mudar a margem, os marcos internos dos prazos ativos da organização são recalculados; o vencimento legal permanece intocado.

## Fase 3 — revisão humana e evidências

- A política passa a explicitar exigência de responsável, checklist, evidência, segunda pessoa e margem operacional.
- Confirmar referência histórica/duplicada/dispensada registra revisor e data, sem reabrir o prazo.
- Documentos de entrega passam a guardar tamanho em bytes, além do hash SHA-256 já existente.
- A tela distingue “Vencimento legal” de “Marco interno” e permite configurar a política conforme a permissão `legal.manage`.

## Fase 4 — observabilidade do motor

- Criado `execucoes_motor_juridico`, isolado por organização via RLS.
- Cada execução registra início, fim, status e contadores.
- Falhas revertem alterações parciais, persistem somente uma descrição sanitizada e continuam subindo para retry/DLQ.
- Criado endpoint paginado limitado (`1..100`) para histórico e resumo da última execução na tela.

## Proteções e compatibilidade

- Não houve carga automática de regras ou exceções legais.
- Não houve exclusão nem alteração em massa de prazos.
- Catálogos globais usam RLS: leitura geral autenticada e escrita somente em contexto superadmin.
- A migration possui downgrade técnico e mantém os defaults compatíveis com o comportamento anterior.
- O motor conserva lote máximo de 2.000 e o fallback existente.

## Validação exigida antes do deploy

1. `ruff check` nos arquivos alterados.
2. `alembic heads` deve retornar somente `h0c1d2e3f4g5`.
3. `pytest tests/test_juridico.py` sem falhas.
4. Em homologação PostgreSQL: `alembic upgrade head`, validar RLS com duas organizações e executar `alembic downgrade g9b0c1d2e3f4` em banco descartável.
5. Teste manual: margem `0`, margem `2`, confirmação de histórico, regra homologada e exceção de calendário.

O deploy não faz parte destas fases nesta execução; deve ocorrer somente após a validação da migration em PostgreSQL e autorização explícita.
