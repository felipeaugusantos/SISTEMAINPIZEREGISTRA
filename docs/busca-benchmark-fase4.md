# Busca V4 — ranking, benchmark e performance

## Escopo e separação de scores

O `score_busca` serve exclusivamente para ordenar ocorrências. Ele não é probabilidade de
registro e não é o score determinístico de risco. Cada parcela possui regra, peso, evidência e
número do processo. Os pesos e a versão ficam centralizados em `app/search_ranking.py`.

O orçamento nominativo domina os fatores contextuais. Classe, afinidade, situação ativa e alto
renome podem desempatar ou elevar um resultado, mas não eliminam candidatos nem aplicam peso
negativo. Essa decisão protege recall jurídico durante a adoção inicial.

## Governança do dataset

`data/search-benchmark.candidate.v1.json` é apenas uma base candidata extraída do acervo local.
Ela permanece com `status: pendente_revisao_especialista` e a CLI a rejeita por padrão. Para
promovê-la, um especialista deve revisar cada conflito esperado e crítico, preencher sua
identificação, papel e data, e versionar um novo arquivo aprovado. Não altere a baseline aprovada
sem registrar a justificativa da revisão.

O gate bloqueia qualquer falso negativo crítico, queda de Recall@5/10/20 ou MRR e regressões de
Precision@5/10/20 e p95 além dos limites versionados. O contrato técnico do ranking é executado
explicitamente no CI mesmo antes da promoção do dataset jurídico.

## Evidência de performance

Em 14/08/2026, `EXPLAIN (ANALYZE, BUFFERS)` foi executado no PostgreSQL local com 5.475.840
processos de marca. A consulta representativa de “Zé Registra”, classe 45, utilizou:

- `ix_processos_titulo_trgm` em três `Bitmap Index Scan`;
- `uq_classificacoes_marca_processo_sistema_codigo` em `Index Only Scan`;
- tempo de execução observado de aproximadamente 641 ms com cache parcialmente aquecido.

Como os predicados nominativos e de classe já utilizaram índices adequados, não foi criada nova
migration. A baseline candidata mediu a operação completa, incluindo contagens e carregamento dos
relacionamentos; esses números devem ser comparados sempre no mesmo ambiente.
