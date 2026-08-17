# Operação e suporte do RC1

## Rotina diária

- Conferir `GET /health` e os containers Docker.
- Verificar falhas da sincronização RPI e a fila de retries.
- Revisar colidências pendentes e notificações aprovadas.
- Conferir parcelas vencidas e conciliação financeira.

## Incidentes

Registrar horário, request ID, tenant, endpoint, impacto e evidência. Não apagar logs nem executar alterações manuais diretamente no banco.

## Treinamento

Operadores devem concluir o fluxo lead → proposta → aceite → pagamento → documentos → protocolo. O financeiro deve operar apenas com as permissões `finance.*`; decisões jurídicas permanecem no módulo jurídico.

## Suporte

Prioridade crítica: indisponibilidade, acesso cruzado, duplicidade financeira ou comunicação externa indevida. Prioridade alta: falha de RPI, assinatura ou SLA. Demais solicitações entram na fila operacional.
