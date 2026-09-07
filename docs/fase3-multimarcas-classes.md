# Fase 3 — várias marcas e classes

## Decisão de arquitetura

A oportunidade continua sendo o `Lead`. Cada combinação de marca e classe Nice
é uma `PesquisaMarca` vinculada à mesma oportunidade. Isso preserva o motor de
análise, que trabalha corretamente com uma classe por execução, e evita criar
uma segunda fonte de verdade para marcas ou resultados.

## Entregas

- consulta administrativa com até 20 marcas por envio;
- até 50 combinações de marca/classe por operação;
- seleção independente de várias classes para cada marca;
- prevenção de combinações repetidas no mesmo envio;
- navegação dos resultados identificada por marca e classe;
- proposta com seleção explícita das pesquisas incluídas;
- consolidação de todas as marcas/classes no documento da proposta;
- detalhes estruturados preservados em `PropostaComercial.dados.pesquisas`;
- campos-resumo da proposta ampliados para texto, sem truncar conjuntos maiores;
- compatibilidade com o contrato legado de consulta de uma marca.

## Critério de aceite

1. Cadastrar duas marcas, cada uma com duas classes, para o mesmo cliente.
2. Confirmar quatro pesquisas na mesma oportunidade.
3. Alternar entre os quatro resultados na própria tela.
4. Criar uma proposta selecionando pelo menos uma pesquisa de cada marca.
5. Confirmar que a proposta mostra todas as marcas e associa as classes à marca correta.
6. Confirmar que consulta legada de uma marca continua funcionando.

## Migração e reversão

A migration `gz42u8b4n064` altera apenas `propostas_comerciais.marca` e
`propostas_comerciais.classes` de `VARCHAR(200)` para `TEXT`. A alteração é
compatível com os dados existentes. O downgrade volta para `VARCHAR(200)` e só
deve ser executado depois de confirmar que nenhum resumo novo ultrapassa esse limite.
