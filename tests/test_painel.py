from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.painel import (
    listar_notificacoes,
    marcar_notificacao_lida,
    marcar_notificacao_nao_lida,
    marcar_todas_notificacoes,
    painel_executivo,
)
from app.permissions import permissoes_do_perfil
from tests.conftest import FakeResult, FakeSession, usuario_teste

TODAS = set(permissoes_do_perfil("ceo"))


@pytest.mark.asyncio
async def test_painel_executivo_operador_ve_apenas_comercial() -> None:
    session = FakeSession([FakeResult(itens=[(5, 2, 19)])])
    usuario = usuario_teste(perfil="operador", permissoes={"dashboard.view"})
    painel = await painel_executivo(session, usuario)
    assert set(painel) == {"comercial"}
    assert painel["comercial"]["leads_novos"] == 2


@pytest.mark.asyncio
async def test_painel_executivo_ceo_ve_todos_os_blocos() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[(5, 2, 19)]),  # comercial
            FakeResult(itens=[(750, 700, 1450, 2)]),  # financeiro
            FakeResult(itens=[(7, 0, 0, 14)]),  # juridico
            FakeResult(itens=[(16, 2)]),  # risco
            FakeResult(scalar=None),  # aprendizado (sem modelo ativo)
        ]
    )
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    painel = await painel_executivo(session, usuario)
    assert set(painel) == {"comercial", "financeiro", "juridico", "risco", "aprendizado"}
    assert painel["juridico"]["aguardando_confirmacao"] == 14
    assert painel["financeiro"]["vencido"] == 1450.0
    assert painel["aprendizado"]["modelo_ativo"] is False


@pytest.mark.asyncio
async def test_notificacoes_unifica_e_ordena_por_data() -> None:
    juridica = SimpleNamespace(
        id=1,
        tipo="vencido",
        titulo="Prazo vencido",
        mensagem="Venceu ontem",
        criado_em=datetime(2026, 8, 10, tzinfo=UTC),
        lida_em=None,
    )
    alerta = SimpleNamespace(
        id=2,
        severidade="aviso",
        codigo="RETENCAO_PENDENTE",
        mensagem="10 leads excedem a retenção",
        criado_em=datetime(2026, 8, 11, tzinfo=UTC),
        resolvido_em=None,
    )
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[alerta])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resposta = await listar_notificacoes(session, usuario)
    assert resposta["total"] == 2
    # Mais recente primeiro: o alerta (11/08) antes do prazo jurídico (10/08).
    assert resposta["itens"][0]["fonte"] == "sistema"
    assert resposta["itens"][0]["url"] == "/admin/confiabilidade"
    assert resposta["itens"][1]["severidade"] == "aviso"
    assert resposta["itens"][1]["url"] == "/admin/operacao-juridica"


@pytest.mark.asyncio
async def test_notificacoes_operador_sem_permissao_nao_ve_nada() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador", permissoes={"dashboard.view"})
    resposta = await listar_notificacoes(session, usuario)
    assert resposta == {"total": 0, "total_itens": 0, "itens": []}


@pytest.mark.asyncio
async def test_marcar_juridica_como_lida_registra_autor_e_data() -> None:
    item = SimpleNamespace(status="nova", lida_em=None, lida_por=None)
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_notificacao_lida("juridico", 7, session, usuario)
    assert resultado == {"lida": True}
    assert item.status == "lida"
    assert item.lida_em is not None
    assert item.lida_por == usuario.ator


@pytest.mark.asyncio
async def test_marcar_alerta_sistema_como_resolvido() -> None:
    item = SimpleNamespace(resolvido_em=None)
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    await marcar_notificacao_lida("sistema", 3, session, usuario)
    assert item.resolvido_em is not None


@pytest.mark.asyncio
async def test_marcar_sem_permissao_retorna_404() -> None:
    session = FakeSession([])
    usuario = usuario_teste(perfil="operador", permissoes={"dashboard.view"})
    with pytest.raises(HTTPException) as erro:
        await marcar_notificacao_lida("sistema", 3, session, usuario)
    assert erro.value.status_code == 404


@pytest.mark.asyncio
async def test_marcar_juridica_como_nao_lida_reverte_estado() -> None:
    item = SimpleNamespace(status="lida", lida_em=datetime(2026, 8, 10, tzinfo=UTC), lida_por="admin@teste.local")
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_notificacao_nao_lida("juridico", 7, session, usuario)
    assert resultado == {"lida": False}
    assert item.status == "nova"
    assert item.lida_em is None
    assert item.lida_por is None


@pytest.mark.asyncio
async def test_marcar_alerta_sistema_como_nao_resolvido() -> None:
    item = SimpleNamespace(resolvido_em=datetime(2026, 8, 10, tzinfo=UTC))
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    await marcar_notificacao_nao_lida("sistema", 3, session, usuario)
    assert item.resolvido_em is None


@pytest.mark.asyncio
async def test_marcar_todas_como_lidas_afeta_juridico_e_sistema() -> None:
    juridica = SimpleNamespace(status="nova", lida_em=None, lida_por=None)
    alerta = SimpleNamespace(resolvido_em=None)
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[alerta])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_todas_notificacoes(session, usuario, lida=True)
    assert resultado == {"afetadas": 2, "lida": True}
    assert juridica.status == "lida"
    assert juridica.lida_em is not None
    assert alerta.resolvido_em is not None


@pytest.mark.asyncio
async def test_marcar_todas_como_nao_lidas_reverte_tudo() -> None:
    juridica = SimpleNamespace(status="lida", lida_em=datetime(2026, 8, 10, tzinfo=UTC), lida_por="x")
    alerta = SimpleNamespace(resolvido_em=datetime(2026, 8, 10, tzinfo=UTC))
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[alerta])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resultado = await marcar_todas_notificacoes(session, usuario, lida=False)
    assert resultado == {"afetadas": 2, "lida": False}
    assert juridica.status == "nova"
    assert juridica.lida_em is None
    assert alerta.resolvido_em is None


@pytest.mark.asyncio
async def test_notificacoes_incluem_id_e_fonte() -> None:
    juridica = SimpleNamespace(
        id=42,
        tipo="vencido",
        titulo="Prazo",
        mensagem="x",
        criado_em=datetime(2026, 8, 10, tzinfo=UTC),
        lida_em=None,
    )
    session = FakeSession([FakeResult(itens=[juridica]), FakeResult(itens=[])])
    usuario = usuario_teste(perfil="ceo", permissoes=TODAS)
    resposta = await listar_notificacoes(session, usuario)
    assert resposta["itens"][0]["id"] == 42
    assert resposta["itens"][0]["fonte"] == "juridico"
