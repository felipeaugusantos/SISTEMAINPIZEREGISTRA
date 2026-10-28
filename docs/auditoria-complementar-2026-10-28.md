# Auditoria funcional complementar (28/10/2026)

O PR #230 recebeu oito jornadas E2E novas com APIs e PostgreSQL reais, conferindo persistência por novas consultas. CI aprovada com 2.308 testes de backend, 42 frontend e 16 E2E. As 16 jornadas também passaram na homologação temporária isolada autorizada na VPS, com banco vazio, Redis e aplicação próprios, sem portas públicas nem credenciais de produção. O hostname oficial foi resolvido somente na rede isolada. Login e MFA foram exercitados.

O PR #230 foi integrado no commit `328766eac81c6970c8800c2d600420ec188fbefc`. A publicação oficial gerou `1.2.33-2026-10-28`, com health 200 e os três serviços de aplicação no mesmo commit. O registro na Central e as evidências finais de publicação constam no relatório de entrega.

## Novos bugs corrigidos neste PR

| Falha reproduzida | Causa | Resultado |
| --- | --- | --- |
| Scripts fechava o modal após falha de rede ao salvar. | O formulário method=dialog não cancelava o submit padrão, e o erro aparecia fora do modal. | Modal permanece aberto, preserva o conteúdo e permite tentar novamente. Fecha somente depois do sucesso. |
| Categoria enviava duas operações ao repetir o salvamento. | Ausência de estado pendente e bloqueio do botão. | Uma requisição por vez, recuperação do botão ao concluir e erro dentro do modal. |
| Forma de pagamento enviava duas operações ao repetir o salvamento. | Mesma ausência de bloqueio. | Uma requisição por vez, recuperação do botão ao concluir e erro dentro do modal. |
| Produção e auditoria lançava erro JavaScript para superadmin. | Inicialização legada acessava formulário removido do template. | Inicialização condicionada aos elementos existentes. Nenhuma funcionalidade antiga foi recriada. |
| As três visões financeiras excediam a largura no celular. | Uma regra posterior de até 1.150 px sobrescrevia a coluna única prevista até 700 px. | Restauração da coluna única na última regra móvel, sem redesenhar a página. |

Os três primeiros testes falharam antes das correções: modal oculto e duas operações onde deveria haver uma. Os dois últimos problemas foram reproduzidos pela varredura real das páginas.

## Testes e limites

Cinco E2E adicionais passaram com backend real: falha de rede e nova tentativa em Scripts, persistência após recarga, cliques repetidos com latência real nos dois cadastros, carregamento da Produção e filtros nas três visões financeiras em 390, 768 e 1.440 px. A latência é introduzida antes de encaminhar a requisição. Nenhuma resposta de API é simulada nesses E2E.

A varredura autenticada cobriu 38 destinos dos menus em 1.440 e 390 px (76 visitas). Após as correções, não encontrou erro JavaScript, resposta 5xx, imagem quebrada, mensagem de erro visível ou largura excedente. Essa varredura comprova carregamento e apresentação. Não significa que todos os botões de todas as páginas foram acionados.

Uma asserção do cabeçalho falhou por precisão fracionária do Chromium (41,999996 px para uma caixa CSS de 42 px). O teste passou a aceitar diferença inferior a 0,01 px. Os dois testes do cabeçalho passaram após o ajuste. A aprovação da suíte completa é conferida na CI do PR.

As versões dos scripts e CSS alterados foram incrementadas nos templates. Foram preservados os bitmaps oficiais, regras de negócio, endpoints, permissões e autenticação. Não há novas migrations. Segurança e vulnerabilidades ficaram fora da auditoria.

Não foram disparados envio real de mensagens, emissão fiscal externa, assinatura eletrônica, coleta nacional, tarefas pesadas de IA ou alterações em dados de clientes. A disponibilidade dessas integrações e os fluxos dependentes de dados reais permanecem validações separadas. O controle do navegador local falhou ao inicializar (kernel assets: caminho não encontrado), impedindo a inspeção visual autenticada de produção nesta sessão.
