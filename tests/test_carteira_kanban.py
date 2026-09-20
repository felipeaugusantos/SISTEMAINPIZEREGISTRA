from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.carteira import ETAPAS_KANBAN, listar_kanban
from app.auth import UsuarioAutenticado
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

# --- Achado da análise da tela "Processos monitorados" pedida pelo usuário
# (20/09/2026): listar_kanban fazia 1 query por etapa em loop (N+1 real) --
# reescrito para uma única query com row_number() OVER (PARTITION BY etapa).
# FakeSession não pega erro de sintaxe SQL (não roda o SQLAlchemy de
# verdade) -- por isso este teste bate direto no Postgres real, mesmo
# padrão de test_carteira_kanban_inpi.py.


def _usuario_teste() -> UsuarioAutenticado:
    return UsuarioAutenticado(
        id=1,
        nome="Teste",
        usuario="teste",
        email="teste@example.test",
        perfil="administrador",
        permissoes=frozenset({"portfolio.view"}),
        alterar_senha=False,
        sessao_id=1,
        csrf_hash="x",
        organizacao_id=1,
        superadmin=True,
    )


async def test_listar_kanban_sem_busca_nao_quebra_e_respeita_limite_por_coluna() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with AsyncSession(engine) as session:
            await aplicar_contexto_tenant(session, 1, superadmin=True)
            resultado = await listar_kanban(session, _usuario_teste())
            assert "total" in resultado
            assert "colunas" in resultado
            assert [coluna["chave"] for coluna in resultado["colunas"]] == [chave for chave, _ in ETAPAS_KANBAN]
            for coluna in resultado["colunas"]:
                assert len(coluna["itens"]) <= 20
    finally:
        await engine.dispose()


async def test_listar_kanban_com_busca_nao_quebra() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with AsyncSession(engine) as session:
            await aplicar_contexto_tenant(session, 1, superadmin=True)
            resultado = await listar_kanban(session, _usuario_teste(), busca="teste")
            assert "total" in resultado
            assert "colunas" in resultado
    finally:
        await engine.dispose()
