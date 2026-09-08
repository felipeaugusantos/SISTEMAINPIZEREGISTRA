from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.carteira import listar_kanban_inpi
from app.auth import UsuarioAutenticado
from app.settings import get_settings
from app.tenancy import aplicar_contexto_tenant

# --- Achado do usuário (08/09/2026): a aba "Situação INPI" da tela de
# Processos Monitorados (view Kanban por situação oficial no INPI) estava
# completamente quebrada -- 500 Internal Server Error em toda carga, com ou
# sem busca. A query de contagem por grupo de situação (listar_kanban_inpi,
# app/api/carteira.py) selecionava só uma expressão derivada de Processo
# (não uma coluna de ProcessoMonitorado) sem `select_from(ProcessoMonitorado)`
# explícito -- o SQLAlchemy não conseguia inferir a base do FROM para o
# `.join(Processo, ...)` seguinte, levantando InvalidRequestError ("Don't
# know how to join to Processo... use .select_from()").
#
# FakeSession não pega esse tipo de bug (não roda o SQLAlchemy de verdade) --
# por isso este teste bate direto no Postgres real, engine próprio (mesmo
# motivo do padrão já usado em test_leads_origem_constraint.py).


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


async def test_listar_kanban_inpi_sem_busca_nao_quebra() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with AsyncSession(engine) as session:
            await aplicar_contexto_tenant(session, 1, superadmin=True)
            resultado = await listar_kanban_inpi(session, _usuario_teste())
            assert "total" in resultado
            assert "colunas" in resultado
    finally:
        await engine.dispose()


async def test_listar_kanban_inpi_com_busca_nao_quebra() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with AsyncSession(engine) as session:
            await aplicar_contexto_tenant(session, 1, superadmin=True)
            resultado = await listar_kanban_inpi(session, _usuario_teste(), busca="teste")
            assert "total" in resultado
            assert "colunas" in resultado
    finally:
        await engine.dispose()
