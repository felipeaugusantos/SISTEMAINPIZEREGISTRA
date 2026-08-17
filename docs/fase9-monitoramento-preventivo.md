# Fase 9 — Monitoramento preventivo

O módulo de vigilância compara semanalmente novas publicações RPI com as marcas monitoradas. A comparação registra fatores explicáveis de nome, classes Nice e códigos de Viena, além de um score determinístico de risco.

## Fluxo seguro

1. A execução semanal é identificada por organização e semana ISO; repetir a execução não cria colisões duplicadas.
2. Cada colisão entra como `pendente` com evidências e justificativa, aguardando fila de revisão humana.
3. O operador pode aprovar, descartar ou marcar falso positivo, sempre informando justificativa.
4. A comunicação só é enfileirada quando existe preferência ativa, canal habilitado, regra/evidência, justificativa e aprovação humana. A fila usa chave idempotente por colisão/canal.
5. `historico_alertas_vigilancia` e `vigilancia_execucoes` preservam o histórico para relatórios e auditoria.

## API principal

- `POST /v1/admin/vigilancia/executar-semanal`
- `GET /v1/admin/vigilancia/relatorios`
- `PATCH /v1/admin/vigilancia/colidencias/{id}`
- `POST /v1/admin/vigilancia/colidencias/{id}/falso-positivo`
- `POST /v1/admin/vigilancia/colidencias/{id}/comunicar`
- `GET/PUT /v1/portal/vigilancia/preferencias`
- `GET /v1/portal/vigilancia/colidencias`

Os canais suportados são `portal`, `email` e `whatsapp`; o padrão é somente portal. O envio efetivo permanece separado da geração da fila, permitindo integração com provedores e retentativas sem burlar os gates.
