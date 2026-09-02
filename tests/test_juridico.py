import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from app.api.juridico import (
    CHECKLIST_GENERICO,
    CHECKLIST_PADRAO,
    MARCO_DEFERIMENTO_ISENTO_TAXA_CONCESSAO,
    PADRAO_PRAZO,
    TIPOS_PRAZO,
    ChecklistItemUpdate,
    PrazoInput,
    _classificar_despacho,
    _classificar_despacho_terminal,
    _dispensa_concessao,
    _pascoa,
    _reconciliar_prazos_historicos,
    _reconciliar_prazos_terminais,
    _serializar_prazo,
    atualizar_item_checklist,
    calcular_vencimento,
    criar_prazo,
    painel,
)
from app.models import EventoJuridico, Movimentacao, PrazoJuridico, ProcessoMonitorado
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


def test_calcula_prazo_em_dias_corridos() -> None:
    vencimento = calcular_vencimento(date(2026, 8, 11), 10, "corridos")
    assert vencimento.date() == date(2026, 8, 21)


def test_agenda_centralizada_cobre_eventos_de_propriedade_intelectual() -> None:
    assert {"publicacao_rpi", "deferimento", "concessao", "decenio", "vencimento_interno"}.issubset(TIPOS_PRAZO)


def test_calcula_prazo_em_dias_uteis_sem_contar_fim_de_semana() -> None:
    vencimento = calcular_vencimento(date(2026, 8, 14), 2, "uteis")
    assert vencimento.date() == date(2026, 8, 18)


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
    assert vencimento.date() == date(2026, 8, 24)  # segunda-feira seguinte


def test_vencimento_corrido_prorroga_quando_cai_em_feriado_nacional() -> None:
    # 09/07/2026 + 60 dias corridos = 07/09/2026 (Independência, 2ª-feira).
    vencimento = calcular_vencimento(date(2026, 7, 9), 60, "corridos")
    assert vencimento.date() == date(2026, 9, 8)  # 1º dia útil seguinte (terça)


def test_pascoa_calcula_data_correta_para_ano_conhecido() -> None:
    # Domingo de Páscoa de 2026 é 05/04/2026 (data pública/oficialmente
    # conhecida) — verifica o algoritmo do calendário móvel usado para
    # calcular a Sexta-feira Santa.
    assert _pascoa(2026) == date(2026, 4, 5)


def test_vencimento_corrido_prorroga_quando_cai_em_feriado_movel() -> None:
    # 02/02/2026 + 60 dias corridos = 03/04/2026, Sexta-feira Santa.
    vencimento = calcular_vencimento(date(2026, 2, 2), 60, "corridos")
    assert vencimento.date() == date(2026, 4, 6)  # pula sáb/dom também


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
            FakeResult(itens=[]),
        ]
    )

    total, terminais = asyncio.run(_reconciliar_prazos_terminais(session, 1, "motor-juridico"))

    assert total == 1
    assert terminais[77] is terminal
    assert prazo.status == "cancelado"
    assert prazo.confirmado is True
    assert prazo.concluido_em is not None
    assert prazo.concluido_por == "motor-juridico"
    evento = next(item for item in session.adicionados if isinstance(item, EventoJuridico))
    assert evento.tipo == "prazo_reconciliado"
    assert evento.detalhes["rpi_terminal"] == 2602


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
    assert prazos[0].vencimento_em.date() == date(2026, 10, 13)
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
    assert "admin-juridico.css?v=14" in html
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
