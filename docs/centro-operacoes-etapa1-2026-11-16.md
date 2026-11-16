# Centro de Operações | Correções e primeira etapa de usabilidade

## Base e escopo

Entrega em `feat/centro-operacoes-usabilidade-etapa1`, PR #245, empilhado sobre #244 (`fix/centro-operacoes-auditoria-2026-11-16`). A main foi conferida em `d474bbed1cd9`; #244 continua aberto. A busca dos PRs abertos encontrou #244 e atualizações de dependências, sem outra mudança funcional pendente nesses arquivos.

Nenhum merge, deploy, migração, operação destrutiva ou mudança de regras de negócio. As propostas M05, M06, M07, M08, M10, M11 e M12 não foram implementadas. Dashboard geral e site institucional não foram alterados.

## Correções funcionais

| Problema | Causa localizada | Tratamento |
| --- | --- | --- |
| Salto ao retornar da Análise | Restauração automática do navegador concorria com a manual; a mensagem de carregamento era removida depois da rolagem, alterando a altura da página | Restauração manual somente na lista, após renderização, paginação, resumo e mensagem final. O teste legado e os novos verificam a posição real no Chromium |
| Retorno a partir do diálogo | A posição da página era capturada, mas não a aba e a rolagem interna do diálogo | Captura e reabertura do mesmo contato/aba, com restauração da rolagem interna e foco sem deslocamento |
| Borda verde persistente | `.lead-returned` em `operations-list.css`, aplicada à linha e nunca removida | Destaque termina em 5 segundos ou na primeira interação de mouse/teclado. Foco acessível e cores legítimas foram preservados |
| Filtros antigos após recarga | Token `retomar` continuava na URL e recuperava o mesmo snapshot | Token consumido e removido da URL. Histórico passa a acompanhar filtros aplicados e posição |

A classe verde foi identificada no código e o seu ciclo de vida foi testado no navegador. Isso comprova o destaque de retorno descrito, sem afirmar que qualquer outra marca verde de produção tenha a mesma origem.

## Melhorias implementadas

- **M01:** criar, aplicar, renomear e excluir até 20 visões locais por usuário e organização. Filtros estruturados, ordenação, tamanho/página e modo são reutilizados. Chips permitem remover filtros individuais, além da limpeza geral existente. A busca livre não é persistida nas visões. Nomes personalizados devem ser rótulos de trabalho, sem dados de clientes. Sem sincronização entre dispositivos. Armazenamento bloqueado é informado e não impede o filtro normal.
- **M02:** busca por contato/empresa na API existente de Leads, com até 20 resultados e orientação para refinar. Seleção explícita, identificação por empresa/ID, confirmação antes do POST, tratamento de falhas e bloqueio de envio duplicado. Digitar novamente invalida a seleção anterior. O backend mantém permissão, organização e validação do destino. O fluxo de cliente novo continua disponível.
- **M03:** resumo no topo do diálogo com responsável, data/resultado do último contato e próximo retorno agendado. Atraso só é exibido quando existe uma data vencida. Valores ausentes são descritos como não informados. Atalho para Atendimento Comercial. Usa os campos e histórico existentes, sem novo sistema de tarefas.
- **M04:** seleção pesquisável de todas as 45 classes, checkboxes, contagem, lista de selecionadas, remoção individual e limpeza. Busca sem distinção de acentos. O select original e `classes_nice` permanecem como contrato de envio. Nenhuma seleção mantém a semântica de pesquisar em todas as classes.
- **M09:** abas com setas/Home/End e relação tab/painel, diálogos nomeados, foco de retorno sem rolagem, indicação de foco pontual, checklist da Análise conduzindo o foco ao cabeçalho da etapa. Validados teclado, erros, carregamento e contraste do novo resumo. Isso não equivale a certificação completa de acessibilidade nem teste com leitor de tela real.

## Compatibilidade

`lead-dialog.js` é compartilhado. As novas interfaces, relações de acessibilidade e restauração de foco são ativadas por `operationsMode: true` apenas na tela de Leads. O CRM mantém sua configuração padrão. Os testes existentes de CRM/PII, inicialização, salvar e reabrir continuam no workflow de regressão.

Sem novas dependências de produção ou de desenvolvimento. Os módulos JavaScript novos usam as APIs nativas do navegador. Nenhum endpoint de busca novo foi necessário.

## Verificação

- 425 testes Python selecionados aprovados após as mudanças de template e implementação. Ruff, sintaxe JavaScript e `git diff --check` aprovados.
- Suíte Python completa: 2.360 aprovados, 60 ignorados, 19 falhas. Seis ocorreram na configuração de proxy do HTTPX e 13 pela indisponibilidade do PostgreSQL local. Reexecutar o arquivo do assistente sem as variáveis de proxy resultou em 20 aprovações. Nenhum desses módulos foi modificado nesta entrega.
- Testes Node/VM: retorno, privacidade, respostas fora de ordem, cliques repetidos e tratamento de falhas. Não são evidência visual.
- Workflow dedicado `operations-usability.yml`: Chromium real no GitHub, APIs simuladas, sem acesso a produção. Testa fluxos em 1440 e 390 px, retorno por breadcrumb/navegador, diálogo/aba, recarga, visões, movimentação, resumo, classes/payload e teclado/foco. Gera capturas da interface renderizada com dados fictícios.
- A rodada do commit `53e8c676b74f` aprovou os 41 testes (28 de navegador e 13 de comportamento/privacidade). Evidência: https://github.com/felipeaugusantos/SISTEMAINPIZEREGISTRA/actions/runs/43894597942 . As capturas incluem visões salvas, resumo e Consulta em dados fictícios. O resumo também usa a data do histórico quando o campo agregado do lead está ausente. Resultados por commit estão disponíveis em Checks no PR.

## Limitações e condições para revisão

A CI geral continua com o gate de auditoria de dependências ativo. A execução anterior identificou dnspython 2.9.0 / CVE-2026-128089 (correção 2.9.2) e opentelemetry-api 1.45.1 / CVE-2026-126360 (correção 1.45.2). São dependências herdadas de #244. Não foram atualizadas fora do escopo nem houve desativação do gate.

Produção, integrações externas, PostgreSQL real, desempenho com volume real, dispositivos físicos e leitor de tela não foram homologados. O teste de contraste cobre o resumo novo, não todos os elementos históricos. As capturas são de navegador real com dados simulados, não de produção.

Revisar #244 e depois a etapa #245. Antes de integrar, tratar o bloqueio da CI geral e homologar os fluxos de escrita em organização de teste. Não reexecutar exclusões ou movimentações em dados reais apenas para validar esta entrega.
