import asyncio

from starlette.requests import Request

from app.api.carteira import (
    AtualizacaoMonitoramento,
    CadastroManual,
    _filtro_procurador,
    _grupo_situacao_valor,
    _normalizar_busca,
    _procurador_exibicao,
    _titulo_exibicao,
    _validar_grupo_situacao_inpi,
    atualizar_monitoramento,
    buscar_por_procurador,
    cadastrar_manual,
)
from app.models import (
    EventoAuditoria,
    HistoricoEtapaCarteira,
    Processo,
    ProcessoMonitorado,
    TipoProcesso,
)
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
        valor for valor in expressao.compile().params.values() if isinstance(valor, str) and valor.startswith("%")
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
            FakeResult(itens=[]),  # verificar_conflito_interesse: titulares de outros clientes
            FakeResult(itens=[]),  # verificar_conflito_interesse: empresas ja cliente
            FakeResult(itens=[101]),
            FakeResult(itens=[]),
        ]
    )
    usuario = usuario_teste()

    resultado = asyncio.run(
        cadastrar_manual(
            CadastroManual(numero="935977333", titular="Titular Teste Ltda"),
            _request(),
            session,
            usuario,
        )
    )

    assert resultado["vinculados"] == 1
    assert resultado["alertas_conflito_interesse"] == []
    monitorados = [item for item in session.adicionados if isinstance(item, ProcessoMonitorado)]
    assert len(monitorados) == 1
    assert monitorados[0].processo_id == processo.id
    assert monitorados[0].organizacao_id == usuario.organizacao_id
    assert any(isinstance(item, EventoAuditoria) for item in session.adicionados)
    assert session.commits == 1


def test_cadastro_manual_liga_processo_ao_lead_quando_numero_bate() -> None:
    # Achado da auditoria do CRM: Lead.processo_numero era só texto solto, sem
    # integridade com a carteira. Agora o vínculo é preenchido sozinho quando
    # o número do processo bate com o que o lead informou.
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
            FakeResult(itens=[]),  # verificar_conflito_interesse: titulares de outros clientes
            FakeResult(itens=[]),  # verificar_conflito_interesse: empresas ja cliente
            FakeResult(itens=[101]),
            FakeResult(itens=[]),
            FakeResult(itens=[(101, 7)]),
        ]
    )
    usuario = usuario_teste()

    resultado = asyncio.run(
        cadastrar_manual(
            CadastroManual(numero="935977333", titular="Titular Teste Ltda"),
            _request(),
            session,
            usuario,
        )
    )

    assert resultado["vinculados"] == 1
    monitorados = [item for item in session.adicionados if isinstance(item, ProcessoMonitorado)]
    assert monitorados[0].lead_id == 7


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
            FakeResult(itens=[]),  # verificar_conflito_interesse: titulares de outros clientes
            FakeResult(itens=[]),  # verificar_conflito_interesse: empresas ja cliente
            FakeResult(itens=[101]),
            FakeResult(itens=[101]),
        ]
    )

    resultado = asyncio.run(
        cadastrar_manual(
            CadastroManual(numero="935977333", titular="Titular Teste Ltda"),
            _request(),
            session,
            usuario_teste(),
        )
    )

    assert resultado["vinculados"] == 0
    assert resultado["ja_vinculados"] == 1
    assert not any(isinstance(item, ProcessoMonitorado) for item in session.adicionados)


def test_tela_expoe_cadastro_e_vinculo_por_procurador() -> None:
    page = "app/web/admin-carteira.html"
    script = "app/web/static/admin-carteira.js"
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert "Pesquisar por procurador" in html
    assert "Cadastrar processo" in html
    assert "admin-carteira.css?v=10" in html
    assert "admin-carteira.js?v=" in html
    assert "Incluir variações do nome" in html
    assert "titular" in javascript
    assert "attorney-variants" in javascript
    assert 'class="portfolio-inpi"' in javascript
    assert "portfolio-item-label" in javascript
    assert "/v1/admin/carteira/vincular-procurador" in javascript
    assert "/v1/admin/carteira/manual" in javascript
    assert 'id="portfolio-view-kanban"' in html
    assert 'id="portfolio-view-inpi"' in html
    assert 'name="situacao_inpi"' in html
    assert 'id="portfolio-pagination"' in html
    assert 'params.set("limite", state.pageSize)' in javascript
    assert "pageSize: 20" in javascript
    assert "/v1/admin/carteira/kanban" in javascript
    assert "/v1/admin/carteira/kanban-inpi" in javascript
    assert "function readableError" in javascript
    assert "params.delete(key)" in javascript
    assert "Classificação automática pela RPI" in javascript
    assert "Processos deferidos" in javascript
    assert "Processos pausados" in javascript
    assert "Processos arquivados/extintos" in javascript
    assert "Processos indeferidos" in javascript
    assert "Registros concedidos" in javascript
    assert "Em tramitação" in javascript
    assert "data-metric-filter" in javascript
    assert "function applyMetricFilter" in javascript


def test_agrupa_situacoes_oficiais_sem_misturar_fluxo_interno() -> None:
    assert _validar_grupo_situacao_inpi("") is None
    assert _validar_grupo_situacao_inpi("deferido") == "deferido"
    assert _grupo_situacao_valor("deferida") == "deferido"
    assert _grupo_situacao_valor("registrada") == "registrado"
    assert _grupo_situacao_valor("indeferida") == "indeferido"
    assert _grupo_situacao_valor("inexistente") == "encerrado"
    assert _grupo_situacao_valor("peticao_decidida") == "revisar"


def test_titulo_exibicao_explica_ausencia_do_titulo_oficial() -> None:
    figurativa = Processo(
        titulo=None,
        apresentacao="Figurativa",
        situacao_normalizada="arquivada",
    )
    inexistente = Processo(
        titulo=None,
        apresentacao=None,
        situacao_normalizada="inexistente",
    )
    nominativa = Processo(titulo="  ACME  ")

    assert _titulo_exibicao(figurativa) == "Marca figurativa (sem elemento nominativo)"
    assert _titulo_exibicao(inexistente) == "Pedido inexistente — título não publicado"
    assert _titulo_exibicao(nominativa) == "ACME"


def test_etapa_kanban_e_validada_e_historico_tem_tenant() -> None:
    dados = AtualizacaoMonitoramento(etapa_kanban="aguardando_inpi")
    assert dados.etapa_kanban == "aguardando_inpi"
    assert ProcessoMonitorado.etapa_kanban.property.columns[0].default.arg == "triagem"


def test_procurador_exibicao_prioriza_correcao_da_organizacao() -> None:
    processo = Processo(procurador="Publicado na RPI")
    sem_correcao = ProcessoMonitorado(procurador_manual=None)
    com_correcao = ProcessoMonitorado(procurador_manual="Corrigido por esta organização")

    assert _procurador_exibicao(processo, sem_correcao) == "Publicado na RPI"
    assert _procurador_exibicao(processo, com_correcao) == "Corrigido por esta organização"


def test_atualizar_monitoramento_nao_grava_no_processo_compartilhado() -> None:
    # Achado da analise do modulo (13/09/2026): processo.procurador e
    # compartilhado entre organizacoes (tabela sem organizacao_id); a
    # correcao feita pela carteira precisa ficar so no vinculo desta
    # organizacao (procurador_manual), nunca no Processo.
    monitorado = ProcessoMonitorado(
        id=1,
        organizacao_id=1,
        processo_id=10,
        status="ativo",
        vinculado_por="teste",
        procurador_manual=None,
    )
    session = FakeSession([FakeResult(scalar=monitorado)])

    resultado = asyncio.run(
        atualizar_monitoramento(
            1,
            AtualizacaoMonitoramento(procurador="Dr. Corrigido"),
            _request(),
            session,
            usuario_teste(),
        )
    )

    assert resultado == {"status": "ok", "id": 1}
    assert monitorado.procurador_manual == "Dr. Corrigido"
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.detalhes["depois"]["procurador_manual"] == "Dr. Corrigido"
    assert session.commits == 1
    assert HistoricoEtapaCarteira.__table__.c.organizacao_id.foreign_keys
