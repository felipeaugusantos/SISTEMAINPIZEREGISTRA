import os
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import UsuarioAutenticado, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.tenancy import aplicar_contexto_tenant

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


async def _conectar(dsn: str) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponivel para teste RLS: {type(exc).__name__}")


async def _criar_role_aplicacao(admin: asyncpg.Connection) -> tuple[str, str]:
    role = f"rls_test_{uuid4().hex[:12]}"
    senha = uuid4().hex + uuid4().hex
    await admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{senha}' INHERIT")
    await admin.execute(f'GRANT inpi_app TO "{role}"')
    origem = urlsplit(ADMIN_DSN)
    host = origem.hostname or "localhost"
    porta = origem.port or 5432
    banco = origem.path.lstrip("/") or "inpi"
    dsn = f"postgresql://{quote(role)}:{quote(senha)}@{host}:{porta}/{banco}"
    return role, dsn


async def _criar_cenario(admin: asyncpg.Connection) -> dict:
    sufixo = uuid4().hex[:12]
    plano_id = await admin.fetchval("SELECT id FROM planos_saas ORDER BY id LIMIT 1")
    processo_id = await admin.fetchval("SELECT id FROM processos ORDER BY id LIMIT 1")
    if plano_id is None or processo_id is None:
        pytest.skip("Base de integracao precisa de um plano SaaS e um processo INPI")

    ids: dict[str, int | str | list[int]] = {"orgs": []}
    for tenant in ("a", "b"):
        org_id = await admin.fetchval(
            "INSERT INTO organizacoes (nome, slug, plano_id) VALUES ($1, $2, $3) RETURNING id",
            f"Tenant RLS {tenant.upper()} {sufixo}",
            f"rls-{tenant}-{sufixo}",
            plano_id,
        )
        ids["orgs"].append(org_id)
        usuario_id = await admin.fetchval(
            """
            INSERT INTO usuarios_operacoes
                (organizacao_id, nome, usuario, email, senha_hash, alterar_senha)
            VALUES ($1, $2, $3, $4, 'hash-teste', false) RETURNING id
            """,
            org_id,
            f"Usuario {tenant.upper()}",
            f"rls.{tenant}.{sufixo}",
            f"rls.{tenant}.{sufixo}@example.test",
        )
        sessao_hash = uuid4().hex + uuid4().hex
        await admin.execute(
            """
            INSERT INTO sessoes_operacoes
                (usuario_id, token_hash, csrf_hash, expira_em)
            VALUES ($1, $2, $3, now() + interval '1 hour')
            """,
            usuario_id,
            sessao_hash,
            uuid4().hex + uuid4().hex,
        )
        integracao_hash = uuid4().hex + uuid4().hex
        await admin.execute(
            """
            INSERT INTO credenciais_integracao
                (organizacao_id, nome, token_prefixo, token_hash, criado_por)
            VALUES ($1, 'Teste RLS', $2, $3, 'teste')
            """,
            org_id,
            integracao_hash[:16],
            integracao_hash,
        )
        integracao_revogada_hash = uuid4().hex + uuid4().hex
        await admin.execute(
            """
            INSERT INTO credenciais_integracao
                (organizacao_id, nome, token_prefixo, token_hash, criado_por, ativo, revoked_at)
            VALUES ($1, 'Teste RLS revogada', $2, $3, 'teste', false, now())
            """,
            org_id,
            integracao_revogada_hash[:16],
            integracao_revogada_hash,
        )
        empresa_id = await admin.fetchval(
            """
            INSERT INTO empresas_crm (organizacao_id, nome, nome_normalizado)
            VALUES ($1, $2, $3) RETURNING id
            """,
            org_id,
            f"Empresa {tenant.upper()} {sufixo}",
            f"empresa {tenant} {sufixo}",
        )
        lead_id = await admin.fetchval(
            """
            INSERT INTO leads
                (organizacao_id, empresa_id, nome, email, telefone, marca, status,
                 aceite_privacidade)
            VALUES ($1, $2, $3, $4, '11999999999', $5, 'novo', true) RETURNING id
            """,
            org_id,
            empresa_id,
            f"Lead {tenant.upper()}",
            f"lead.{tenant}.{sufixo}@example.test",
            f"Marca {tenant.upper()} {sufixo}",
        )
        contato_id = await admin.fetchval(
            "INSERT INTO contatos (organizacao_id, empresa_id, nome) VALUES ($1, $2, $3) RETURNING id",
            org_id,
            empresa_id,
            f"Contato {tenant.upper()}",
        )
        pesquisa_id = str(uuid4())
        await admin.execute(
            """
            INSERT INTO pesquisas_marca (id, organizacao_id, lead_id, marca, tipo_pesquisa)
            VALUES ($1, $2, $3, $4, 'nominativa')
            """,
            pesquisa_id,
            org_id,
            lead_id,
            f"Marca {tenant.upper()} {sufixo}",
        )
        relatorio_id = await admin.fetchval(
            """
            INSERT INTO versoes_relatorio_marca
                (pesquisa_id, numero_versao, schema_versao, conteudo_hash, payload)
            VALUES ($1, 1, '1', $2, '{}'::jsonb) RETURNING id
            """,
            pesquisa_id,
            uuid4().hex,
        )
        documento_id = await admin.fetchval(
            """
            INSERT INTO documentos_lead (organizacao_id, lead_id, tipo)
            VALUES ($1, $2, 'procuracao') RETURNING id
            """,
            org_id,
            lead_id,
        )
        monitorado_id = await admin.fetchval(
            """
            INSERT INTO processos_monitorados (organizacao_id, processo_id, vinculado_por)
            VALUES ($1, $2, $3) RETURNING id
            """,
            org_id,
            processo_id,
            f"rls.{tenant}@example.test",
        )
        lembrete_id = await admin.fetchval(
            """
            INSERT INTO lembretes_crm
                (organizacao_id, lead_id, tipo, titulo, lembrar_em, criado_por)
            VALUES ($1, $2, 'retorno', $3, $4, $5) RETURNING id
            """,
            org_id,
            lead_id,
            f"Lembrete {tenant.upper()}",
            datetime.now(UTC),
            f"rls.{tenant}@example.test",
        )
        financeiro_id = await admin.fetchval(
            """
            INSERT INTO lancamentos_financeiros
                (organizacao_id, empresa_id, tipo, descricao, competencia, valor_total, criado_por)
            VALUES ($1, $2, 'receber', $3, CURRENT_DATE, 100, $4) RETURNING id
            """,
            org_id,
            empresa_id,
            f"Lancamento {tenant.upper()}",
            f"rls.{tenant}@example.test",
        )
        regra_id = await admin.fetchval(
            "INSERT INTO regras_automacao (organizacao_id, chave) VALUES ($1, $2) RETURNING id",
            org_id,
            f"rls_{tenant}_{sufixo}",
        )
        ids[tenant] = {
            "usuarios_operacoes": usuario_id,
            "empresas_crm": empresa_id,
            "leads": lead_id,
            "contatos": contato_id,
            "pesquisas_marca": pesquisa_id,
            "versoes_relatorio_marca": relatorio_id,
            "documentos_lead": documento_id,
            "processos_monitorados": monitorado_id,
            "lembretes_crm": lembrete_id,
            "lancamentos_financeiros": financeiro_id,
            "regras_automacao": regra_id,
        }
        ids[f"auth_{tenant}"] = {
            "identificador": f"rls.{tenant}.{sufixo}",
            "sessao_hash": sessao_hash,
            "integracao_hash": integracao_hash,
            "integracao_revogada_hash": integracao_revogada_hash,
        }
    return ids


async def _limpar_cenario(admin: asyncpg.Connection, ids: dict) -> None:
    orgs = ids.get("orgs", [])
    if not orgs:
        return
    for tabela in (
        "versoes_relatorio_marca",
        "documentos_lead",
        "lembretes_crm",
        "contatos",
        "processos_monitorados",
        "lancamentos_financeiros",
        "regras_automacao",
        "pesquisas_marca",
        "leads",
        "empresas_crm",
        "credenciais_integracao",
        "sessoes_operacoes",
        "usuarios_operacoes",
    ):
        coluna = "pesquisa_id" if tabela == "versoes_relatorio_marca" else "organizacao_id"
        if tabela == "versoes_relatorio_marca":
            pesquisas = [ids[tenant]["pesquisas_marca"] for tenant in ("a", "b")]
            await admin.execute(
                f"DELETE FROM {tabela} WHERE {coluna} = ANY($1::varchar[])", pesquisas
            )
        elif tabela == "sessoes_operacoes":
            usuarios = [ids[tenant]["usuarios_operacoes"] for tenant in ("a", "b")]
            await admin.execute(
                "DELETE FROM sessoes_operacoes WHERE usuario_id = ANY($1::bigint[])", usuarios
            )
        else:
            await admin.execute(f"DELETE FROM {tabela} WHERE {coluna} = ANY($1::bigint[])", orgs)
    await admin.execute("DELETE FROM organizacoes WHERE id = ANY($1::bigint[])", orgs)


@pytest.mark.asyncio
async def test_rls_real_isola_toda_matriz_e_ids_cruzados_na_api() -> None:
    admin = await _conectar(ADMIN_DSN)
    ids: dict = {}
    role: str | None = None
    engine_teste = None
    try:
        ids = await _criar_cenario(admin)
        role, app_dsn = await _criar_role_aplicacao(admin)
        app_conn = await _conectar(app_dsn)
        try:
            async with app_conn.transaction():
                assert await app_conn.fetchval("SELECT count(*) FROM usuarios_operacoes") == 0
                await app_conn.execute(
                    "SELECT set_config('app.auth_scope', 'login', true), "
                    "set_config('app.auth_value', $1, true), "
                    "set_config('app.auth_aux', '', true)",
                    ids["auth_a"]["identificador"],
                )
                assert await app_conn.fetchval("SELECT count(*) FROM usuarios_operacoes") == 1
                await app_conn.execute(
                    "SELECT set_config('app.auth_scope', 'sessao', true), "
                    "set_config('app.auth_value', $1, true)",
                    ids["auth_a"]["sessao_hash"],
                )
                assert await app_conn.fetchval("SELECT count(*) FROM sessoes_operacoes") == 1
                assert await app_conn.fetchval("SELECT count(*) FROM usuarios_operacoes") == 1
                await app_conn.execute(
                    "SELECT set_config('app.auth_scope', 'integracao', true), "
                    "set_config('app.auth_value', $1, true)",
                    ids["auth_a"]["integracao_hash"],
                )
                assert await app_conn.fetchval("SELECT count(*) FROM credenciais_integracao") == 1
                await app_conn.execute(
                    "SELECT set_config('app.auth_value', $1, true)",
                    ids["auth_a"]["integracao_revogada_hash"],
                )
                assert await app_conn.fetchval("SELECT count(*) FROM credenciais_integracao") == 0
                await app_conn.execute(
                    "SELECT set_config('app.organizacao_id', $1, true), "
                    "set_config('app.superadmin', 'false', true)",
                    str(ids["orgs"][0]),
                )
                for tabela, id_a in ids["a"].items():
                    id_b = ids["b"][tabela]
                    assert (
                        await app_conn.fetchval(
                            f"SELECT count(*) FROM {tabela} WHERE id = $1", id_a
                        )
                        == 1
                    )
                    assert (
                        await app_conn.fetchval(
                            f"SELECT count(*) FROM {tabela} WHERE id = $1", id_b
                        )
                        == 0
                    )
                assert (
                    await app_conn.execute(
                        "UPDATE empresas_crm SET observacoes = 'bloqueado' WHERE id = $1",
                        ids["b"]["empresas_crm"],
                    )
                    == "UPDATE 0"
                )
                assert (
                    await app_conn.execute(
                        "DELETE FROM contatos WHERE id = $1", ids["b"]["contatos"]
                    )
                    == "DELETE 0"
                )
                insercao_cruzada = app_conn.transaction()
                await insercao_cruzada.start()
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app_conn.execute(
                        "INSERT INTO empresas_crm "
                        "(organizacao_id, nome, nome_normalizado) VALUES ($1, $2, $3)",
                        ids["orgs"][1],
                        "Empresa cruzada bloqueada",
                        f"empresa-cruzada-{uuid4().hex}",
                    )
                await insercao_cruzada.rollback()
        finally:
            await app_conn.close()

        usuario = UsuarioAutenticado(
            id=ids["a"]["usuarios_operacoes"],
            nome="Tenant A",
            usuario="tenant-a",
            email="tenant-a@example.test",
            perfil="administrador",
            permissoes=frozenset(),
            alterar_senha=False,
            sessao_id=1,
            csrf_hash="",
            organizacao_id=ids["orgs"][0],
            organizacao_slug="tenant-a",
        )

        async def usuario_override() -> UsuarioAutenticado:
            return usuario

        engine_teste = create_async_engine(
            app_dsn.replace("postgresql://", "postgresql+asyncpg://")
        )
        fabrica_teste = async_sessionmaker(engine_teste, expire_on_commit=False)

        async def sessao_override():
            async with fabrica_teste() as session:
                await aplicar_contexto_tenant(session, usuario.organizacao_id)
                yield session

        app.dependency_overrides[obter_usuario_atual] = usuario_override
        app.dependency_overrides[get_session] = sessao_override
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                respostas = (
                    await client.get(
                        f"/v1/admin/crm/empresas/{ids['b']['empresas_crm']}"
                    ),
                    await client.get(f"/v1/admin/leads/{ids['b']['leads']}"),
                    await client.get(
                        f"/v1/admin/analises/{ids['b']['pesquisas_marca']}"
                    ),
                    await client.get(
                        f"/v1/admin/leads/{ids['b']['leads']}/documentos"
                    ),
                    await client.get(
                        f"/v1/admin/carteira/{ids['b']['processos_monitorados']}/historico-kanban"
                    ),
                    await client.get(
                        "/v1/admin/financeiro/lancamentos/"
                        f"{ids['b']['lancamentos_financeiros']}/historico"
                    ),
                )
            assert [resposta.status_code for resposta in respostas] == [404] * 6
        finally:
            app.dependency_overrides.clear()
    finally:
        if engine_teste is not None:
            await engine_teste.dispose()
        await _limpar_cenario(admin, ids)
        if role:
            await admin.execute(f'REVOKE inpi_app FROM "{role}"')
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
