# SLOs e critérios de incidente

Achado FASE6-10 da auditoria (04/09/2026): não existia nenhum SLO técnico
formal (só o SLA comercial de protocolo em 24h, `docs/fase4-propostas-aceite-protocolo.md`)
nem um objetivo numérico de disponibilidade/latência/erro. Este documento
fixa os primeiros SLOs -- calibrados pelos limiares que o sistema já
verifica de verdade (`app/settings.py`, `app/alertas_plataforma.py`), não
números escolhidos a priori. Revisar depois de alguns meses de dado real de
produção.

## SLOs de plataforma

| Métrica | Objetivo | Onde é medido | O que acontece se violar |
|---|---|---|---|
| Erro de API (24h) | < 5% das requisições | `EventoOperacional` via `app/observability.py`, agregado em `app/alertas_plataforma.py::verificar_saude_plataforma` | Alerta `API_LATENCIA_ERRO_ALTA` (severidade aviso), e-mail para `ADMIN_EMAIL` |
| Latência média de API (24h) | < 2000 ms | idem | idem |
| Fila de falhas do worker | < 5 jobs simultâneos na dead-letter queue | `FAILED_KEY` (Redis), checado a cada ciclo de manutenção | Alerta `FILA_FALHAS_ALTA` (crítico se ≥ 4x o limite); itens visíveis/tratáveis em `GET /v1/admin/fila/falhas` |
| Disponibilidade do Redis/fila | sempre disponível | `status_fila()` | Alerta `FILA_INDISPONIVEL` (crítico) |
| Atraso da sincronização RPI | < 12h (`RPI_STALE_HOURS`) | `app/rpi/health.py::avaliar_saude_rpi` | Alerta `RPI_DESATUALIZADA` (crítico se status "erro", aviso se "atrasado") |
| Idade do backup do banco | < 26h (`ALERTA_BACKUP_MAX_HORAS`) | arquivo mais recente `inpi-*.dump` em `backups/` (montado só leitura no worker) | Alerta `BACKUP_AUSENTE` (crítico) |
| Notificação de prazo jurídico crítico | sempre notificado por e-mail quando há destinatário configurado | `app/api/juridico.py` (motor de prazos) + `app/emailing.py::enviar_alerta_prazo_juridico` | Sem alerta próprio -- é o próprio SLO que a notificação existe (achado 5.9 da auditoria anterior) |

Todos os limiares acima são configuráveis via variável de ambiente
(`app/settings.py`) sem precisar mexer em código -- ajustar conforme o
volume real de produção for revelando o comportamento normal do sistema.

## O que ainda não tem SLO formal

- **p95/p99 de latência por endpoint** -- hoje só medimos duração média
  agregada em 24h. Um endpoint lento isolado (ex. um relatório pesado) pode
  passar despercebido enquanto a média geral continua saudável. Fica como
  item de follow-up quando houver painel de latência por rota.
- **Recall/precisão do motor de busca** -- existe gate de regressão
  (`app/cli/avaliar_busca_marcas.py::avaliar_regressao`), mas não um SLO de
  qualidade absoluta em produção contínua (só em benchmark manual). O
  benchmark real rodado em 04/09/2026 mediu Recall@20 = 33,7% e p95 de
  latência de busca = 58,2s -- ambos muito abaixo de qualquer meta razoável
  (referência interna discutida: Recall@20 ≥ 70%, p95 ≤ 15s) -- achado
  registrado à parte, correção de causa raiz ainda pendente (não é escopo
  deste documento).

## Critérios de incidente

Complementa a classificação de prioridade de suporte já existente em
`docs/operacao-rc1.md` (indisponibilidade/acesso cruzado/duplicidade
financeira = crítico; falha de RPI/assinatura/SLA = alta). Regras
adicionais, específicas de infraestrutura:

- **Incidente crítico**: qualquer alerta de plataforma com
  `severidade="critico"` (`FILA_INDISPONIVEL`, `BACKUP_AUSENTE`,
  `RPI_DESATUALIZADA` com status "erro", `FILA_FALHAS_ALTA` acima de 4x o
  limite) parado há mais de 1h sem resolução -- abrir incidente, não
  esperar o próximo ciclo de manutenção.
- **Incidente alto**: `API_LATENCIA_ERRO_ALTA` sustentado por mais de 3
  ciclos horários seguidos (ou seja, não é um pico passageiro).
- **Não é incidente**: um alerta que se resolve sozinho no ciclo seguinte
  (`resolver_alerta_plataforma` marca `resolvido_em` automaticamente quando
  a checagem volta ao normal) -- só documentar se aconteceu por
  curiosidade/tendência, não abrir chamado.

## Registro de incidente

Mesma disciplina de `docs/operacao-rc1.md`: horário, request ID, tenant,
endpoint, impacto e evidência. Nunca apagar logs nem alterar dado
manualmente no banco de produção sem um plano revisado -- ver
`docs/operacao-recuperacao-desastre.md` para o procedimento de restauração
de backup quando o incidente exigir.
