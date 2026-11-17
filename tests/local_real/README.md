# Testes locais com banco real (pedido de exclusão de contato e descarte)

Não fazem parte do `pytest` do CI (precisam de Postgres e, os `.cjs`, de Chrome). Usam dados sintéticos e
uma autenticação substituída por override de teste. Não tocam produção.

## Dependências
- Python 3.12 com as dependências do projeto (`pip install -e .` ou o `requirements` do repositório) e `pgserver`
  (Postgres 16 com pgvector embutido): `pip install pgserver`.
- Node + Playwright do repositório (`npm ci`) e Chrome/Chromium (`PW_CHROME=/usr/bin/google-chrome`).

## Banco local
```bash
python - <<'PY'
import pgserver
db = pgserver.get_server("/tmp/pgdata", cleanup_mode=None)
print(db.get_uri())   # socket em /tmp/pgdata; usuário/banco conforme o pgserver
PY
export DATABASE_URL='postgresql+asyncpg://inpi:inpi@/inpi?host=/tmp/pgdata' APP_ENV=test \
  ADMIN_PASSWORD='Teste-Seguro-123!' SECURITY_MASTER_KEY=ci-only-security-key REDIS_URL=redis://localhost:6379/0
python tests/local_real/setup_db.py        # cria o schema (Base.metadata.create_all) e o papel inpi_app
```
Limite conhecido: `alembic upgrade head` não roda nesse Postgres (faltam as extensões `unaccent` e `pg_trgm`).
Por isso o schema vem de `create_all` e a migração nova é testada à parte, em `run_migracao_rls.py`.

## Rodar
```bash
python tests/local_real/run_pedido_contato_api.py     # API + banco real (exit 1 se alguma checagem falhar)
python tests/local_real/run_migracao_rls.py           # aplica a migração nova e testa RLS com o papel inpi_app
python tests/local_real/serve_local.py &              # app real em 127.0.0.1:8123 (cookie zr_test_user=op|adm)
sleep 8; node tests/local_real/e2e_pedido_contato.cjs # Chrome real; capturas em $SHOTS (padrão /tmp/shots)
```
Reinicie `serve_local.py` antes de cada rodada do e2e (ele recria os dados sintéticos ao subir).
