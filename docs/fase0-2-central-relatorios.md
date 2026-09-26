# Central de Relatórios — Fases 0–2

Data: 26/09/2026
Base auditada: `057920d526ac1897e760144f48fcb627f348b72e` (working tree limpo; igual a `origin/main` no início da mudança).

## Fase 0 — inventário e linha de base

### Estado anterior

- Não havia item/menu nem página `/admin/relatorios`.
- Os principais indicadores e relatórios estavam distribuídos pelas telas e APIs de cada módulo; dashboards existentes seguem sendo a fonte dos dados.
- CRM/Leads: `GET /v1/admin/leads-dashboard` e `/v1/admin/leads-dashboard/serie-temporal`; funil, resultado, conversão, motivo de perda, produtividade e metas. O primeiro endpoint descreve o estoque atual de leads não arquivados, não uma coorte temporal por data de criação. Consumidores: tela CRM e widget da visão geral.
- Financeiro: `GET /v1/admin/financeiro/dre`, `/dre/caixa` e `/lucratividade/clientes`; cada endpoint tem filtros/bases distintos. O DRE é gerencial por competência, caixa realizado usa baixa, e vencimento é previsão/aberto, não recebimento garantido. `ParcelaFinanceira` mantém acumulado pago e uma data de baixa; não representa um razão de várias baixas parciais.
- Operação jurídica: `GET /v1/admin/juridico/indicadores?dias=`; cumprimento, tempo de confirmação, carga atual por responsável e escalonamento. Janela de 1–365 dias, com controles de 30/90/180/365 na tela.
- Carteira: listagem filtrável, `GET /v1/admin/carteira/exportar.csv` (teto documentado de 5.000 linhas) e PDF por processo. A situação depende da sincronização do INPI.
- As APIs acima impõem permissões de módulo e escopo de organização. As exports, quando disponíveis, têm permissões próprias.

### Escopo deliberadamente não consolidado

Não foi criado um endpoint agregador transversal: ele duplicaria métricas, poderia misturar bases temporais diferentes e ampliaria o risco de exposição entre permissões. A Central inicialmente organiza links para as fontes existentes. NFS-e não é apresentada como relatório fiscal; a DRE existente é identificada como gerencial.

## Fase 1 — centralização

- Adicionada página administrativa `/admin/relatorios` e item de primeiro nível no menu.
- A rota exige pelo menos uma permissão de relatório: `leads.view`, `crm.view`, `finance.view`, `legal.view` ou `portfolio.view`.
- O menu se adapta a permissões de módulo; a tela esconde categorias/links não autorizados. Isso é apenas usabilidade: a autorização efetiva continua nas rotas originais dos módulos.

## Fase 2 — MVP

A Central apresenta quatro grupos prioritários com descrição do conteúdo, fonte e ressalvas:

1. CRM e produtividade;
2. DRE, caixa e lucratividade;
3. Prazos e desempenho jurídico;
4. Processos monitorados.

Os filtros permanecem nas páginas oficiais de cada módulo nesta primeira entrega. Não foi criada falsa aparência de filtro global, pois os critérios de período não são uniformes entre fontes. A próxima evolução pode padronizar intervalos depois de aprovar definições e validar o impacto nos indicadores existentes.

## Verificações

- Testes automatizados cobrem rota autenticada, negação a usuário sem permissão e presença dos quatro grupos/metadados da Central.
- Nenhuma alteração de banco ou deploy é necessário para esta entrega.
