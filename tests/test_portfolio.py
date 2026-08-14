import asyncio

from starlette.requests import Request

from app.api.carteira import (
    CadastroManual,
    _filtro_procurador,
    _normalizar_busca,
    buscar_por_procurador,
    cadastrar_manual,
)
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


def test_modo_variacoes_busca_todos_os_termos_do_nome() -> None:
    expressao = _filtro_procurador("José Vicente", "variacoes")
    valores = {
        valor
        for valor in expressao.compile().params.values()
        if isinstance(valor, str) and valor.startswith("%")
    }
    assert valores == {"%jose%", "%vicente%"}


def test_busca_informa_processos_titulares_variacoes_e_cobertura() -> None:
    processos = [
        Processo(
            id=1,
            numero="937557234",
            numero_normalizado="937557234",
            tipo=TipoProcesso.MARCA,
            fonte="RPI 2901",
            titulo="ECQ",
            procurador="José Vicente",
        ),
        Processo(
            id=2,
            numero="937558818",
            numero_normalizado="937558818",
            tipo=TipoProcesso.MARCA,
            fonte="RPI 2901",
            titulo="Liga Safe",
            procurador="José Daniel de Vicente Fossa",
        ),
    ]
    session = FakeSession(
        [
            FakeResult(itens=[(2, 2)]),
            FakeResult(itens=[(2818, 2901, 84)]),
            FakeResult(itens=[(processos[0], None), (processos[1], 9)]),
        ]
    )

    resultado = asyncio.run(
        buscar_por_procurador(
            session,
            usuario_teste(),
            "José Vicente",
            "variacoes",
            50,
            0,
        )
    )

    assert resultado["total_processos"] == 2
    assert resultado["total_titulares"] == 2
    assert resultado["modo"] == "variacoes"
    assert {item["nome"] for item in resultado["variacoes"]} == {
        "José Vicente",
        "José Daniel de Vicente Fossa",
    }
    assert resultado["cobertura"]["primeira_rpi"] == 2818
    assert resultado["cobertura"]["ultima_rpi"] == 2901


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
    assert "admin-carteira.css?v=5" in html
    assert "admin-carteira.js?v=8" in html
    assert "Incluir variações do nome" in html
    assert "titular" in javascript
    assert "attorney-variants" in javascript
    assert 'class="portfolio-inpi"' in javascript
    assert "portfolio-item-label" in javascript
    assert "/v1/admin/carteira/vincular-procurador" in javascript
    assert "/v1/admin/carteira/manual" in javascript
