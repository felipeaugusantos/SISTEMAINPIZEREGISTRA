import asyncio
import base64
import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.juridico import (
    CHECKLIST_GENERICO,
    CHECKLIST_PADRAO,
    CODIGOS_DESPACHO_PRAZO,
    CODIGOS_DESPACHO_TERMINAL,
    MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO,
    PADRAO_PRAZO,
    PRAZO_ADMINISTRATIVO_PADRAO_DIAS,
    TIPOS_PRAZO,
    ChecklistItemUpdate,
    EntregaInput,
    PoliticaJuridicaUpdate,
    PrazoInput,
    PrazoUpdate,
    ReceberEncaminhamentoInput,
    RegraJuridicaInput,
    _classificar_despacho,
    _classificar_despacho_terminal,
    _dias_restantes,
    _dispensa_concessao,
    _documentos_por_lead,
    _emails_usuarios,
    _filtro_busca_painel,
    _pascoa,
    _pendencias_encaminhamento,
    _reconciliar_prazos_historicos,
    _reconciliar_prazos_terminais,
    _serializar_prazo,
    _status_encaminhamento,
    _valor_vigente,
    atualizar_item_checklist,
    atualizar_prazo,
    calcular_vencimento,
    consultar_regras_juridicas,
    criar_prazo,
    criar_regra_juridica,
    editar_politica_juridica,
    executar_motor_organizacao,
    indicadores_juridicos,
    listar_documentos_entrega,
    painel,
    receber_encaminhamento,
    registrar_entrega,
)
from app.models import (
    DocumentoEntregaJuridico,
    DocumentoLead,
    EventoDominio,
    EventoJuridico,
    Lead,
    Movimentacao,
    MovimentacaoAvaliadaJuridico,
    PoliticaJuridica,
    PrazoJuridico,
    ProcessoMonitorado,
    PropostaComercial,
    RegraJuridicaVersionada,
    UsuarioOperacoes,
)
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/admin/juridico/prazos",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


# --- Achado JUR-1 da auditoria (04/09/2026): calcular_vencimento rotulava
# 23:59:59 de uma data civil brasileira diretamente como UTC -- prazos
# apareciam vencidos ate 3h antes da meia-noite real em Brasilia. Os
# asserts abaixo comparam pela data civil em America/Sao_Paulo (o que
# `vencimento` de fato representa), nao pelo `.date()` cru em UTC. ---
FUSO_BRASIL_TESTE = ZoneInfo("America/Sao_Paulo")


def _data_brasil(vencimento: datetime) -> date:
    return vencimento.astimezone(FUSO_BRASIL_TESTE).date()


def test_status_encaminhamento_explica_bloqueios_e_recebimento() -> None:
    proposta = PropostaComercial(pagamento_status="pendente")
    assert _status_encaminhamento(proposta, []) == "pagamento_pendente"
    proposta.pagamento_status = "confirmado"
    assert _status_encaminhamento(proposta, ["procuracao"]) == "documentos_pendentes"
    assert _status_encaminhamento(proposta, []) == "pronto"
    proposta.juridico_recebido_em = datetime.now(UTC)
    assert _status_encaminhamento(proposta, []) == "em_atendimento"


def test_pendencias_encaminhamento_exige_procuracao_mesmo_sem_documentos() -> None:
    assert _pendencias_encaminhamento([]) == ["procuracao"]


def test_pendencias_encaminhamento_considera_status_e_validade() -> None:
    hoje = datetime.now(UTC).date()
    documentos = [
        DocumentoLead(lead_id=1, tipo="procuracao", status="validado", obrigatorio=True),
        DocumentoLead(lead_id=1, tipo="gru", status="pendente", obrigatorio=True),
        DocumentoLead(lead_id=1, tipo="certificado", status="validado", obrigatorio=True, validade_em=hoje - timedelta(days=1)),
    ]

    assert _pendencias_encaminhamento(documentos) == ["certificado", "gru"]


def test_documentos_por_lead_busca_tudo_em_uma_unica_consulta() -> None:
    # Achado da analise do modulo (13/09/2026): listar_encaminhamentos fazia
    # uma consulta de DocumentoLead por proposta da fila -- este teste
    # garante que a busca em lote fica restrita a uma unica chamada a
    # session.execute, nao uma por lead_id.
    documentos = [
        DocumentoLead(lead_id=1, tipo="procuracao", status="validado", obrigatorio=True),
        DocumentoLead(lead_id=2, tipo="procuracao", status="pendente", obrigatorio=True),
    ]
    session = FakeSession([FakeResult(itens=documentos)])

    agrupados = asyncio.run(_documentos_por_lead(session, 1, {1, 2}))

    assert len(session.executados) == 1
    assert [doc.tipo for doc in agrupados[1]] == ["procuracao"]
    assert [doc.tipo for doc in agrupados[2]] == ["procuracao"]


def test_documentos_por_lead_sem_leads_nao_consulta_o_banco() -> None:
    session = FakeSession([FakeResult(itens=[])])

    agrupados = asyncio.run(_documentos_por_lead(session, 1, set()))

    assert agrupados == {}
    assert session.executados == []


def test_filtro_busca_painel_ignora_acento_e_usa_indice_trigram() -> None:
    expressao = _filtro_busca_painel("Contestação")
    sql = str(expressao)
    valores = [
        valor
        for valor in expressao.compile().params.values()
        if isinstance(valor, str) and valor.startswith("%")
    ]

    assert "immutable_unaccent" in sql
    assert "%contestacao%" in valores


def test_receber_encaminhamento_atribui_responsavel_e_avanca_para_ganho() -> None:
    proposta = PropostaComercial(
        id=7,
        organizacao_id=1,
        lead_id=9,
        numero="PROP-7",
        status="aceita",
        pagamento_status="confirmado",
        escopo="Registro de marca",
    )
    responsavel = UsuarioOperacoes(id=1, organizacao_id=1, nome="Admin Teste", ativo=True)
    lead = Lead(
        id=9,
        organizacao_id=1,
        nome="Cliente",
        email="cliente@example.com",
        telefone="",
        marca="NORTE",
        fase="pagamento_confirmado",
    )
    session = FakeSession(
        [FakeResult(scalar=proposta), FakeResult(scalar=responsavel)],
        objetos_get=[lead],
    )

    resultado = asyncio.run(
        receber_encaminhamento(7, ReceberEncaminhamentoInput(), _request(), session, usuario_teste())
    )

    assert resultado["status"] == "em_atendimento"
    assert proposta.juridico_recebido_por_id == 1
    assert proposta.responsavel_protocolo_id == 1
    assert lead.fase == "ganho"
    assert any(
        isinstance(item, EventoDominio) and item.tipo == "juridico.encaminhamento_recebido"
        for item in session.adicionados
    )


def test_interface_juridica_expoe_fila_de_novos_servicos() -> None:
    html = Path("app/web/admin-juridico.html").read_text(encoding="utf-8")
    script = Path("app/web/static/admin-juridico.js").read_text(encoding="utf-8")

    assert 'id="legal-intakes"' in html
    assert "Novos serviços para iniciar" in html
    assert "/v1/admin/juridico/encaminhamentos" in script
    assert "Assumir atendimento" in script


def test_calcula_prazo_em_dias_corridos() -> None:
    vencimento = calcular_vencimento(date(2026, 8, 11), 10, "corridos")
    assert _data_brasil(vencimento) == date(2026, 8, 21)


def test_vencimento_e_o_instante_utc_do_fim_do_dia_em_brasilia() -> None:
    """23:59:59 em Brasilia (UTC-3, sem horario de verao desde 2019) e
    02:59:59 UTC do dia SEGUINTE -- nao 23:59:59 UTC do mesmo dia."""
    vencimento = calcular_vencimento(date(2026, 8, 11), 10, "corridos")
    assert vencimento == datetime(2026, 8, 22, 2, 59, 59, tzinfo=UTC)


def _fixar_agora(monkeypatch, momento_utc: datetime) -> None:
    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            return momento_utc if tz else momento_utc.replace(tzinfo=None)

    monkeypatch.setattr("app.api.juridico.datetime", _DatetimeFixo)


def test_dias_restantes_nao_marca_vencido_as_22h_de_brasilia_no_dia_do_vencimento(monkeypatch) -> None:
    """Regressao do achado JUR-1: as 22h de Brasilia (01h UTC do dia
    seguinte) de 21/08/2026, um prazo que vence nesse mesmo dia (Brasilia)
    ainda NAO deve estar vencido -- o bug antigo (comparando .date() em UTC
    direto) já marcaria como vencido (-1 dia) nesse horario."""
    vencimento = calcular_vencimento(date(2026, 8, 11), 10, "corridos")  # vence 21/08 em Brasilia
    _fixar_agora(monkeypatch, datetime(2026, 8, 22, 1, 0, 0, tzinfo=UTC))  # 21/08 22h em Brasilia

    assert _dias_restantes(vencimento) == 0


def test_dias_restantes_marca_vencido_so_apos_meia_noite_real_em_brasilia(monkeypatch) -> None:
    vencimento = calcular_vencimento(date(2026, 8, 11), 10, "corridos")  # vence 21/08 em Brasilia
    _fixar_agora(monkeypatch, datetime(2026, 8, 22, 3, 0, 0, tzinfo=UTC))  # 22/08 00h em Brasilia

    assert _dias_restantes(vencimento) == -1


def test_agenda_centralizada_cobre_eventos_de_propriedade_intelectual() -> None:
    assert {"publicacao_rpi", "deferimento", "concessao", "decenio", "vencimento_interno"}.issubset(TIPOS_PRAZO)


def test_calcula_prazo_em_dias_uteis_sem_contar_fim_de_semana() -> None:
    vencimento = calcular_vencimento(date(2026, 8, 14), 2, "uteis")
    assert _data_brasil(vencimento) == date(2026, 8, 18)


# --- Achado 5.1 da auditoria (01/09/2026): a dispensa da taxa de concessão
# usava a data de DEPÓSITO; a regra oficial do INPI (FAQ oficial, item 7,
# gov.br/inpi/pt-br/inpi-data/precificacao-dos-servicos/PerguntaseRespostas)
# isenta pela data de DEFERIMENTO (RPI nº 2842, 24/06/2025), mesmo para
# pedidos depositados antes da nova tabela (Portaria INPI/PR nº 10/2025).
def test_dispensa_concessao_usa_data_de_deferimento_nao_de_deposito() -> None:
    # Depósito antigo (muito antes do marco), deferimento após o corte real:
    # é o caso mais comum na prática e o critério antigo (data de depósito)
    # cobrava indevidamente esse cenário.
    assert _dispensa_concessao("pagamento", date(2025, 8, 15)) is True


def test_dispensa_concessao_nao_se_aplica_a_deferimento_anterior_ao_marco() -> None:
    assert _dispensa_concessao("pagamento", date(2025, 6, 17)) is False


def test_dispensa_concessao_no_limite_exato_do_marco_e_isenta() -> None:
    assert _dispensa_concessao("pagamento", MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO) is True
    assert MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO == date(2025, 6, 24)


def test_dispensa_concessao_nao_se_aplica_a_outros_tipos_de_prazo() -> None:
    assert _dispensa_concessao("exigencia", date(2026, 1, 1)) is False


# --- Achado 5.2 da auditoria: vencimento em dias corridos não prorrogava
# quando caía em sábado, domingo ou feriado nacional -- viola a Portaria
# INPI/PR nº 08/2022, art. 6º, § 2º ("prorroga-se automaticamente para o
# primeiro dia útil o prazo que vença no sábado, domingo ou feriado").
def test_vencimento_corrido_prorroga_quando_cai_em_domingo() -> None:
    # 11/08/2026 + 12 dias corridos = 23/08/2026, um domingo.
    vencimento = calcular_vencimento(date(2026, 8, 11), 12, "corridos")
    assert _data_brasil(vencimento) == date(2026, 8, 24)  # segunda-feira seguinte


def test_vencimento_corrido_prorroga_quando_cai_em_feriado_nacional() -> None:
    # 09/07/2026 + 60 dias corridos = 07/09/2026 (Independência, 2ª-feira).
    vencimento = calcular_vencimento(date(2026, 7, 9), 60, "corridos")
    assert _data_brasil(vencimento) == date(2026, 9, 8)  # 1º dia útil seguinte (terça)


def test_pascoa_calcula_data_correta_para_ano_conhecido() -> None:
    # Domingo de Páscoa de 2026 é 05/04/2026 (data pública/oficialmente
    # conhecida) — verifica o algoritmo do calendário móvel usado para
    # calcular a Sexta-feira Santa.
    assert _pascoa(2026) == date(2026, 4, 5)


def test_vencimento_corrido_prorroga_quando_cai_em_feriado_movel() -> None:
    # 02/02/2026 + 60 dias corridos = 03/04/2026, Sexta-feira Santa.
    vencimento = calcular_vencimento(date(2026, 2, 2), 60, "corridos")
    assert _data_brasil(vencimento) == date(2026, 4, 6)  # pula sáb/dom também


def test_motor_reconhece_prazo_numerico_com_texto_por_extenso() -> None:
    texto = "Cumpra a exigência no prazo de 60 (sessenta) dias."
    match = PADRAO_PRAZO.search(texto)
    assert match is not None
    assert match.group(1) == "60"


def test_classifica_indeferimento_como_recurso_60_dias() -> None:
    assert _classificar_despacho("Indeferimento do pedido") == (
        60,
        "recurso",
        "Recurso contra indeferimento",
    )


def test_classifica_publicacao_para_oposicao() -> None:
    dias, tipo, _ = _classificar_despacho("Publicação de pedido de registro para oposição (exame formal concluído)")
    assert (dias, tipo) == (60, "oposicao")


def test_classifica_deferimento_do_pedido_como_pagamento() -> None:
    dias, tipo, _ = _classificar_despacho("Deferimento do pedido")
    assert (dias, tipo) == (60, "pagamento")


def test_texto_soletrado_tem_prioridade_sobre_prazo_legal() -> None:
    dias, tipo, _ = _classificar_despacho(
        "Exigência Formal Preliminar. Prazo para cumprimento - 30 (Trinta) dias corridos."
    )
    assert (dias, tipo) == (30, "exigencia")


def test_despachos_terminais_nao_geram_prazo() -> None:
    assert _classificar_despacho("Concessão de registro") is None
    assert _classificar_despacho("Arquivamento definitivo de pedido de registro") is None
    assert _classificar_despacho("Recurso não provido (decisão mantida)") is None


def test_classifica_arquivamento_por_falta_de_pagamento_como_terminal() -> None:
    assert _classificar_despacho_terminal(
        "Arquivamento definitivo de pedido de registro por falta de pagamento da concessão"
    ) == (
        "cancelado",
        "Não pago — pedido arquivado por falta de pagamento da concessão",
    )


def test_prazo_historico_nao_e_exibido_como_vencido() -> None:
    prazo = PrazoJuridico(
        id=1,
        organizacao_id=1,
        processo_monitorado_id=1,
        titulo="Histórico: Recurso contra indeferimento (RPI 2568)",
        tipo="recurso",
        origem="motor_rpi",
        data_base=date(2020, 3, 24),
        dias_prazo=60,
        contagem="corridos",
        vencimento_em=datetime(2020, 5, 23, tzinfo=UTC),
        status="historico",
        prioridade="baixa",
        confirmado=True,
        criado_por="motor-juridico",
    )
    item = _serializar_prazo((prazo, "918199131", "3Cash", None, None, None))
    assert item["historico"] is True
    assert item["vencido"] is False


def test_reconcilia_prazo_antigo_quando_rpi_posterior_arquiva_pedido() -> None:
    origem = Movimentacao(
        id=10,
        processo_id=77,
        codigo_despacho="IPAS029",
        descricao="Deferimento do pedido",
        data_rpi=date(2019, 12, 17),
        numero_rpi=2554,
        fonte_arquivo="marcas2554.xml",
        chave_origem="origem",
    )
    terminal = Movimentacao(
        id=11,
        processo_id=77,
        codigo_despacho="IPAS157",
        descricao=("Arquivamento definitivo de pedido de registro por falta de pagamento da concessão"),
        data_rpi=date(2020, 11, 17),
        numero_rpi=2602,
        fonte_arquivo="marcas2602.xml",
        chave_origem="terminal",
    )
    prazo = PrazoJuridico(
        id=215,
        organizacao_id=1,
        processo_monitorado_id=64,
        movimentacao_origem_id=origem.id,
        titulo="Revisar pagamento",
        tipo="pagamento",
        origem="motor_rpi",
        data_base=origem.data_rpi,
        dias_prazo=60,
        contagem="corridos",
        vencimento_em=datetime(2020, 2, 15, tzinfo=UTC),
        status="aguardando_confirmacao",
        prioridade="alta",
        confirmado=False,
        criado_por="motor-juridico",
    )
    session = FakeSession(
        [
            FakeResult(itens=[(prazo, origem)]),
            FakeResult(itens=[terminal]),
        ]
    )

    total, terminais = asyncio.run(_reconciliar_prazos_terminais(session, 1, "motor-juridico"))

    assert total == 1
    assert terminais[77] is terminal
    # Achado crítico da auditoria jurídica (15/09/2026): o motor não pode mais
    # fechar o prazo sozinho (violaria a regra de nunca decidir sem revisão
    # humana) -- só sinaliza a sugestão, deixando o prazo "aguardando_confirmacao"
    # até um humano confirmar explicitamente via PATCH /prazos/{id}.
    assert prazo.status == "aguardando_confirmacao"
    assert prazo.confirmado is False
    assert prazo.concluido_em is None
    assert prazo.concluido_por is None
    evento = next(item for item in session.adicionados if isinstance(item, EventoJuridico))
    assert evento.tipo == "prazo_reconciliado"
    assert evento.detalhes["rpi_terminal"] == 2602
    assert evento.detalhes["status_sugerido"] == "cancelado"


def test_reconcilia_importacao_historica_e_duplicidade_da_mesma_rpi() -> None:
    origem = Movimentacao(
        id=100,
        processo_id=77,
        codigo_despacho="IPAS024",
        descricao="Indeferimento do pedido",
        data_rpi=date(2020, 3, 24),
        numero_rpi=2568,
        fonte_arquivo="marcas2568.xml",
        chave_origem="origem-a",
    )
    origem_repetida = Movimentacao(
        id=101,
        processo_id=77,
        codigo_despacho="IPAS024",
        descricao="Indeferimento do pedido",
        data_rpi=date(2020, 3, 24),
        numero_rpi=2568,
        fonte_arquivo="marcas2568.xml",
        chave_origem="origem-b",
    )
    prazos = []
    for identificador, movimentacao_id in ((213, 100), (214, 101)):
        prazos.append(
            PrazoJuridico(
                id=identificador,
                organizacao_id=1,
                processo_monitorado_id=64,
                movimentacao_origem_id=movimentacao_id,
                titulo="Revisar recurso",
                tipo="recurso",
                origem="motor_rpi",
                data_base=date(2020, 3, 24),
                dias_prazo=60,
                contagem="corridos",
                vencimento_em=datetime(2020, 5, 23, tzinfo=UTC),
                status="aguardando_confirmacao",
                prioridade="alta",
                confirmado=False,
                criado_por="motor-juridico",
                criado_em=datetime(2026, 8, 14, tzinfo=UTC),
            )
        )
    session = FakeSession(
        [
            FakeResult(itens=[(prazos[0], origem), (prazos[1], origem_repetida)]),
            FakeResult(itens=[]),
        ]
    )

    historicos, duplicados = asyncio.run(_reconciliar_prazos_historicos(session, 1, "motor-juridico"))

    assert (historicos, duplicados) == (1, 1)
    assert prazos[0].status == "historico"
    assert prazos[1].status == "duplicado"
    tipos = {item.tipo for item in session.adicionados if isinstance(item, EventoJuridico)}
    assert tipos == {"prazo_historico", "prazo_duplicado"}


def test_deferimento_de_peticao_nao_e_confundido_com_pedido() -> None:
    assert _classificar_despacho("Deferimento da petição") is None


def test_criar_prazo_vincula_processo_e_registra_historico() -> None:
    monitorado = ProcessoMonitorado(id=9, organizacao_id=1, processo_id=33)
    session = FakeSession(
        [
            FakeResult(scalar=monitorado),
            FakeResult(scalar=1),
            FakeResult(scalar=2),
        ]
    )
    resultado = asyncio.run(
        criar_prazo(
            PrazoInput(
                processo_monitorado_id=9,
                titulo="Responder exigência",
                tipo="exigencia",
                data_base=date(2026, 8, 11),
                dias_prazo=60,
                responsavel_id=1,
                escalonar_para_id=2,
            ),
            _request(),
            session,
            usuario_teste(),
        )
    )
    prazos = [item for item in session.adicionados if isinstance(item, PrazoJuridico)]
    eventos = [item for item in session.adicionados if isinstance(item, EventoJuridico)]
    assert resultado["status"] == "pendente"
    # 11/08/2026 + 60 dias corridos = 10/10/2026 (sábado); com a prorrogação
    # da Portaria/INPI/PR nº 08/2022 (achado 5.2 da auditoria), pula o fim de
    # semana E o feriado de 12/10 (Nossa Senhora Aparecida) até o próximo dia
    # útil, 13/10/2026 (terça). Antes da correção, este teste esperava
    # 10/10/2026 — uma data que caía num sábado.
    assert _data_brasil(prazos[0].vencimento_em) == date(2026, 10, 13)
    assert eventos[0].tipo == "prazo_criado"
    assert session.commits == 1


def test_tela_juridica_expoe_fluxos_principais() -> None:
    html = Path("app/web/admin-juridico.html").read_text(encoding="utf-8")
    javascript = Path("app/web/static/admin-juridico.js").read_text(encoding="utf-8")
    shell = Path("app/web/static/admin-shell.js").read_text(encoding="utf-8")
    assert "Operação jurídica" in html
    assert "Executar motor de prazos" in html
    assert "CENTRAL DE NOTIFICAÇÕES" in html
    assert "Registrar entrega" in html
    assert "admin-juridico.css?v=17" in html
    assert "admin-juridico.js?v=" in html
    assert 'id="view-calendar"' in html
    assert 'option value="historico"' in html
    assert "Referência histórica" in javascript
    assert 'id="legal-pagination"' in html
    assert "pageSize: 10" in javascript
    assert 'query.set("limite", legalState.pageSize)' in javascript
    assert "pagination.total_clientes" in javascript
    assert "/v1/admin/juridico/motor/executar" in javascript
    assert "/admin/operacao-juridica" in shell


def test_painel_pagina_dez_clientes_sem_cortar_prazos_do_cliente() -> None:
    agora = datetime.now(UTC)
    linhas = []
    for indice in range(10):
        prazo = PrazoJuridico(
            id=indice + 1,
            organizacao_id=1,
            processo_monitorado_id=indice + 100,
            titulo=f"Prazo {indice}",
            tipo="manifestacao",
            origem="manual",
            data_base=agora.date(),
            dias_prazo=10,
            contagem="corridos",
            vencimento_em=agora + timedelta(days=10),
            status="pendente",
            prioridade="media",
            confirmado=True,
            criado_por="teste",
        )
        linhas.append((prazo, f"900{indice}", f"Marca {indice}", f"Cliente {indice}", None, None))
    session = FakeSession(
        [
            FakeResult(itens=[(11, 0, 0, 0, 0, 11)]),
            FakeResult(itens=list(range(100, 110))),
            FakeResult(itens=linhas),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
        ]
    )

    resultado = asyncio.run(painel(session, usuario_teste(), None, None, None, None, None, 10, 0))

    assert len(resultado["prazos"]) == 10
    assert resultado["paginacao"] == {
        "total_clientes": 11,
        "limite": 10,
        "deslocamento": 0,
        "pagina": 1,
        "total_paginas": 2,
        "tem_anterior": False,
        "tem_proxima": True,
    }


def test_checklist_padrao_cobre_todos_os_tipos() -> None:
    for tipo in ("oposicao", "recurso", "exigencia", "pagamento", "manifestacao"):
        assert CHECKLIST_PADRAO[tipo], f"template vazio para {tipo}"
    assert CHECKLIST_GENERICO


def test_checklist_tipo_desconhecido_usa_generico() -> None:
    assert CHECKLIST_PADRAO.get("inexistente", CHECKLIST_GENERICO) is CHECKLIST_GENERICO


def test_marcar_item_registra_autor_e_data() -> None:
    item = SimpleNamespace(
        id=5,
        descricao="Protocolar no INPI",
        concluido=False,
        ordem=1,
        concluido_em=None,
        concluido_por=None,
    )
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste()
    resultado = asyncio.run(atualizar_item_checklist(5, ChecklistItemUpdate(concluido=True), session, usuario))
    assert resultado["concluido"] is True
    assert resultado["concluido_por"] == usuario.ator
    assert item.concluido_em is not None


def test_desmarcar_item_limpa_autor_e_data() -> None:
    item = SimpleNamespace(
        id=5,
        descricao="Protocolar no INPI",
        concluido=True,
        ordem=1,
        concluido_em=object(),
        concluido_por="alguem",
    )
    session = FakeSession([FakeResult(scalar=item)])
    resultado = asyncio.run(atualizar_item_checklist(5, ChecklistItemUpdate(concluido=False), session, usuario_teste()))
    assert resultado["concluido"] is False
    assert item.concluido_em is None
    assert item.concluido_por is None


# --- Achado 5.3 da auditoria (01/09/2026): dupla conferência e evidência de conclusão ---


def _prazo_ativo(**overrides: object) -> PrazoJuridico:
    base: dict = {
        "id": 9,
        "organizacao_id": 1,
        "processo_monitorado_id": 3,
        "titulo": "Responder exigência",
        "tipo": "exigencia",
        "origem": "motor_rpi",
        "data_base": date(2026, 8, 14),
        "dias_prazo": 60,
        "contagem": "corridos",
        "vencimento_em": datetime(2026, 10, 13, tzinfo=UTC),
        "status": "pendente",
        "prioridade": "alta",
        "confirmado": True,
        "confirmado_por_id": 2,
        "criado_por": "motor-juridico",
    }
    base.update(overrides)
    return PrazoJuridico(**base)


def test_cancelamento_sem_justificativa_e_rejeitado() -> None:
    prazo = _prazo_ativo()
    session = FakeSession([FakeResult(scalar=prazo)])
    try:
        asyncio.run(atualizar_prazo(9, PrazoUpdate(status="cancelado"), _request(), session, usuario_teste()))
        raise AssertionError("Esperava HTTPException 422 por falta de justificativa")
    except HTTPException as erro:
        assert erro.status_code == 422


def test_cancelamento_com_justificativa_e_aceito() -> None:
    prazo = _prazo_ativo()
    session = FakeSession([FakeResult(scalar=prazo)])
    resultado = asyncio.run(
        atualizar_prazo(
            9,
            PrazoUpdate(status="cancelado", descricao_evento="Processo arquivado pelo cliente."),
            _request(),
            session,
            usuario_teste(),
        )
    )
    assert resultado["status"] == "cancelado"


def test_conclusao_sem_politica_configurada_nao_exige_evidencia() -> None:
    prazo = _prazo_ativo()
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=None)])
    resultado = asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario_teste()))
    assert resultado["status"] == "concluido"


def test_conclusao_exige_evidencia_quando_politica_ativa() -> None:
    prazo = _prazo_ativo()
    politica = PoliticaJuridica(organizacao_id=1, exigir_evidencia_conclusao=True, exigir_segunda_pessoa_critico=False)
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=politica), FakeResult(scalar=None)])
    try:
        asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario_teste()))
        raise AssertionError("Esperava HTTPException 422 por falta de evidência de entrega")
    except HTTPException as erro:
        assert erro.status_code == 422


def test_conclusao_permitida_quando_ha_entrega_registrada() -> None:
    prazo = _prazo_ativo()
    politica = PoliticaJuridica(organizacao_id=1, exigir_evidencia_conclusao=True, exigir_segunda_pessoa_critico=False)
    session = FakeSession(
        [FakeResult(scalar=prazo), FakeResult(scalar=0), FakeResult(scalar=politica), FakeResult(scalar=1)]
    )
    resultado = asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario_teste()))
    assert resultado["status"] == "concluido"


def test_conclusao_de_prazo_critico_exige_segunda_pessoa_quando_politica_ativa() -> None:
    usuario = usuario_teste()
    prazo = _prazo_ativo(prioridade="critica", confirmado_por_id=usuario.id)
    politica = PoliticaJuridica(organizacao_id=1, exigir_evidencia_conclusao=False, exigir_segunda_pessoa_critico=True)
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=0), FakeResult(scalar=politica)])
    try:
        asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario))
        raise AssertionError("Esperava HTTPException 422 por falta de segunda pessoa na conclusão")
    except HTTPException as erro:
        assert erro.status_code == 422


def test_conclusao_de_prazo_critico_permitida_quando_outra_pessoa_confirmou() -> None:
    usuario = usuario_teste()
    prazo = _prazo_ativo(prioridade="critica", confirmado_por_id=usuario.id + 1)
    politica = PoliticaJuridica(organizacao_id=1, exigir_evidencia_conclusao=False, exigir_segunda_pessoa_critico=True)
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=0), FakeResult(scalar=politica)])
    resultado = asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario))
    assert resultado["status"] == "concluido"


# --- Achado FASE3-2 da auditoria (04/09/2026): nada bloqueava concluir um
# prazo com itens do checklist ainda pendentes. ---


def test_conclusao_bloqueada_quando_ha_item_de_checklist_pendente() -> None:
    prazo = _prazo_ativo()
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=2)])
    try:
        asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario_teste()))
        raise AssertionError("Esperava HTTPException 422 por checklist pendente")
    except HTTPException as erro:
        assert erro.status_code == 422
        assert "checklist" in erro.detail.lower()


def test_conclusao_permitida_quando_checklist_esta_vazio() -> None:
    """Checklist vazio (nenhum item aplicado) não bloqueia -- é opcional."""
    prazo = _prazo_ativo()
    politica = PoliticaJuridica(organizacao_id=1, exigir_evidencia_conclusao=False, exigir_segunda_pessoa_critico=False)
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=0), FakeResult(scalar=politica)])
    resultado = asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario_teste()))
    assert resultado["status"] == "concluido"


def test_conclusao_de_prazo_ja_concluido_nao_reexige_evidencia() -> None:
    prazo = _prazo_ativo(status="concluido")
    session = FakeSession([FakeResult(scalar=prazo)])
    resultado = asyncio.run(atualizar_prazo(9, PrazoUpdate(prioridade="media"), _request(), session, usuario_teste()))
    assert resultado["status"] == "concluido"


# --- Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): nada impedia
# concluir um prazo ainda não confirmado por um humano -- o gate
# "aguardando_confirmacao"/confirmado=False (achado crítico, PR #50) só se
# aplicava ao motor automático, não a esta rota. ---


def test_conclusao_bloqueada_quando_prazo_nunca_foi_confirmado() -> None:
    prazo = _prazo_ativo(status="aguardando_confirmacao", confirmado=False, confirmado_por_id=None)
    session = FakeSession([FakeResult(scalar=prazo)])
    try:
        asyncio.run(atualizar_prazo(9, PrazoUpdate(status="concluido"), _request(), session, usuario_teste()))
        raise AssertionError("Esperava HTTPException 422 por prazo não confirmado")
    except HTTPException as erro:
        assert erro.status_code == 422
        assert "confirme" in erro.detail.lower()
    assert prazo.status == "aguardando_confirmacao"


def test_conclusao_permitida_quando_confirmada_na_mesma_requisicao() -> None:
    # Confirmar e concluir num único PATCH (confirmar=True + status=concluido)
    # continua funcionando -- o gate olha prazo.confirmado, que já é True
    # nesse ponto porque o bloco de confirmação roda antes.
    prazo = _prazo_ativo(
        status="aguardando_confirmacao", confirmado=False, confirmado_por_id=None, responsavel_id=2
    )
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(scalar=0), FakeResult(scalar=None)])
    resultado = asyncio.run(
        atualizar_prazo(
            9,
            PrazoUpdate(status="concluido", confirmar=True, confirmacao_observacoes="Conferido no BuscaWeb."),
            _request(),
            session,
            usuario_teste(),
        )
    )
    assert resultado["status"] == "concluido"
    assert prazo.confirmado is True


def test_editar_politica_juridica_cria_registro_quando_inexistente() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    resultado = asyncio.run(
        editar_politica_juridica(
            PoliticaJuridicaUpdate(exigir_evidencia_conclusao=True, exigir_segunda_pessoa_critico=True),
            session,
            usuario_teste(),
        )
    )
    assert resultado == {"exigir_evidencia_conclusao": True, "exigir_segunda_pessoa_critico": True}
    assert session.commits == 1
    assert len(session.adicionados) == 1


# --- Achado 5.5 da auditoria (02/09/2026): versionamento de regras jurídicas (Fase 3) ---


def _regra(**overrides: object) -> RegraJuridicaVersionada:
    base: dict = {
        "id": 1,
        "codigo": "marco_isencao_taxa_concessao",
        "valor": {"valor": "2025-06-24"},
        "vigencia_inicio": date(2025, 6, 22),
        "vigencia_fim": None,
        "fonte_legal": "FAQ oficial do INPI, item 7.",
        "observacoes": None,
        "criado_por": "superadmin@teste.local",
    }
    base.update(overrides)
    return RegraJuridicaVersionada(**base)


def test_valor_vigente_usa_padrao_quando_historico_vazio() -> None:
    assert _valor_vigente([], date(2026, 1, 1), PRAZO_ADMINISTRATIVO_PADRAO_DIAS) == PRAZO_ADMINISTRATIVO_PADRAO_DIAS


def test_valor_vigente_escolhe_linha_cuja_vigencia_contem_a_data() -> None:
    historico = [_regra(valor={"valor": "2025-07-01"})]
    assert _valor_vigente(historico, date(2025, 8, 1), MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO) == "2025-07-01"


def test_valor_vigente_ignora_linha_expirada_e_usa_a_seguinte() -> None:
    historico = [
        _regra(valor={"valor": "2025-06-24"}, vigencia_inicio=date(2025, 6, 22), vigencia_fim=date(2026, 1, 1)),
        _regra(valor={"valor": "2026-01-01"}, vigencia_inicio=date(2026, 1, 1), vigencia_fim=None),
    ]
    assert _valor_vigente(historico, date(2026, 3, 1), MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO) == "2026-01-01"


def test_valor_vigente_trata_vigencia_fim_como_exclusiva() -> None:
    historico = [
        _regra(valor={"valor": "2025-06-24"}, vigencia_inicio=date(2025, 6, 22), vigencia_fim=date(2026, 1, 1)),
        _regra(valor={"valor": "2026-01-01"}, vigencia_inicio=date(2026, 1, 1), vigencia_fim=None),
    ]
    assert _valor_vigente(historico, date(2026, 1, 1), MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO) == "2026-01-01"


def test_classificar_despacho_usa_dias_padrao_customizado() -> None:
    resultado = _classificar_despacho("Exigência formulada pelo examinador.", dias_padrao=90)
    assert resultado == (90, "exigencia", "Cumprimento de exigência")


def test_dispensa_concessao_usa_marco_customizado() -> None:
    assert _dispensa_concessao("pagamento", date(2025, 1, 10), marco=date(2025, 1, 1)) is True
    assert _dispensa_concessao("pagamento", date(2025, 1, 10), marco=date(2025, 6, 24)) is False


def test_regra_juridica_input_rejeita_valor_do_tipo_errado_para_o_codigo() -> None:
    try:
        RegraJuridicaInput(
            codigo="prazo_administrativo_padrao_dias",
            valor=date(2025, 1, 1),
            vigencia_inicio=date(2026, 1, 1),
            fonte_legal="Fonte legal qualquer.",
        )
        raise AssertionError("Esperava ValidationError por tipo de valor incompatível com o código")
    except ValidationError:
        pass


def test_regra_juridica_input_rejeita_dias_fora_do_intervalo_valido() -> None:
    try:
        RegraJuridicaInput(
            codigo="prazo_administrativo_padrao_dias",
            valor=400,
            vigencia_inicio=date(2026, 1, 1),
            fonte_legal="Fonte legal qualquer.",
        )
        raise AssertionError("Esperava ValidationError por dias fora de 1..365")
    except ValidationError:
        pass


def test_criar_regra_juridica_sem_vigencia_aberta_anterior() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    resultado = asyncio.run(
        criar_regra_juridica(
            RegraJuridicaInput(
                codigo="marco_isencao_taxa_concessao",
                valor=date(2025, 6, 24),
                vigencia_inicio=date(2025, 6, 22),
                fonte_legal="FAQ oficial do INPI, item 7.",
            ),
            _request(),
            session,
            usuario_teste(),
        )
    )
    assert resultado["codigo"] == "marco_isencao_taxa_concessao"
    assert resultado["valor"] == "2025-06-24"
    assert session.commits == 1


def test_criar_regra_juridica_fecha_vigencia_aberta_anterior() -> None:
    aberta = _regra(codigo="prazo_administrativo_padrao_dias", valor={"valor": 60}, vigencia_inicio=date(2020, 1, 1))
    session = FakeSession([FakeResult(scalar=aberta)])
    asyncio.run(
        criar_regra_juridica(
            RegraJuridicaInput(
                codigo="prazo_administrativo_padrao_dias",
                valor=90,
                vigencia_inicio=date(2026, 1, 1),
                fonte_legal="Portaria hipotética de teste.",
            ),
            _request(),
            session,
            usuario_teste(),
        )
    )
    assert aberta.vigencia_fim == date(2026, 1, 1)


def test_criar_regra_juridica_rejeita_vigencia_anterior_a_aberta() -> None:
    aberta = _regra(codigo="prazo_administrativo_padrao_dias", valor={"valor": 60}, vigencia_inicio=date(2026, 1, 1))
    session = FakeSession([FakeResult(scalar=aberta)])
    try:
        asyncio.run(
            criar_regra_juridica(
                RegraJuridicaInput(
                    codigo="prazo_administrativo_padrao_dias",
                    valor=90,
                    vigencia_inicio=date(2025, 1, 1),
                    fonte_legal="Portaria hipotética de teste.",
                ),
                _request(),
                session,
                usuario_teste(),
            )
        )
        raise AssertionError("Esperava HTTPException 422 por vigência retroativa à vigência aberta")
    except HTTPException as erro:
        assert erro.status_code == 422


def test_consultar_regras_juridicas_retorna_lista_serializada() -> None:
    session = FakeSession([FakeResult(itens=[_regra()])])
    resultado = asyncio.run(consultar_regras_juridicas(session, usuario_teste(), None))
    assert resultado[0]["codigo"] == "marco_isencao_taxa_concessao"
    assert resultado[0]["valor"] == "2025-06-24"


# --- Achado 5.6 da auditoria (02/09/2026): backlog do motor jurídico (Fase 4) ---


def test_motor_marca_despacho_nao_mapeado_como_avaliado_em_vez_de_ignorar_para_sempre() -> None:
    monitorado = ProcessoMonitorado(id=1, processo_id=10, organizacao_id=1, status="ativo")
    sem_prazo = Movimentacao(
        id=101,
        processo_id=10,
        descricao="Publicação sem despacho mapeado pelo motor.",
        data_rpi=date(2030, 1, 5),
        numero_rpi=2900,
        codigo_despacho="IPAS999",
        fonte_arquivo="",
        chave_origem="chave-101",
    )
    com_exigencia = Movimentacao(
        id=102,
        processo_id=10,
        descricao="Exigência formulada pelo examinador.",
        data_rpi=date(2030, 1, 6),
        numero_rpi=2901,
        codigo_despacho="IPAS010",
        fonte_arquivo="",
        chave_origem="chave-102",
    )
    session = FakeSession(
        [
            FakeResult(itens=[]),  # _reconciliar_prazos_terminais: pendencias
            FakeResult(itens=[]),  # _reconciliar_prazos_historicos: linhas
            FakeResult(itens=[]),  # prazos ativos confirmados
            FakeResult(itens=[(monitorado, sem_prazo), (monitorado, com_exigencia)]),  # candidatos
            FakeResult(itens=[]),  # _terminais_dos_processos
            FakeResult(itens=[]),  # chaves_existentes
            FakeResult(itens=[]),  # historico_prazo_padrao
            FakeResult(itens=[]),  # historico_marco_isencao
        ]
    )
    resultado = asyncio.run(executar_motor_organizacao(session, organizacao_id=1))

    assert resultado["avaliados_sem_prazo"] == 1
    assert resultado["prazos_sugeridos"] == 1
    marcas = [obj for obj in session.adicionados if isinstance(obj, MovimentacaoAvaliadaJuridico)]
    assert len(marcas) == 1
    assert marcas[0].movimentacao_id == 101
    assert marcas[0].motivo == "sem_prazo_mapeado"
    prazos_criados = [obj for obj in session.adicionados if isinstance(obj, PrazoJuridico)]
    assert len(prazos_criados) == 1
    assert prazos_criados[0].tipo == "exigencia"


def test_motor_sinaliza_backlog_no_limite_quando_consulta_retorna_o_maximo() -> None:
    monitorado = ProcessoMonitorado(id=1, processo_id=10, organizacao_id=1, status="ativo")
    movimentacao = Movimentacao(
        id=201,
        processo_id=10,
        descricao="Exigência formulada pelo examinador.",
        data_rpi=date(2030, 1, 6),
        numero_rpi=2901,
        codigo_despacho="IPAS010",
        fonte_arquivo="",
        chave_origem="chave-201",
    )
    candidatos_no_limite = [(monitorado, movimentacao)] * 2000
    session = FakeSession(
        [
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=candidatos_no_limite),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
            FakeResult(itens=[]),
        ]
    )
    resultado = asyncio.run(executar_motor_organizacao(session, organizacao_id=1))
    assert resultado["backlog_no_limite"] is True


# --- Achado 5.7 da auditoria (02/09/2026): prioridade do código de despacho (Fase 5) ---


def test_codigos_despacho_prazo_deriva_deferimento_do_catalogo_oficial() -> None:
    assert CODIGOS_DESPACHO_PRAZO["029"] == ("pagamento", "Pagamento da taxa de concessão")


def test_codigos_despacho_terminal_deriva_concessao_do_catalogo_oficial() -> None:
    assert CODIGOS_DESPACHO_TERMINAL["158"] == ("concluido", "Registro concedido pelo INPI")


def test_classificar_despacho_usa_codigo_quando_texto_nao_bate_com_nenhuma_regra() -> None:
    resultado = _classificar_despacho("Despacho publicado na RPI.", codigo_despacho="IPAS029")
    assert resultado == (PRAZO_ADMINISTRATIVO_PADRAO_DIAS, "pagamento", "Pagamento da taxa de concessão")


def test_classificar_despacho_aceita_codigo_em_formato_desp_ou_ipas() -> None:
    via_desp = _classificar_despacho("texto qualquer", codigo_despacho="DESP029")
    via_ipas = _classificar_despacho("texto qualquer", codigo_despacho="IPAS029")
    assert via_desp == via_ipas == (PRAZO_ADMINISTRATIVO_PADRAO_DIAS, "pagamento", "Pagamento da taxa de concessão")


def test_classificar_despacho_texto_soletrado_tem_prioridade_mesmo_com_codigo() -> None:
    resultado = _classificar_despacho("Prazo de 30 (trinta) dias.", codigo_despacho="IPAS029")
    assert resultado == (30, "pagamento", "Pagamento da taxa de concessão")


def test_classificar_despacho_sem_codigo_reconhecido_cai_para_o_texto() -> None:
    resultado = _classificar_despacho("Exigência formulada pelo examinador.", codigo_despacho="IPAS999999")
    assert resultado == (PRAZO_ADMINISTRATIVO_PADRAO_DIAS, "exigencia", "Cumprimento de exigência")


def test_classificar_despacho_terminal_usa_codigo_quando_texto_nao_bate() -> None:
    resultado = _classificar_despacho_terminal("Despacho publicado na RPI.", codigo_despacho="IPAS158")
    assert resultado == ("concluido", "Registro concedido pelo INPI")


def test_classificar_despacho_terminal_sem_codigo_mantem_comportamento_por_texto() -> None:
    resultado = _classificar_despacho_terminal("Concessão de registro deferida.")
    assert resultado == ("concluido", "Registro concedido pelo INPI")


# --- Achado 5.8 da auditoria (02/09/2026): evidência de entrega com hash (Fase 7) ---


def test_registrar_entrega_sem_documento_continua_funcionando_como_antes() -> None:
    prazo = _prazo_ativo()
    session = FakeSession([FakeResult(scalar=prazo)])
    resultado = asyncio.run(
        registrar_entrega(
            prazo.id,
            EntregaInput(descricao="Protocolo BR512345678 registrado.", protocolo="BR512345678"),
            _request(),
            session,
            usuario_teste(),
        )
    )
    assert resultado == {"registrado": True, "documento_id": None}
    documentos = [obj for obj in session.adicionados if isinstance(obj, DocumentoEntregaJuridico)]
    assert documentos == []


def test_entrega_input_exige_nome_quando_anexa_documento_base64() -> None:
    try:
        EntregaInput(descricao="Comprovante anexado.", documento_base64=base64.b64encode(b"x").decode())
        raise AssertionError("Esperava ValidationError por falta de documento_nome")
    except ValidationError:
        pass


def test_registrar_entrega_com_documento_calcula_hash_e_persiste_via_storage() -> None:
    import app.api.juridico as juridico_modulo

    caminhos_salvos: list[tuple[str, bytes]] = []

    def _save_bytes_fake(key: str, content: bytes) -> str:
        caminhos_salvos.append((key, content))
        return f"data/uploads/{key}"

    original = juridico_modulo.save_bytes
    juridico_modulo.save_bytes = _save_bytes_fake
    try:
        prazo = _prazo_ativo()
        session = FakeSession([FakeResult(scalar=prazo)])
        conteudo = b"comprovante de protocolo em pdf"
        resultado = asyncio.run(
            juridico_modulo.registrar_entrega(
                prazo.id,
                EntregaInput(
                    descricao="Protocolo enviado ao INPI.",
                    documento_nome="comprovante.pdf",
                    documento_base64=base64.b64encode(conteudo).decode(),
                    documento_content_type="application/pdf",
                ),
                _request(),
                session,
                usuario_teste(),
            )
        )
    finally:
        juridico_modulo.save_bytes = original

    assert resultado["registrado"] is True
    assert len(caminhos_salvos) == 1
    digest_esperado = hashlib.sha256(conteudo).hexdigest()
    documentos = [obj for obj in session.adicionados if isinstance(obj, DocumentoEntregaJuridico)]
    assert len(documentos) == 1
    assert documentos[0].hash_documento == digest_esperado
    assert documentos[0].nome == "comprovante.pdf"


def test_registrar_entrega_rejeita_base64_invalido() -> None:
    prazo = _prazo_ativo()
    session = FakeSession([FakeResult(scalar=prazo)])
    try:
        asyncio.run(
            registrar_entrega(
                prazo.id,
                EntregaInput(
                    descricao="Comprovante anexado.",
                    documento_nome="comprovante.pdf",
                    documento_base64="isto-nao-e-base64-valido!!!",
                ),
                _request(),
                session,
                usuario_teste(),
            )
        )
        raise AssertionError("Esperava HTTPException 422 por base64 inválido")
    except HTTPException as erro:
        assert erro.status_code == 422


def test_listar_documentos_entrega_retorna_lista_serializada() -> None:
    prazo = _prazo_ativo()
    documento = DocumentoEntregaJuridico(
        id=1,
        organizacao_id=1,
        prazo_id=prazo.id,
        nome="comprovante.pdf",
        hash_documento="abc123",
        caminho="data/uploads/juridico/1/9/abc123-comprovante.pdf",
        content_type="application/pdf",
        criado_por="admin@teste.local",
    )
    session = FakeSession([FakeResult(scalar=prazo), FakeResult(itens=[documento])])
    resultado = asyncio.run(listar_documentos_entrega(prazo.id, session, usuario_teste()))
    assert resultado["documentos"][0]["hash"] == "abc123"
    assert resultado["documentos"][0]["nome"] == "comprovante.pdf"


# --- Achado 5.9 da auditoria (02/09/2026): alerta de prazo por e-mail (Fase 8) ---


def test_emails_usuarios_retorna_mapa_id_para_email() -> None:
    session = FakeSession([FakeResult(itens=[(2, "responsavel@teste.local"), (3, "outro@teste.local")])])
    resultado = asyncio.run(_emails_usuarios(session, organizacao_id=1, usuario_ids={2, 3}))
    assert resultado == {2: "responsavel@teste.local", 3: "outro@teste.local"}


def test_emails_usuarios_nao_consulta_o_banco_quando_nao_ha_ids() -> None:
    resultado = asyncio.run(_emails_usuarios(FakeSession([]), organizacao_id=1, usuario_ids=set()))
    assert resultado == {}


def test_notificar_envia_email_quando_e_notificacao_nova_e_ha_destinatario() -> None:
    import app.api.juridico as juridico_modulo

    chamadas: list[tuple] = []

    async def _enviar_fake(destinatario: str, titulo: str, mensagem_texto: str) -> None:
        chamadas.append((destinatario, titulo, mensagem_texto))

    original = juridico_modulo.enviar_alerta_prazo_juridico
    juridico_modulo.enviar_alerta_prazo_juridico = _enviar_fake
    try:
        prazo = _prazo_ativo()
        session = FakeSession([FakeResult(scalar=None)])
        enviado = asyncio.run(
            juridico_modulo._notificar(
                session,
                prazo,
                "vencido",
                2,
                "Prazo jurídico vencido",
                "Responder exigência venceu há 3 dia(s).",
                "responsavel@teste.local",
            )
        )
    finally:
        juridico_modulo.enviar_alerta_prazo_juridico = original

    assert enviado is True
    assert chamadas == [
        ("responsavel@teste.local", "Prazo jurídico vencido", "Responder exigência venceu há 3 dia(s).")
    ]


def test_notificar_nao_reenvia_email_quando_ja_notificado() -> None:
    import app.api.juridico as juridico_modulo

    chamadas: list[tuple] = []

    async def _enviar_fake(destinatario: str, titulo: str, mensagem_texto: str) -> None:
        chamadas.append((destinatario, titulo, mensagem_texto))

    original = juridico_modulo.enviar_alerta_prazo_juridico
    juridico_modulo.enviar_alerta_prazo_juridico = _enviar_fake
    try:
        prazo = _prazo_ativo()
        session = FakeSession([FakeResult(scalar=123)])  # já existe notificação com essa chave
        enviado = asyncio.run(
            juridico_modulo._notificar(
                session, prazo, "vencido", 2, "Prazo jurídico vencido", "texto", "responsavel@teste.local"
            )
        )
    finally:
        juridico_modulo.enviar_alerta_prazo_juridico = original

    assert enviado is False
    assert chamadas == []


def test_notificar_sem_email_destinatario_nao_tenta_enviar() -> None:
    import app.api.juridico as juridico_modulo

    chamadas: list[tuple] = []

    async def _enviar_fake(destinatario: str, titulo: str, mensagem_texto: str) -> None:
        chamadas.append((destinatario, titulo, mensagem_texto))

    original = juridico_modulo.enviar_alerta_prazo_juridico
    juridico_modulo.enviar_alerta_prazo_juridico = _enviar_fake
    try:
        prazo = _prazo_ativo()
        session = FakeSession([FakeResult(scalar=None)])
        enviado = asyncio.run(
            juridico_modulo._notificar(session, prazo, "vencido", 2, "Prazo jurídico vencido", "texto")
        )
    finally:
        juridico_modulo.enviar_alerta_prazo_juridico = original

    assert enviado is True
    assert chamadas == []


# --- Achado 5.10 da auditoria (02/09/2026): indicadores de gestão (Fase 10) ---


def test_indicadores_calcula_taxa_de_cumprimento_tempo_carga_e_escalonamento() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[(7, 3)]),  # taxa de cumprimento: 7 no prazo, 3 atrasados
            FakeResult(scalar=7200.0),  # tempo médio de confirmação: 7200s = 2h
            FakeResult(itens=[(2, "Ana Responsável", 5, 1), (3, "Bruno Responsável", 2, 0)]),
            FakeResult(itens=[(10, 4)]),  # escalonamento: 10 elegíveis, 4 escalados
        ]
    )
    resultado = asyncio.run(indicadores_juridicos(session, usuario_teste(), dias=90))
    assert resultado["periodo_dias"] == 90
    assert resultado["taxa_cumprimento"] == {
        "concluidos_no_prazo": 7,
        "concluidos_atrasados": 3,
        "total_concluidos": 10,
        "percentual_no_prazo": 70.0,
    }
    assert resultado["tempo_medio_confirmacao_horas"] == 2.0
    assert resultado["carga_por_responsavel"] == [
        {"responsavel_id": 2, "responsavel_nome": "Ana Responsável", "ativos": 5, "atrasados": 1},
        {"responsavel_id": 3, "responsavel_nome": "Bruno Responsável", "ativos": 2, "atrasados": 0},
    ]
    assert resultado["taxa_escalonamento"] == {"elegiveis": 10, "escalonados": 4, "percentual": 40.0}


def test_indicadores_sem_dados_devolve_percentuais_nulos_em_vez_de_dividir_por_zero() -> None:
    session = FakeSession(
        [
            FakeResult(itens=[(0, 0)]),
            FakeResult(scalar=None),
            FakeResult(itens=[]),
            FakeResult(itens=[(0, 0)]),
        ]
    )
    resultado = asyncio.run(indicadores_juridicos(session, usuario_teste(), dias=90))
    assert resultado["taxa_cumprimento"]["percentual_no_prazo"] is None
    assert resultado["tempo_medio_confirmacao_horas"] is None
    assert resultado["carga_por_responsavel"] == []
    assert resultado["taxa_escalonamento"]["percentual"] is None
