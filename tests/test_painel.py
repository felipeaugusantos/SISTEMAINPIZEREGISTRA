from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.painel import listar_notificacoes, painel_executivo
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
        tipo="vencido",
        titulo="Prazo vencido",
        mensagem="Venceu ontem",
        criado_em=datetime(2026, 8, 10, tzinfo=UTC),
    )
    alerta = SimpleNamespace(
        severidade="aviso",
        codigo="RETENCAO_PENDENTE",
        mensagem="10 leads excedem a retenção",
        criado_em=datetime(2026, 8, 11, tzinfo=UTC),
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
    assert resposta == {"total": 0, "itens": []}
