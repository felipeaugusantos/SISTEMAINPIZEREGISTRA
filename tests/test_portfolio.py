import asyncio

from starlette.requests import Request

from app.api.carteira import CadastroManual, _normalizar_busca, cadastrar_manual
from app.models import EventoAuditoria, Processo, ProcessoMonitorado, TipoProcesso
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/carteira/manual",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_normaliza_nome_do_procurador() -> None:
    assert _normalizar_busca("  DÁCOSTA   Marcas  ") == "dacosta marcas"


def test_cadastro_manual_vincula_processo_sem_duplicar_dados_rpi() -> None:
    processo = Processo(
        id=101,
        numero="935977333",
        numero_normalizado="935977333",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2897",
    )
    session = FakeSession(
        [
            FakeResult(scalar=processo),
            FakeResult(itens=[101]),
            FakeResult(itens=[]),
        ]
    )
    usuario = usuario_teste()

    resultado = asyncio.run(
        cadastrar_manual(
            CadastroManual(numero="935977333"),
            _request(),
            session,
            usuario,
        )
    )

    assert resultado["vinculados"] == 1
    monitorados = [item for item in session.adicionados if isinstance(item, ProcessoMonitorado)]
    assert len(monitorados) == 1
    assert monitorados[0].processo_id == processo.id
    assert monitorados[0].organizacao_id == usuario.organizacao_id
    assert any(isinstance(item, EventoAuditoria) for item in session.adicionados)
    assert session.commits == 1


def test_cadastro_manual_informa_quando_processo_ja_esta_vinculado() -> None:
    processo = Processo(
        id=101,
        numero="935977333",
        numero_normalizado="935977333",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2897",
    )
    session = FakeSession(
        [
            FakeResult(scalar=processo),
            FakeResult(itens=[101]),
            FakeResult(itens=[101]),
        ]
    )

    resultado = asyncio.run(
        cadastrar_manual(
            CadastroManual(numero="935977333"),
            _request(),
            session,
            usuario_teste(),
        )
    )

    assert resultado["vinculados"] == 0
    assert resultado["ja_vinculados"] == 1
    assert not any(isinstance(item, ProcessoMonitorado) for item in session.adicionados)


def test_tela_expoe_cadastro_e_vinculo_por_procurador() -> None:
    page = ("app/web/admin-carteira.html")
    script = ("app/web/static/admin-carteira.js")
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert "Pesquisar por procurador" in html
    assert "Cadastrar processo" in html
    assert "/v1/admin/carteira/vincular-procurador" in javascript
    assert "/v1/admin/carteira/manual" in javascript
