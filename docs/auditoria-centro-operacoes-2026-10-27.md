# Auditoria funcional do Centro de Operações | Zé Registra

Data: 27/10/2026. Repositório: [SISTEMAINPIZEREGISTRA](https://github.com/felipeaugusantos/SISTEMAINPIZEREGISTRA). Revisão base: `1a3845081265550d6a4530d5ed2af43810ecc097`.

Foram implementadas correções em Leads: Pesquisas e Análises e no Radar de Prospecção. A validação local terminou com 340 testes de backend e 25 testes de frontend aprovados. O código está preparado para revisão. A operação com serviços externos e dados reais ainda precisa de homologação.

## Correções implementadas

| Área | Causa identificada | Resultado |
| --- | --- | --- |
| Retorno da análise | A listagem mantinha o contexto apenas em memória, e o breadcrumb apontava para uma listagem nova. | O retorno pelo navegador e pelo breadcrumb restaura página, tamanho da página, busca, filtros, ordenação, cliente e rolagem. O contato recuperado recebe destaque e foco. O contexto também é capturado nos links de análise do diálogo de contato. |
| Paginação de Leads | Havia apenas anterior e próxima. Uma página podia ficar fora do intervalo após alterações nos resultados. | Entrada direta do número, primeira e última página, limites de 20, 50, 100 e 200 contatos e correção automática de deslocamentos inválidos. Busca e filtros acompanham as consultas. |
| Pesquisa e classificação | A busca histórica considerava a marca, mas não a atividade de uma pesquisa vinculada. Faltavam opções de risco e ordenação. | Busca também por atividade histórica. Filtro combinável por risco nas pesquisas do contato e ordenação por nome, mais recentes ou mais antigos. Foram usados os dados existentes, sem criar categorias ou mudar a classificação de clientes. |
| Exportação e cópia de e-mails | A seleção de clientes arquivados não era repassada às consultas de exportação. | CSV e cópia de e-mails respeitam o filtro de risco e a visão de arquivados, mantendo as permissões e limites existentes. |
| Consultas concorrentes | Leads ignorava novas consultas durante um carregamento. O Radar permitia que uma resposta antiga substituísse a consulta mais recente. | A última consulta prevalece nas duas telas. Em Leads, erros de consultas anteriores também são descartados. |
| Ações do Radar | Botões de escrita apareciam para perfis de consulta. Conversão possui permissão distinta de gerenciamento. | Ações de cadastro, campanhas, política, supressão, mesclagem e qualificação respeitam gerenciamento. Converter em lead respeita especificamente `prospeccao.convert`. A leitura permanece disponível. As permissões do servidor foram preservadas. |
| Consistência do Radar | A última página podia permanecer inválida quando o resultado chegava a zero. A exclusão de campanha podia mudar o filtro sem atualizar a lista. | Retorno à primeira página quando necessário, atualização da lista e do funil após excluir campanha e botão para limpar os filtros combinados. Ações dos cartões bloqueiam cliques repetidos enquanto aguardam resposta. |
| Falhas de conexão | A alteração de status de Leads podia deixar o seletor bloqueado após falha de rede. | O seletor é liberado, o valor anterior é recuperado e uma mensagem orienta a nova tentativa. |
| Identidade visual e celular | As imagens usavam recorte. O botão do assistente também deixava a imagem exceder sua área. A paginação e um elemento acessível da tabela do Radar geravam largura excedente no celular. | Imagens proporcionais, sem recorte, com o mascote contido no botão. Paginação com quebra de linha e tabela contida em sua área rolável. Os ajustes foram limitados às telas do escopo. |

A paginação de Leads continua contando contatos, conforme o contrato atual da API. Um contato pode apresentar várias linhas de pesquisa. O rótulo agora informa explicitamente “Contatos por página”, evitando confundir a quantidade de contatos com a quantidade de pesquisas.

O filtro de risco seleciona contatos com uma pesquisa no nível escolhido. O histórico das demais pesquisas desse contato continua visível. O contexto de retorno fica no histórico da aba e no armazenamento de sessão, com uma referência opaca no link. Nenhum dado de cliente é alterado para manter a navegação.

## Testes e revisão final

| Verificação | Resultado |
| --- | --- |
| Backend: Leads, Radar, triagem, cache RFB, reconciliação, score, rotas web e exclusão de pesquisas | 340 aprovados. Inclui três testes novos que executam SQL para verificar combinação de risco e atividade, arquivamento, isolamento entre organizações e escape de curingas. |
| Frontend completo | 25 aprovados. Inclui oito testes novos para paginação, retorno da análise, concorrência de buscas, permissões, cliques repetidos, filtros combinados e revisão visual. As suítes existentes de diálogo de Leads/CRM, logos, observabilidade e Carteira também passaram. |
| Qualidade de código | `ruff check app tests migrations --ignore E501` aprovado. |
| Compilação e sintaxe | `compileall -q app` e `node --check` dos três arquivos JavaScript alterados aprovados. |
| Integridade do diff | `git diff --check` aprovado. |
| Revisão visual | Telas completas com o shell administrativo em 1440 e 390 pixels. Verificados carregamento das imagens oficiais, proporção, enquadramento do mascote e ausência de rolagem horizontal da página. Capturas inspecionadas após as transições de layout. |

O backend emitiu um aviso de depreciação da integração entre Starlette TestClient e httpx. Os testes terminaram aprovados. Não houve atualização de dependências do projeto para tratar esse aviso.

Foram preservados os arquivos oficiais de imagem. Não houve redesenho, geração de imagens, alteração dos bitmaps, migração de banco ou modificação de regras de negócio. As verificações de frontend usaram APIs simuladas. Os testes de backend usaram as fixtures existentes e um banco SQLite temporário para as consultas novas. Não foram usados dados reais de clientes.

## Homologação pendente

Não houve publicação nem execução contra o ambiente de produção. Ainda precisam ser confirmados no ambiente real o desempenho com o volume efetivo de registros, as consultas PostgreSQL com os dados e índices de produção, os perfis e a marca própria de cada organização, o processamento das filas e as integrações de coleta, triagem e verificação de sites.

O cache nacional de CNPJ envolve uma operação pesada e não foi disparado. Envio de mensagens, importações reais, conversões de clientes e exclusões reais também não foram executados. Os testes automatizados cobrem os contratos e fluxos locais dessas áreas, sem comprovar a disponibilidade dos serviços externos.

Para homologar, recomenda-se usar uma organização de teste e seguir estes passos:

1. Combinar busca, risco, status e ordenação em Leads. Ir a uma página distante, abrir análises de contatos diferentes e retornar tanto pelo navegador quanto pelo breadcrumb.
2. Conferir os limites de página e comparar os contatos retornados com o CSV e a cópia de e-mails, incluindo a visão de arquivados.
3. Conferir perfis de consulta, gerenciamento e conversão no Radar. Executar as operações em dados de teste e acompanhar o término das tarefas nas filas.
4. Conferir a marca própria da organização e o layout em computadores e celulares utilizados pela equipe.

As capturas e os logs desta entrega registram a validação local. A homologação descrita acima permanece necessária antes de afirmar que todos os fluxos estão validados em produção.
