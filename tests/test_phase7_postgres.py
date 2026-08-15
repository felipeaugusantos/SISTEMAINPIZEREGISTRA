import os
from uuid import uuid4

import asyncpg
import pytest

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


async def _conectar() -> asyncpg.Connection:
    try:
        return await asyncpg.connect(ADMIN_DSN)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponível: {type(exc).__name__}")


async def test_empresa_contato_oportunidade_rejeita_vinculo_entre_tenants() -> None:
    conexao = await _conectar()
    transacao = conexao.transaction()
    await transacao.start()
    try:
        plano_id = await conexao.fetchval("SELECT id FROM planos_saas ORDER BY id LIMIT 1")
        if plano_id is None:
            pytest.skip("O teste exige um plano SaaS cadastrado")
        sufixo = uuid4().hex[:10]
        orgs = []
        for indice in (1, 2):
            orgs.append(
                await conexao.fetchval(
                    "INSERT INTO organizacoes (nome, slug, plano_id) "
                    "VALUES ($1, $2, $3) RETURNING id",
                    f"Tenant Fase 7 {indice}",
                    f"fase7-{indice}-{sufixo}",
                    plano_id,
                )
            )
        empresas = []
        for indice, org_id in enumerate(orgs, 1):
            empresas.append(
                await conexao.fetchval(
                    "INSERT INTO empresas_crm (organizacao_id, nome, nome_normalizado) "
                    "VALUES ($1, $2, $3) RETURNING id",
                    org_id,
                    f"Empresa {indice}",
                    f"empresa-{indice}-{sufixo}",
                )
            )
        contato_b = await conexao.fetchval(
            "INSERT INTO contatos (organizacao_id, empresa_id, nome) "
            "VALUES ($1, $2, 'Contato B') RETURNING id",
            orgs[1],
            empresas[1],
        )
        savepoint = conexao.transaction()
        await savepoint.start()
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conexao.execute(
                "INSERT INTO leads "
                "(organizacao_id, empresa_id, contato_id, nome, email, telefone, marca, "
                "status, aceite_privacidade) "
                "VALUES ($1, $2, $3, 'Lead', $4, '11999999999', 'Marca', 'novo', true)",
                orgs[0],
                empresas[0],
                contato_b,
                f"lead-{sufixo}@example.test",
            )
        await savepoint.rollback()
    finally:
        await transacao.rollback()
        await conexao.close()


async def test_juridico_e_financeiro_rejeitam_vinculos_entre_tenants() -> None:
    conexao = await _conectar()
    transacao = conexao.transaction()
    await transacao.start()
    try:
        plano_id = await conexao.fetchval("SELECT id FROM planos_saas ORDER BY id LIMIT 1")
        processo_id = await conexao.fetchval("SELECT id FROM processos ORDER BY id LIMIT 1")
        if plano_id is None or processo_id is None:
            pytest.skip("O teste exige plano SaaS e processo INPI cadastrados")
        sufixo = uuid4().hex[:10]
        orgs = []
        for indice in (1, 2):
            orgs.append(
                await conexao.fetchval(
                    "INSERT INTO organizacoes (nome, slug, plano_id) "
                    "VALUES ($1, $2, $3) RETURNING id",
                    f"Tenant Integridade {indice}",
                    f"integridade-{indice}-{sufixo}",
                    plano_id,
                )
            )
        monitorado_a = await conexao.fetchval(
            "INSERT INTO processos_monitorados (organizacao_id, processo_id, vinculado_por) "
            "VALUES ($1, $2, 'teste') RETURNING id",
            orgs[0],
            processo_id,
        )
        lancamento_a = await conexao.fetchval(
            "INSERT INTO lancamentos_financeiros "
            "(organizacao_id, tipo, descricao, competencia, valor_total, criado_por) "
            "VALUES ($1, 'receber', 'Teste cross-tenant', CURRENT_DATE, 100, 'teste') "
            "RETURNING id",
            orgs[0],
        )

        juridico = conexao.transaction()
        await juridico.start()
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conexao.execute(
                "INSERT INTO prazos_juridicos "
                "(organizacao_id, processo_monitorado_id, titulo, data_base, dias_prazo, "
                "vencimento_em, criado_por) "
                "VALUES ($1, $2, 'Prazo inválido', CURRENT_DATE, 10, now(), 'teste')",
                orgs[1],
                monitorado_a,
            )
        await juridico.rollback()

        financeiro = conexao.transaction()
        await financeiro.start()
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conexao.execute(
                "INSERT INTO parcelas_financeiras "
                "(organizacao_id, lancamento_id, numero, vencimento, valor, valor_pago, status) "
                "VALUES ($1, $2, 1, CURRENT_DATE, 100, 0, 'aberta')",
                orgs[1],
                lancamento_a,
            )
        await financeiro.rollback()
    finally:
        await transacao.rollback()
        await conexao.close()


async def test_lock_financeiro_serializa_baixa_concorrente() -> None:
    primeira = await _conectar()
    segunda = await _conectar()
    parcela_id = await primeira.fetchval("SELECT id FROM parcelas_financeiras ORDER BY id LIMIT 1")
    if parcela_id is None:
        await primeira.close()
        await segunda.close()
        pytest.skip("O teste exige uma parcela financeira")
    transacao_a = primeira.transaction()
    transacao_b = segunda.transaction()
    await transacao_a.start()
    await transacao_b.start()
    consulta = (
        "SELECT p.id FROM parcelas_financeiras p "
        "JOIN lancamentos_financeiros l ON l.id = p.lancamento_id "
        "WHERE p.id = $1 FOR UPDATE OF l, p"
    )
    try:
        await primeira.fetchval(consulta, parcela_id)
        await segunda.execute("SET LOCAL lock_timeout = '250ms'")
        with pytest.raises(asyncpg.LockNotAvailableError):
            await segunda.fetchval(consulta, parcela_id)
    finally:
        await transacao_b.rollback()
        await transacao_a.rollback()
        await segunda.close()
        await primeira.close()
