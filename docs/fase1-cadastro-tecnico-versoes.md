# Fase 1 — Cadastro técnico de versões

## Objetivo

Manter um histórico global, estruturado e auditável das releases da plataforma.
Esta fase entrega o modelo de dados e a API administrativa. A interface, os
avisos aos operadores e a ativação opcional pertencem às fases seguintes.

## Dados registrados

Cada release contém versão única, título, problema, solução, tipo da atualização,
módulos afetados, data real da implantação, SHA completo do commit, migration
opcional, evidências estruturadas dos testes, riscos, instruções, plano de
rollback e hash SHA-256 do conteúdo técnico.

As evidências não aceitam logs livres: cada item possui nome, resultado, resumo,
quantidade e data opcional. Os modelos rejeitam campos desconhecidos e padrões
comuns de senhas, tokens, cabeçalhos de autorização, chaves privadas e segredos.
Os eventos de auditoria guardam somente metadados e hashes, nunca o texto técnico
completo.

## Estados

- `rascunho`: pode ser corrigido pelo superadministrador;
- `publicada`: visível no histórico e imutável;
- `arquivada`: retirada da consulta principal no futuro, mas preservada e
  imutável.

Publicar exige confirmação explícita, data real de implantação e ao menos uma
evidência de teste aprovada. Corrigir
uma versão publicada exige criar uma versão nova. O arquivamento é permitido
somente para uma versão publicada, exige justificativa e altera apenas os campos
de arquivamento.

Um trigger PostgreSQL impede update/delete de versões publicadas ou arquivadas,
mesmo que uma falha futura contorne a validação da API. O conteúdo canônico recebe
um hash SHA-256 recalculado a cada edição do rascunho.

## Permissões

- consulta: usuário autenticado com `production.view`;
- criação, edição, publicação e arquivamento: superadministrador da plataforma;
- banco: leitura pelo papel restrito da aplicação; escrita permitida pela RLS
  somente quando o contexto da sessão é de superadministrador.

## API

- `GET /v1/admin/versoes-sistema` — lista paginada e filtrável;
- `GET /v1/admin/versoes-sistema/{id}` — consulta integral;
- `POST /v1/admin/versoes-sistema` — cria rascunho;
- `PUT /v1/admin/versoes-sistema/{id}` — edita rascunho;
- `POST /v1/admin/versoes-sistema/{id}/publicar` — publica;
- `POST /v1/admin/versoes-sistema/{id}/arquivar` — arquiva.

## Rollback técnico

A migration `j21t6v2q953` sucede `i20s5u1p842`. O downgrade remove primeiro o
trigger e sua função e depois a tabela. Antes do downgrade, exportar
`versoes_sistema`, pois o histórico cadastrado será perdido. A migration não
altera registros operacionais, organizações ou usuários.

## Pré-requisito de CI

O PostgreSQL do GitHub Actions foi alinhado à imagem de produção
`pgvector/pgvector:pg16`. Isso permite aplicar a migration anterior do RAG e
chegar à migration desta fase em um banco limpo.
