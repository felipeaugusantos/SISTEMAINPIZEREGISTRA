# Auditoria funcional da interface

Data: 27/10/2026. Base: produção 1.2.32, commit c0b7178a948af91c19ed8c55e4ef55ac1391986d. As correções desta auditoria dependem de revisão e publicação posterior. Não houve deploy nesta etapa.

## Falhas reproduzidas e corrigidas

| Área | Falha | Correção |
| --- | --- | --- |
| CRM | Busca de cliente do lembrete falhava ao executar o temporizador. | Captura do termo antes do callback adiado. |
| NFS-e | Busca de cliente falhava pelo mesmo motivo. | Captura do termo antes do callback adiado. |
| Central de Atualizações | Relato salvo não limpava o formulário nem mostrava a confirmação. | Referência do formulário preservada durante a requisição. |
| Conciliação | Importação OFX bem-sucedida apresentava erro e não recarregava a lista. | Referência do formulário preservada durante a requisição. |
| Barra lateral | Preferência de recolhimento do desktop impedia acesso aos submenus no celular. | Recolhimento aplicado somente a partir de 981 px, com restauração da preferência ao voltar ao desktop. |
| Notificações | Erros HTTP eram ignorados e cliques repetidos podiam duplicar operações. | Verificação da resposta, erro visível e bloqueio temporário dos controles, restaurados ao terminar. |
| WhatsApp | Revogação concluída apresentava erro e não atualizava os dados. | Referência do formulário preservada durante a requisição. |
| Jurídico | Confirmação de reprocessamento permanecia aberta após sucesso. | Referência do formulário preservada para encerrar a confirmação e permitir a atualização do painel. |

O evento DOM deixa de oferecer `currentTarget` depois que o handler síncrono termina. Seis falhas vinham do uso dessa propriedade dentro de temporizadores ou depois de `await`.

## Arquivos e escopo

Foram alterados os oito scripts correspondentes em `app/web/static`: `admin-crm.js`, `admin-financeiro-nfse.js`, `admin-atualizacoes.js`, `admin-financeiro-conciliacao.js`, `admin-shell.js`, `admin-notificacoes.js`, `admin-whatsapp.js` e `admin-juridico.js`. Os 42 templates administrativos receberam incremento da versão do script compartilhado. Nos módulos afetados também foi incrementada a versão do script próprio para evitar reutilização de conteúdo antigo pelo navegador.

Foi acrescentado `tests/frontend/functional-audit.test.cjs` com dez cenários. Nenhuma imagem oficial, CSS, regra de negócio, endpoint, permissão, autenticação, banco ou migração foi alterada. Segurança e vulnerabilidades ficaram fora do escopo solicitado.

## Validação

As oito falhas foram reproduzidas antes da correção. A suíte completa `node --test tests/frontend/*.test.cjs` passou com 42 testes e nenhuma falha. Os dez novos cenários cobrem as oito falhas, cliques repetidos nas notificações, ambos os botões de ação em lote e recuperação após falha de rede. `git diff --check` passou.

A suíte existente também verifica a identidade oficial em 42 templates e 48 rotas, navegação, recarga, marca carregada posteriormente, cabeçalho em seis páginas e quatro larguras, recolhimento e expansão. A proteção contra a imagem antiga substituir o personagem oficial permanece aprovada.

## Limitações

Os testes usam Chromium com HTML e JavaScript reais, APIs simuladas e identidade de teste. Não comprovam integrações externas ou operações reais em produção. No teste jurídico, as funções de recarga são substituídas para isolar o encerramento da confirmação. Não houve envio real de mensagens, importação financeira, reprocessamento jurídico ou alteração de dados de clientes.

Esta é uma auditoria dirigida aos fluxos examinados. A aprovação dos testes não afirma ausência de outros bugs em todo o sistema. A versão publicada permanece 1.2.32 até uma publicação autorizada destas alterações.
