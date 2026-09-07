# Fase 5 — indicadores

## Objetivo

Medir a jornada implantada entre Comercial, Financeiro e Jurídico usando uma
única coorte: propostas aceitas no período selecionado. Isso evita comparar
estoques de datas diferentes e transformar contagens isoladas em uma falsa
taxa de conversão.

## Indicadores implantados

- volume em cada etapa: aceite, pagamento, recebimento jurídico e protocolo;
- taxa de passagem entre etapas e conversão total entre aceite e protocolo;
- valor contratado, visível apenas a quem possui acesso financeiro;
- tempo médio entre aceite e pagamento, pagamento e recebimento jurídico,
  recebimento e protocolo, além do tempo da jornada completa;
- gargalos pendentes em pagamento, recebimento jurídico e protocolo;
- filtros de 30, 90, 180 e 365 dias.

O período sempre considera a data de aceite da proposta. Registros com datas
invertidas são ignorados nas médias, mas continuam nas contagens. As etapas e
os valores respeitam as permissões `finance.view` e `legal.view`, e todas as
consultas permanecem isoladas por organização.
