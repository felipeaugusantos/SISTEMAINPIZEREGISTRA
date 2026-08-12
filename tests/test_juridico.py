import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from app.api.juridico import (
    CHECKLIST_GENERICO,
    CHECKLIST_PADRAO,
    PADRAO_PRAZO,
    ChecklistItemUpdate,
    PrazoInput,
    _classificar_despacho,
    atualizar_item_checklist,
    calcular_vencimento,
    criar_prazo,
)
from app.models import EventoJuridico, PrazoJuridico, ProcessoMonitorado
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


def test_calcula_prazo_em_dias_uteis_sem_contar_fim_de_semana() -> None:
    vencimento = calcular_vencimento(date(2026, 8, 14), 2, "uteis")
    assert vencimento.date() == date(2026, 8, 18)


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
    dias, tipo, _ = _classificar_despacho(
        "Publicação de pedido de registro para oposição (exame formal concluído)"
    )
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
    assert prazos[0].vencimento_em.date() == date(2026, 10, 10)
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
    assert "/v1/admin/juridico/motor/executar" in javascript
    assert "/admin/operacao-juridica" in shell


def test_checklist_padrao_cobre_todos_os_tipos() -> None:
    for tipo in ("oposicao", "recurso", "exigencia", "pagamento", "manifestacao"):
        assert CHECKLIST_PADRAO[tipo], f"template vazio para {tipo}"
    assert CHECKLIST_GENERICO


def test_checklist_tipo_desconhecido_usa_generico() -> None:
    assert CHECKLIST_PADRAO.get("inexistente", CHECKLIST_GENERICO) is CHECKLIST_GENERICO


def test_marcar_item_registra_autor_e_data() -> None:
    item = SimpleNamespace(
        id=5, descricao="Protocolar no INPI", concluido=False, ordem=1,
        concluido_em=None, concluido_por=None,
    )
    session = FakeSession([FakeResult(scalar=item)])
    usuario = usuario_teste()
    resultado = asyncio.run(
        atualizar_item_checklist(5, ChecklistItemUpdate(concluido=True), session, usuario)
    )
    assert resultado["concluido"] is True
    assert resultado["concluido_por"] == usuario.ator
    assert item.concluido_em is not None


def test_desmarcar_item_limpa_autor_e_data() -> None:
    item = SimpleNamespace(
        id=5, descricao="Protocolar no INPI", concluido=True, ordem=1,
        concluido_em=object(), concluido_por="alguem",
    )
    session = FakeSession([FakeResult(scalar=item)])
    resultado = asyncio.run(
        atualizar_item_checklist(5, ChecklistItemUpdate(concluido=False), session, usuario_teste())
    )
    assert resultado["concluido"] is False
    assert item.concluido_em is None
    assert item.concluido_por is None
