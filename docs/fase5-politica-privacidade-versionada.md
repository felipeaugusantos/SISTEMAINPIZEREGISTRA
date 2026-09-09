# Fase 5 — Política de privacidade versionada

## Resultado

A versão de política deixou de ser um texto livre dentro da configuração da
organização. Cada organização passa a ter registros próprios, com versão,
documento imutável, hash SHA-256, autoria, aprovação, vigência, motivo da
alteração e indicação sobre a necessidade de um novo consentimento.

## Estados e publicação

- `rascunho`: pode ser editado e ainda não aparece publicamente;
- `publicada`: é a única política vigente da organização e não pode ser editada;
- `revogada`: permanece disponível no histórico e não pode ser editada nem
  republicada.

A publicação exige confirmação explícita. Dentro da mesma transação, a política
vigente anterior é revogada, o rascunho é publicado, o ponteiro legado da
organização é atualizado e um evento de auditoria é criado. Um índice parcial no
banco impede que duas políticas sejam publicadas simultaneamente para a mesma
organização. Um trigger protege políticas publicadas e revogadas contra edição
ou exclusão direta no banco.

## Migração e consentimentos históricos

A migration `i20s5u1p842` cria um registro para a versão atual de cada
organização e para cada versão histórica encontrada nos consentimentos. Os
valores são preservados exatamente como foram gravados; nenhum `Lead` é
atualizado. Depois da carga, uma chave estrangeira composta assegura que todo
consentimento continue ligado à sua organização e à versão original.

As versões importadas referenciam `/privacidade`, que mantém o texto legado. Uma
nova versão pode guardar o conteúdo diretamente ou uma referência pública HTTPS
ou interna, nunca ambas.

## Regra de novo consentimento

Marcar `requer_novo_consentimento` não altera aceites antigos. O painel apenas
contabiliza titulares que aceitaram outra versão (ou nenhuma versão). O novo
aceite deve ocorrer em uma nova interação pública; uma ação administrativa não
renova consentimento automaticamente.

## API e interface

- `GET /v1/admin/politicas-privacidade`: histórico, vigente e pendências;
- `POST /v1/admin/politicas-privacidade`: cria rascunho;
- `PUT /v1/admin/politicas-privacidade/{id}`: edita somente rascunho;
- `POST /v1/admin/politicas-privacidade/{id}/publicar`: publica com confirmação;
- `GET /v1/tenant/politica-privacidade/vigente`: documento público sem dados
  administrativos.

O painel de confiabilidade contém criação, edição e publicação. A página pública
de privacidade consulta somente o endpoint público e renderiza conteúdo como
texto, sem interpretar HTML.

## Rollback técnico

Antes do downgrade, exportar a tabela `politicas_privacidade`. O downgrade
remove primeiro a chave estrangeira dos consentimentos, depois o trigger e sua
função, e por fim a tabela. Ele não altera `organizacoes` nem `leads`; portanto,
o ponteiro legado da organização e os consentimentos históricos continuam
presentes. O conteúdo de versões novas deixa de existir após o downgrade, razão
pela qual o backup é obrigatório.

## Validação

Os testes cobrem validação do documento, isolamento por organização, hash,
imutabilidade, publicação e revogação atômicas, auditoria, preservação de
consentimentos antigos, contagem de novos aceites, resposta pública restrita e
rollback da migration.
