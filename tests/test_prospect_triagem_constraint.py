from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.settings import get_settings

# --- Achado do usuário (08/09/2026): o job prospeccao.triar_marca_prospect
# falhou em produção com CheckViolationError ao classificar um prospect como
# POSSUI_OUTRA_MARCA_REGISTRADA (checagem ampla de titularidade, base local +
# busca ao vivo por CNPJ no INPI -- ver app/prospeccao_triagem.py) --
# ck_prospect_triagens_classificacao_valida e ck_prospects_triagem_marca_
# status_valido nunca foram atualizadas com o novo valor do enum. Mesmo
# padrão do bug de ck_leads_origem_valida (tests/test_leads_origem_
# constraint.py). Corrigido em migrations/versions/
# lf97a2h8t519_triagem_possui_outra_marca.py.
#
# Bate direto no Postgres real (engine próprio, não app.database.engine
# global -- ver comentário em test_leads_origem_constraint.py sobre por que),
# dentro de uma transação com rollback -- nunca persiste nada de verdade.


async def test_possui_outra_marca_registrada_e_aceita_em_prospect_triagens() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conexao:
            async with conexao.begin() as transacao:
                # prospects/prospect_triagens tem RLS com FORCE -- precisa do
                # contexto de tenant que o app normalmente aplica via
                # app.tenancy.aplicar_contexto_tenant (ver app/tenancy.py).
                await conexao.execute(text("SELECT set_config('app.superadmin', 'true', true)"))
                organizacao_id = (
                    await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
                ).scalar_one()
                prospect_id = (
                    await conexao.execute(
                        text(
                            "INSERT INTO prospects (organizacao_id, razao_social, cnpj, status) "
                            "VALUES (:organizacao_id, 'Teste Constraint Triagem', '00000000000100', 'novo') "
                            "RETURNING id"
                        ),
                        {"organizacao_id": organizacao_id},
                    )
                ).scalar_one()
                resultado = await conexao.execute(
                    text(
                        "INSERT INTO prospect_triagens (organizacao_id, prospect_id, marca_pesquisada, "
                        "classificacao, justificativa, total_resultados) VALUES (:organizacao_id, "
                        ":prospect_id, 'Marca Teste', 'possui_outra_marca_registrada', 'justificativa teste', "
                        "0) RETURNING id"
                    ),
                    {"organizacao_id": organizacao_id, "prospect_id": prospect_id},
                )
                assert resultado.scalar_one() is not None
                await transacao.rollback()
    finally:
        await engine.dispose()


async def test_possui_outra_marca_registrada_e_aceita_em_prospects_triagem_marca_status() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conexao:
            async with conexao.begin() as transacao:
                # prospects/prospect_triagens tem RLS com FORCE -- precisa do
                # contexto de tenant que o app normalmente aplica via
                # app.tenancy.aplicar_contexto_tenant (ver app/tenancy.py).
                await conexao.execute(text("SELECT set_config('app.superadmin', 'true', true)"))
                organizacao_id = (
                    await conexao.execute(text("SELECT id FROM organizacoes ORDER BY id LIMIT 1"))
                ).scalar_one()
                resultado = await conexao.execute(
                    text(
                        "INSERT INTO prospects (organizacao_id, razao_social, cnpj, status, "
                        "triagem_marca_status) VALUES (:organizacao_id, 'Teste Constraint Triagem', "
                        "'00000000000100', 'novo', 'possui_outra_marca_registrada') RETURNING id"
                    ),
                    {"organizacao_id": organizacao_id},
                )
                assert resultado.scalar_one() is not None
                await transacao.rollback()
    finally:
        await engine.dispose()
