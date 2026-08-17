# Fase 7 — Busca avançada e inteligência visual

- A busca visual retorna assinatura reproduzível, OCR quando disponível e score combinado com fatores e versão.
- A Classificação de Viena continua sendo consultada por códigos e afinidade hierárquica.
- Toda similaridade é indicador técnico; a resposta exige revisão humana.
- O benchmark aceita uma baseline e calcula um gate que bloqueia a publicação quando Recall@5/10/20 ou MRR regredir.
- Os estados de modelo existentes (`SHADOW`, `VALIDATION`, `ACTIVE`, `DISABLED`) permanecem governados pelos gates técnicos e humanos.

O endpoint de benchmark retorna `publicacao_permitida` e lista as regressões encontradas.
