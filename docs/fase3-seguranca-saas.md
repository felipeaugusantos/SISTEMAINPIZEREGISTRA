# Fase 3 - Seguranca SaaS e isolamento

## Controles entregues

- autenticacao restrita por sessao, dominio, integracao ou fluxo de recuperacao;
- isolamento de organizacao por Row Level Security (RLS) e contexto de tenant;
- permissoes por modulo, perfil e plano contratado;
- sessoes com expiracao, inatividade e revogacao (`revogada_em`);
- MFA com TOTP, codigos de recuperacao e limitacao de tentativas;
- segredos protegidos com Fernet e prefixo de versao para rotacao;
- credenciais de integracao com `revoked_at`, expiracao e hash do token;
- auditoria com organizacao, ator, request ID, recurso, IP hash, estado anterior e posterior;
- politicas de leitura e escrita que impedem IDs de outro tenant.

## Evidencias do gate

Validacao executada em 2026-08-17:

- `tests/test_saas_rls_postgres.py`: PostgreSQL real com tenants A/B, matriz de tabelas,
  leitura, escrita, exclusao e acessos cruzados por ID na API;
- `tests/test_security_ext.py`: TOTP, janela de relogio e rotacao de chave mestra;
- `tests/test_permissions.py`: bloqueio por permissao, perfil e modulo;
- `tests/test_bearer_auth.py` e `tests/test_social_auth.py`: autenticacao e fluxos externos;
- `tests/test_password_change.py`: troca obrigatoria e invalidacao de credenciais.

Resultado: **gate aprovado**. O teste de isolamento retornou `404` para os seis recursos
consultados com IDs do tenant B quando autenticado como tenant A, e o banco rejeitou
insercao cruzada com RLS.

## Migracao e operacao

A migracao `zy21t6x2r408_seguranca_saas_restrita.py` registra os campos de revogacao,
versionamento e auditoria, substitui o bootstrap global por politicas de autenticacao
restrita e ativa RLS forcado no fluxo OAuth. Em producao, aplicar a migracao dentro da
janela de manutencao e validar login, MFA, logout e acesso por tenant antes do go-live.
