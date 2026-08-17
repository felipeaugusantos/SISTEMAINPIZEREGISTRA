# Fase 5 — RPI e processos

## Integridade e idempotência

Cada importação é identificada por `numero_rpi + tipo + SHA-256`. O XML é baixado
atomicamente, validado por completo e convertido em memória antes de qualquer
escrita jurídica. Um checksum já registrado é ignorado, inclusive em
reprocessamento forçado; conflitos de checksum geram uma nova tentativa auditável.

Arquivos vazios, incompletos, ilegíveis ou com zero registros não chegam às tabelas
de processos, titulares, classes ou movimentações. Anomalias (queda abrupta,
edição pulada, ausência de classes/movimentações e resultados não reprodutíveis)
são armazenadas e geram alerta.

## Rastreabilidade e operação

`rpi_importacoes_historico` preserva cada tentativa com request ID, checksum,
contagens, status, anomalias e erro. Logs do importador são JSON e usam os eventos
`RPI_IMPORT_SKIPPED`, `RPI_IMPORT_COMPLETED` e `RPI_IMPORT_FAILED`. As movimentações
continuam vinculadas ao processo e à fonte `RPI`, que representa a Revista da
Propriedade Industrial do INPI como fonte oficial dos eventos jurídicos.

## Consulta e carteira

A consulta da RPI retorna paginação com total, limite e `tem_mais`, aceita filtro de
situação normalizada do INPI e exibe a integridade da revista. Processos monitorados
ganharam prioridade (`alta`, `media`, `baixa`) e a ordenação aplica a prioridade
antes da situação jurídica. Alterações permanecem auditadas pelo histórico da
carteira e pelos eventos operacionais.

## Migração

Aplicar com `alembic upgrade head`. A revisão `d41e6f7a8b90` cria o histórico de
importações, campos de rastreabilidade e prioridade dos processos monitorados.
