# Fase 8 - Busca avancada e inteligencia visual

## Entregas validadas

- busca nominativa, mista e figurativa;
- consulta e afinidade pela Classificacao de Viena;
- assinatura visual reproduzivel e OCR quando o componente estiver disponivel;
- score combinado de similaridade visual, OCR e Viena;
- fatores explicaveis, pesos e versao do ranking;
- benchmark com Recall@5/10/20, Precision@5/10/20, MRR e falso negativo critico;
- validacao humana obrigatoria antes de qualquer conclusao juridica;
- estados de modelo `SHADOW`, `VALIDATION`, `ACTIVE` e `DISABLED`;
- gate de regressao que bloqueia a publicacao quando o recall cai.

## Evidencias do gate

Validacao executada em 2026-08-17:

- `tests/test_phase7_visual.py`;
- `tests/test_search_benchmark.py`;
- `tests/test_search_ranking.py`;
- `tests/test_trademark_viena.py`;
- `tests/test_trademark_phase2.py`;
- total: 28 testes aprovados.

O gate foi aprovado: uma queda de Recall@5, Recall@10, Recall@20 ou MRR bloqueia
a publicacao do ranking e os resultados permanecem sujeitos a revisao humana.
