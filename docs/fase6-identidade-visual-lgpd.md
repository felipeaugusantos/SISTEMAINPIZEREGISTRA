# Fase 6 — identidade visual e LGPD

## Linha de base comprovada

- A tela aceitava somente uma URL textual para o logotipo; não havia upload,
  inspeção do formato, limite dimensional, remoção de metadados ou prévia.
- A versão pública da política podia ser alterada sem justificativa específica,
  confirmação de publicação ou evento de auditoria próprio.
- O endpoint público de branding já possuía allowlist, mas apenas pesquisa,
  processo e relatório carregavam a identidade dinâmica. Sobre, contato e
  privacidade permaneciam com a marca fixa.
- A página de privacidade não mostrava qual versão estava publicada.

## Implementação

- Upload administrativo limitado a 1 MB e imagens PNG, JPEG ou WebP, entre
  32 × 32 e 2000 × 2000 pixels.
- Toda imagem é decodificada e regravada como PNG. Isso elimina SVG e
  conteúdo ativo, corrige orientação EXIF e remove metadados do arquivo.
- O objeto fica no backend de armazenamento configurado. O navegador recebe
  somente `/v1/tenant/logo`, resolvido pela organização do host, sem caminho
  local ou endereço do bucket.
- Substituição e remoção preservam todas as demais chaves de `branding` e são
  auditadas. Arquivos substituídos são removidos em melhor esforço depois do
  commit; uma falha de gravação não publica a configuração.
- Alterar a versão da política exige justificativa e confirmação explícita de
  que o conteúdo público foi revisado. A mudança registra versões anterior e
  nova, responsável e justificativa em `eventos_auditoria`.
- A versão publicada passa a aparecer na página de privacidade. Identidade
  dinâmica também é aplicada nas páginas de sobre e contato.

## Compatibilidade e limites

- URLs HTTPS e recursos legados em `/static/` continuam aceitos para não
  invalidar configurações existentes. Imagens externas usam política de
  referência `no-referrer`.
- Nenhum prazo de retenção é alterado por esta fase.
- O conteúdo jurídico da política continua exigindo revisão humana; a
  confirmação registra governança, mas não substitui validação jurídica.
