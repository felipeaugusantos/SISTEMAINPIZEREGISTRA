# Fase 2 - Operacao RPI e processos

## Implementado nesta etapa

- validacao estrutural do XML completo antes da importacao;
- rejeicao de arquivo vazio, ilegivel, sem cabecalho valido ou sem registros;
- checksum SHA-256 e tamanho do arquivo registrados em `rpi_importacoes`;
- deteccao de queda abrupta, edicao pulada, ausencia de classes/movimentacoes e resultado nao reprodutivel;
- importacao idempotente por chaves de origem e conflitos controlados;
- consulta RPI paginada com filtro por situacao e indice composto para grandes volumes;
- fila Redis com idempotencia, retries, backoff, recuperacao de jobs em processamento e metricas;
- alertas de integridade e painel de saude da RPI;
- ordenacao da carteira por prioridade de situacao do INPI.

## Protecao contra contaminacao

O XML e validado integralmente antes de o leitor ser entregue ao importador. Assim, arquivo truncado ou sem registros nao inicia gravacoes em `processos`, `movimentacoes`, `titulares` ou classificacoes.

## Reprocessamento

Use `--forcar` somente para reprocessamento operacional autorizado. As chaves de origem e os `ON CONFLICT` preservam a idempotencia. Uma mesma RPI sem `--forcar` e ignorada quando ja existe registro de importacao.

## Evidencias de validacao do gate

Validacao executada em 2026-08-17:

- `tests/test_rpi_integrity.py`: checksum, arquivo corrompido, arquivo sem registros, queda abrupta, edicao pulada e reprocessamento idempotente;
- `tests/test_rpi_sync.py`: download atomico e substituicao segura de pacote parcial;
- `tests/test_rpi_admin.py`: solicitacao manual e reprocessamento de execucao com falha;
- `tests/test_queueing.py`: retry com backoff exponencial e limite configurado;
- `tests/test_health.py`: endpoint e estados de saude da RPI;
- total dos testes direcionados: 37 aprovados.

Status do gate: **aprovado em homologacao**. A consulta RPI possui paginacao limitada, filtro por situacao e a importacao repetida preserva as chaves de origem sem duplicidade.

## Operacao

A validacao de SLA deve ser executada com o volume representativo de producao antes do release.
