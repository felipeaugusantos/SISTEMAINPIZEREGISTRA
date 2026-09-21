import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.carteira import (
    AtribuicaoLote,
    AtualizacaoLote,
    AtualizacaoMonitoramento,
    CadastroManual,
    _filtro_procurador,
    _grupo_situacao_valor,
    _normalizar_busca,
    _procurador_exibicao,
    _titulo_exibicao,
    _validar_grupo_situacao_inpi,
    atribuir_lote,
    atualizar_monitoramento,
    atualizar_status_lote,
    atualizar_status_processo,
    buscar_por_procurador,
    cadastrar_manual,
    exportar_carteira,
    listar_carteira,
    obter_status_sincronizacao_rpi,
)
from app.models import (
    EmpresaCRM,
    EventoAuditoria,
    HistoricoEtapaCarteira,
    Processo,
    ProcessoMonitorado,
    RpiSyncEstado,
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
    assert "admin-carteira.css?v=14" in html
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
    assert "Atualizar situação de todos" in html
    assert "/v1/admin/carteira/atualizar-lote" in javascript
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


def test_tela_permite_vincular_processo_ja_monitorado_a_um_lead() -> None:
    # Achado do usuário (21/09/2026): um processo já vinculado à carteira
    # (ex.: cadastro manual antes do lead avançar de fase) não tinha como
    # ser ligado a um lead depois -- só o vínculo automático por
    # Lead.processo_numero, que também não tinha onde ser editado. O
    # backend já aceitava lead_id/remover_lead em PATCH /carteira/{id}; só
    # faltava a tela.
    page = "app/web/admin-carteira.html"
    script = "app/web/static/admin-carteira.js"
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert "admin-carteira.css?v=14" in html
    assert "data-lead-search" in javascript
    assert "data-lead-query" in javascript
    assert "data-vincular-lead-id" in javascript
    assert "data-unlink-lead" in javascript
    assert '/v1/admin/leads?busca=' in javascript
    assert '{ lead_id: Number(button.dataset.vincularLeadId) }' in javascript
    assert '{ remover_lead: true }' in javascript
    # Só quem gerencia a carteira vê o controle -- mesmo padrão de
    # state.canManage já usado pra editar status/procurador.
    assert "state.canManage" in javascript

    # Achados do Codex review (PR #97):
    # 1) nome + empresa repetem entre leads do mesmo contato com marcas
    #    diferentes -- cada resultado precisa mostrar a marca (e o id como
    #    desempate) pra não vincular ao lead errado.
    assert "lead.marca" in javascript
    assert "<small>#${lead.id}</small>" in javascript
    # 2) portfolio.manage e leads.view são permissões independentes -- uma
    #    conta com a primeira e sem a segunda via 403 silencioso na busca.
    assert "state.canBuscarLeads" in javascript
    assert "data.acoes?.buscar_leads" in javascript
    assert "acesso a Leads" in javascript
    # 3) o campo de busca permitia 150 caracteres, mas /v1/admin/leads limita
    #    busca a 100 -- um texto de 101-150 caracteres batia 422 em vez de
    #    resultado.
    assert 'maxlength="100"' in javascript
    assert 'maxlength="150"' not in javascript


def test_tela_expoe_atribuicao_em_lote_na_lista() -> None:
    # Achado do usuário (20/09/2026): atribuir empresa/responsável a
    # processos já monitorados só existia um por um -- checkbox de seleção
    # por card + barra de atribuição em lote na view "Lista".
    page = "app/web/admin-carteira.html"
    script = "app/web/static/admin-carteira.js"
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert 'id="portfolio-bulk-assign"' in html
    assert 'id="portfolio-select-page"' in html
    assert 'id="bulk-company"' in html
    assert 'id="bulk-owner"' in html
    assert 'id="bulk-assign-apply"' in html
    assert "data-select-id" in javascript
    assert "/v1/admin/carteira/atribuir-lote" in javascript
    assert "function updateBulkBar" in javascript


def test_tela_expoe_exportacao_da_carteira_em_csv() -> None:
    # Achado do usuário (20/09/2026): só existia relatório em PDF processo
    # por processo -- link de exportação reflete o filtro atual (busca,
    # status, situação no INPI), não uma exportação genérica descolada dele.
    page = "app/web/admin-carteira.html"
    script = "app/web/static/admin-carteira.js"
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert 'id="export-csv"' in html
    assert '/v1/admin/carteira/exportar.csv' in html
    assert '#export-csv' in javascript
    assert "exportParams" in javascript


def test_tela_expoe_indicador_de_atraso_da_rpi() -> None:
    # Achado do usuário (20/09/2026): "Atualizar" não busca nada novo no
    # INPI, só reprocessa o que já foi importado -- sem indicador, o
    # atendimento não sabia se a base estava desatualizada.
    page = "app/web/admin-carteira.html"
    script = "app/web/static/admin-carteira.js"
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert 'id="rpi-status"' in html
    assert "function loadRpiStatus" in javascript
    assert "/v1/admin/carteira/rpi-status" in javascript
    assert "loadRpiStatus()" in javascript


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
    assert HistoricoEtapaCarteira.__table__.c.organizacao_id.foreign_keys


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


def test_atualizar_lote_reconsolida_carteira_ativa_por_padrao() -> None:
    m1 = ProcessoMonitorado(id=1, organizacao_id=1, processo_id=10, status="ativo", vinculado_por="teste")
    m2 = ProcessoMonitorado(id=2, organizacao_id=1, processo_id=11, status="ativo", vinculado_por="teste")
    session = FakeSession([FakeResult(itens=[m1, m2]), FakeResult(rowcount=1)])

    resultado = asyncio.run(atualizar_status_lote(AtualizacaoLote(), _request(), session, usuario_teste()))

    assert resultado == {"status": "ok", "verificados": 2, "alterados": 1}
    assert session.commits == 1
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.acao == "atualizar_lote_carteira"[:20]
    assert evento.detalhes == {"verificados": 2, "alterados": 1}


def test_atualizar_lote_aceita_ids_especificos() -> None:
    m1 = ProcessoMonitorado(id=5, organizacao_id=1, processo_id=50, status="pausado", vinculado_por="teste")
    session = FakeSession([FakeResult(itens=[m1]), FakeResult(rowcount=0)])

    resultado = asyncio.run(
        atualizar_status_lote(AtualizacaoLote(monitorado_ids=[5]), _request(), session, usuario_teste())
    )

    assert resultado == {"status": "ok", "verificados": 1, "alterados": 0}


def test_atualizar_lote_sem_processos_devolve_404() -> None:
    session = FakeSession([FakeResult(itens=[])])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(atualizar_status_lote(AtualizacaoLote(), _request(), session, usuario_teste()))

    assert erro.value.status_code == 404
    assert session.commits == 0


def test_atribuir_lote_aplica_empresa_e_responsavel_aos_selecionados() -> None:
    # Achado do usuário (20/09/2026): atribuir empresa/responsável a
    # processos já monitorados só existia um por um (PATCH /{id}) --
    # inviável para dezenas de processos "Não atribuído"/"Sem empresa
    # vinculada" numa carteira grande.
    m1 = ProcessoMonitorado(id=1, organizacao_id=1, processo_id=10, status="ativo", vinculado_por="teste")
    m2 = ProcessoMonitorado(id=2, organizacao_id=1, processo_id=11, status="ativo", vinculado_por="teste")
    empresa = EmpresaCRM(id=7, organizacao_id=1, nome="Padaria do Zé", nome_normalizado="padaria do ze")
    session = FakeSession(
        [
            FakeResult(itens=[m1, m2]),
            FakeResult(scalar=empresa),
            FakeResult(scalar=9),
        ]
    )

    resultado = asyncio.run(
        atribuir_lote(
            AtribuicaoLote(monitorado_ids=[1, 2], empresa_id=7, responsavel_id=9),
            _request(),
            session,
            usuario_teste(),
        )
    )

    assert resultado == {"status": "ok", "atribuidos": 2}
    assert m1.empresa_id == 7 and m1.responsavel_id == 9
    assert m2.empresa_id == 7 and m2.responsavel_id == 9
    assert session.commits == 1
    evento = next(obj for obj in session.adicionados if isinstance(obj, EventoAuditoria))
    assert evento.detalhes == {"processos": [1, 2], "empresa_id": 7, "responsavel_id": 9}


def test_atribuir_lote_sem_nenhum_campo_devolve_400() -> None:
    session = FakeSession([])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(atribuir_lote(AtribuicaoLote(monitorado_ids=[1]), _request(), session, usuario_teste()))

    assert erro.value.status_code == 400
    assert session.commits == 0


def test_atribuir_lote_sem_processos_encontrados_devolve_404() -> None:
    session = FakeSession([FakeResult(itens=[])])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            atribuir_lote(
                AtribuicaoLote(monitorado_ids=[999], responsavel_id=9),
                _request(),
                session,
                usuario_teste(),
            )
        )

    assert erro.value.status_code == 404
    assert session.commits == 0


async def _exportar_e_ler_csv(*args: object, **kwargs: object) -> str:
    resposta = await exportar_carteira(*args, **kwargs)
    return "".join([parte async for parte in resposta.body_iterator])


def test_exportar_carteira_gera_csv_com_cabecalho_e_linhas() -> None:
    # Achado da análise da tela "Processos monitorados" pedida pelo usuário
    # (20/09/2026): só existia relatório em PDF processo por processo, nada
    # pra exportar a carteira inteira (ou um filtro) de uma vez.
    processo = Processo(
        id=1,
        numero="937557234",
        numero_normalizado="937557234",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2901",
        titulo="ECQ",
        procurador="José Vicente",
        situacao="Deferido",
    )
    monitorado = ProcessoMonitorado(
        id=1, organizacao_id=1, processo_id=1, status="ativo", origem="manual", vinculado_por="teste"
    )
    session = FakeSession([FakeResult(itens=[(monitorado, processo, "Padaria do Zé", "Ana", "2901", None)])])

    conteudo = asyncio.run(_exportar_e_ler_csv(session, usuario_teste(), _request()))

    assert "numero;titulo;empresa;procurador;responsavel" in conteudo
    assert "937557234;ECQ;Padaria do Zé;José Vicente;Ana;ativo;Deferido" in conteudo
    assert session.commits == 1


def test_exportar_carteira_escapa_formula_no_titulo() -> None:
    # Mesma proteção contra injeção de fórmula de app/api/leads.py -- um
    # título começando com "=" não pode virar fórmula ao abrir no Excel.
    processo = Processo(
        id=2,
        numero="937999999",
        numero_normalizado="937999999",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2901",
        titulo="=cmd()",
        procurador=None,
    )
    monitorado = ProcessoMonitorado(
        id=2, organizacao_id=1, processo_id=2, status="ativo", origem="manual", vinculado_por="teste"
    )
    session = FakeSession([FakeResult(itens=[(monitorado, processo, None, None, None, None)])])

    conteudo = asyncio.run(_exportar_e_ler_csv(session, usuario_teste(), _request()))

    assert "'=cmd()" in conteudo
    assert "\n=cmd()" not in conteudo


def test_status_sincronizacao_rpi_em_dia() -> None:
    # Achado do usuário (20/09/2026): "Atualizar situação de todos" e
    # "Atualizar status" só reprocessam despachos já importados -- não
    # buscam nada novo no INPI. Sem indicador, o atendimento não sabia se a
    # base estava desatualizada antes de clicar em "Atualizar".
    estado = RpiSyncEstado(id=1, status="ocioso", ultima_rpi_oficial=2901)
    session = FakeSession(objetos_get=[estado], resultados=[FakeResult(scalar=2901)])

    resultado = asyncio.run(obter_status_sincronizacao_rpi(session, usuario_teste()))

    assert resultado["ultima_rpi_local"] == 2901
    assert resultado["ultima_rpi_oficial"] == 2901
    assert resultado["edicoes_atraso"] == 0
    assert resultado["em_dia"] is True


def test_status_sincronizacao_rpi_atrasada() -> None:
    estado = RpiSyncEstado(id=1, status="ocioso", ultima_rpi_oficial=2905)
    session = FakeSession(objetos_get=[estado], resultados=[FakeResult(scalar=2901)])

    resultado = asyncio.run(obter_status_sincronizacao_rpi(session, usuario_teste()))

    assert resultado["ultima_rpi_local"] == 2901
    assert resultado["ultima_rpi_oficial"] == 2905
    assert resultado["edicoes_atraso"] == 4
    assert resultado["em_dia"] is False


def test_status_sincronizacao_rpi_sem_estado_nao_quebra() -> None:
    session = FakeSession(objetos_get=[None], resultados=[FakeResult(scalar=None)])

    resultado = asyncio.run(obter_status_sincronizacao_rpi(session, usuario_teste()))

    assert resultado["ultima_rpi_local"] is None
    assert resultado["ultima_rpi_oficial"] is None
    assert resultado["edicoes_atraso"] == 0
    assert resultado["em_dia"] is True


def test_atualizar_status_cadastra_empresa_quando_titular_e_unico() -> None:
    processo = Processo(
        id=1,
        numero="937557234",
        numero_normalizado="937557234",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2901",
        situacao="Deferido",
    )
    monitorado = ProcessoMonitorado(
        id=1, organizacao_id=1, processo_id=1, status="ativo", origem="manual", vinculado_por="teste"
    )
    session = FakeSession(
        [
            FakeResult(scalar=monitorado),  # busca do monitorado
            FakeResult(rowcount=0),  # consolidar_situacao
            FakeResult(itens=["Titular Único Ltda"]),  # titulares do processo
            FakeResult(scalar=None),  # obter_ou_criar_empresa: não existe ainda
        ],
        objetos_get=[processo],
    )

    resultado = asyncio.run(atualizar_status_processo(1, _request(), session, usuario_teste()))

    assert resultado["cliente_cadastrado"] == "Titular Único Ltda"
    assert resultado["titulares_multiplos"] is False
    assert monitorado.empresa_id is not None


def test_atualizar_status_nao_cadastra_empresa_quando_ha_varios_titulares() -> None:
    # Achado do usuário (20/09/2026): antes escolhia o titular
    # "alfabeticamente primeiro" sozinho -- com cotitularidade, isso podia
    # vincular a empresa errada sem nenhum aviso.
    processo = Processo(
        id=2,
        numero="937999999",
        numero_normalizado="937999999",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2901",
        situacao="Em tramitação",
    )
    monitorado = ProcessoMonitorado(
        id=2, organizacao_id=1, processo_id=2, status="ativo", origem="manual", vinculado_por="teste"
    )
    session = FakeSession(
        [
            FakeResult(scalar=monitorado),
            FakeResult(rowcount=0),
            FakeResult(itens=["Ana Comércio Ltda", "Beto Distribuidora Ltda"]),
        ],
        objetos_get=[processo],
    )

    resultado = asyncio.run(atualizar_status_processo(2, _request(), session, usuario_teste()))

    assert resultado["cliente_cadastrado"] is None
    assert resultado["titulares_multiplos"] is True
    assert monitorado.empresa_id is None


def test_tela_avisa_quando_atualizar_status_encontra_varios_titulares() -> None:
    script = "app/web/static/admin-carteira.js"
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert "result.titulares_multiplos" in javascript
    assert "vincule a empresa manualmente" in javascript


def test_atualizar_status_processo_nao_encontrado_devolve_404() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(atualizar_status_processo(999, _request(), session, usuario_teste()))

    assert erro.value.status_code == 404
    assert session.commits == 0


def test_atualizar_status_nao_mexe_em_empresa_ja_vinculada() -> None:
    # Empresa já vinculada -- nem chega a consultar titulares, não importa
    # se o processo tem 1, 2 ou nenhum.
    processo = Processo(
        id=3,
        numero="938111222",
        numero_normalizado="938111222",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2901",
        situacao="Registrada",
    )
    monitorado = ProcessoMonitorado(
        id=3,
        organizacao_id=1,
        processo_id=3,
        status="ativo",
        origem="manual",
        vinculado_por="teste",
        empresa_id=42,
    )
    session = FakeSession(
        [FakeResult(scalar=monitorado), FakeResult(rowcount=0)],
        objetos_get=[processo],
    )

    resultado = asyncio.run(atualizar_status_processo(3, _request(), session, usuario_teste()))

    assert resultado["cliente_cadastrado"] is None
    assert resultado["titulares_multiplos"] is False
    assert monitorado.empresa_id == 42


def test_atualizar_status_sem_titular_nao_cadastra_nada() -> None:
    processo = Processo(
        id=4,
        numero="938222333",
        numero_normalizado="938222333",
        tipo=TipoProcesso.MARCA,
        fonte="RPI 2901",
        situacao="Em tramitação",
    )
    monitorado = ProcessoMonitorado(
        id=4, organizacao_id=1, processo_id=4, status="ativo", origem="manual", vinculado_por="teste"
    )
    session = FakeSession(
        [FakeResult(scalar=monitorado), FakeResult(rowcount=0), FakeResult(itens=[])],
        objetos_get=[processo],
    )

    resultado = asyncio.run(atualizar_status_processo(4, _request(), session, usuario_teste()))

    assert resultado["cliente_cadastrado"] is None
    assert resultado["titulares_multiplos"] is False
    assert monitorado.empresa_id is None


def _fake_session_listar_carteira() -> FakeSession:
    return FakeSession(
        [
            FakeResult(itens=[]),  # resumo por status
            FakeResult(itens=[]),  # resumo por situação INPI
            FakeResult(scalar=0),  # total
            FakeResult(itens=[]),  # itens da página
        ]
    )


def test_listar_carteira_informa_quando_usuario_pode_gerenciar() -> None:
    # Achado do usuário (20/09/2026): a tela sempre mostrava todos os
    # botões de gerenciamento, mesmo pra quem só tem portfolio.view
    # (perfis "comercial" e "auditor") -- clicar em qualquer um devolvia
    # "Acesso não autorizado" sem aviso. Mesmo padrão de app/api/leads.py
    # ("acoes.gerenciar"): a tela esconde o que a API já sabe que vai
    # recusar.
    resultado = asyncio.run(listar_carteira(_fake_session_listar_carteira(), usuario_teste()))
    assert resultado["acoes"] == {"gerenciar": True, "buscar_leads": True}


def test_listar_carteira_informa_quando_usuario_nao_pode_gerenciar() -> None:
    usuario_comercial = usuario_teste(perfil="comercial", permissoes={"portfolio.view"})
    resultado = asyncio.run(listar_carteira(_fake_session_listar_carteira(), usuario_comercial))
    assert resultado["acoes"] == {"gerenciar": False, "buscar_leads": False}


def test_listar_carteira_informa_quando_usuario_pode_gerenciar_mas_nao_buscar_leads() -> None:
    # Achado do Codex review (PR #97): portfolio.manage e leads.view são
    # permissões independentes -- uma conta pode ter só a primeira.
    usuario_sem_leads = usuario_teste(perfil="comercial", permissoes={"portfolio.view", "portfolio.manage"})
    resultado = asyncio.run(listar_carteira(_fake_session_listar_carteira(), usuario_sem_leads))
    assert resultado["acoes"] == {"gerenciar": True, "buscar_leads": False}


def test_tela_esconde_botoes_de_gerenciamento_para_quem_so_tem_view() -> None:
    # Achado do revisor (Codex, PR #82): /procuradores e /buscar-procurador
    # usam ViewDep e gerar_relatorio_pdf também -- a busca por procurador e
    # o botão "Gerar relatório" continuam disponíveis pra quem só tem
    # portfolio.view; só o vínculo (empresa/responsável/"Vincular...") e a
    # edição de status/procurador exigem portfolio.manage.
    page = "app/web/admin-carteira.html"
    script = "app/web/static/admin-carteira.js"
    with open(page, encoding="utf-8") as arquivo:
        html = arquivo.read()
    with open(script, encoding="utf-8") as arquivo:
        javascript = arquivo.read()

    assert "function applyManagePermissions" in javascript
    assert "data.acoes?.gerenciar" in javascript
    assert '"#open-manual"' in javascript
    assert '"#open-import"' in javascript
    assert '"#attorney-link-bar"' in javascript
    assert '"#attorney-select-col"' in javascript
    assert 'id="attorney-link-bar" class="portfolio-link-bar" hidden' in html
    assert 'id="attorney-select-col" hidden' in html
    assert '<button class="secondary-button" data-relatorio type="button">Gerar relatório</button>' in javascript
    # Escondido por padrão no HTML estático -- não fica visível/clicável
    # entre o carregamento da página e a resposta de /v1/admin/carteira
    # confirmando (ou recusando) portfolio.manage.
    assert '<button id="open-manual" class="primary-button" type="button" hidden>' in html
    assert '<button id="open-import" class="secondary-button" type="button" hidden>' in html
    assert '<button id="update-all" class="secondary-button" type="button" hidden>' in html
    assert '<div id="portfolio-bulk-assign" class="portfolio-link-bar" hidden>' in html
