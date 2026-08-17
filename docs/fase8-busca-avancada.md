# Fase 8 — Busca avançada

A busca mantém arquitetura multimodal para nominativa, mista, figurativa,
Nice, Viena, OCR e similaridade visual. O resultado é uma triagem técnica de
anterioridades; `revisao_humana_obrigatoria=true` e
`parecer_juridico_definitivo=false` acompanham as evidências.

O ranking combinado registra fatores, pesos, valores dos sinais e versão do
algoritmo. As modalidades podem evoluir sem misturar score de busca com risco,
registrabilidade ou decisão jurídica.

Modelos de ranking são versionados em `modelos_ranking_busca` e só podem seguir
`SHADOW → VALIDATION → ACTIVE`. O gate bloqueia qualquer queda de Recall@5,
Recall@10 ou Recall@20, aumento de falsos negativos críticos ou ausência de
validação humana. Estados permitidos: `SHADOW`, `VALIDATION`, `ACTIVE` e
`DISABLED`.

O benchmark mede Recall/Precision@5/@10/@20, MRR, p50, p95, p99 e falsos
negativos críticos. O CLI `app.cli.avaliar_busca_marcas` permanece o gate de CI;
as tolerâncias de precisão não se aplicam ao recall.

Aplicar com `alembic upgrade head`. A revisão `h85c0d1e2f34` cria o catálogo
versionado de modelos de ranking.
