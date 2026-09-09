# Fase 2 — Central de atualizações

## Resultado

A rota `/admin/atualizacoes` apresenta a versão realmente informada ao processo da API pelo deploy e as versões publicadas no cadastro técnico da Fase 1. O acesso exige autenticação e a permissão básica `dashboard.view`.

## Limite de segurança

A API funcional `/v1/admin/atualizacoes` usa um DTO próprio. Ela não reutiliza a resposta técnica da Fase 1 e não retorna commit, migration, hash, plano de rollback, riscos internos, instruções técnicas, autores ou o texto das evidências. Das evidências, expõe somente as quantidades por resultado.

## Ações do operador

- Correções críticas e comuns pedem confirmação de leitura.
- Correções críticas nunca podem ser adiadas.
- O adiamento, quando autorizado no cadastro da versão, vale por sete dias na interface e aceita de 1 a 30 dias na API.
- O relato de problema aceita categoria, módulo afetado e descrição. Credenciais e campos extras são rejeitados, e o texto do relato não é copiado para a auditoria.

Leitura e adiamento são individuais. Todos os estados e relatos incluem `organizacao_id` e estão protegidos por RLS.

## Versão implantada

`docker/deploy.sh` exporta `APP_VERSION` antes de recriar os serviços. O `compose.yaml` entrega esse valor à API, worker e rpi-sync. Assim, a tela não deduz a versão pelo último cadastro publicado.

## Operação

1. Cadastrar e publicar a versão pela API técnica da Fase 1, incluindo impacto e opção de adiamento.
2. Executar o deploy normal para que `APP_VERSION` receba a tag criada.
3. Abrir `/admin/atualizacoes` com um usuário comum e conferir a marca “Em execução”.

Não há ativação opcional de funcionalidade nesta fase; isso permanece reservado para feature flags em uma etapa posterior.
