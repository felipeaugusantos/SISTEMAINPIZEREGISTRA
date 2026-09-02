import asyncio
from datetime import UTC, date, datetime

from fastapi import HTTPException
from starlette.requests import Request

from app.api.portal_cliente import assinar_proposta_portal, listar_prazos_portal
from app.models import ClientePortal, Lead, PrazoJuridico, Processo, PropostaComercial
from tests.conftest import FakeResult, FakeSession


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/portal/prazos",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _cliente() -> ClientePortal:
    return ClientePortal(id=1, organizacao_id=1, lead_id=9, email="cliente@empresa.test", ativo=True)


# --- Achado 5.4 da auditoria (02/09/2026): portal do cliente sem conexão com prazos (Fase 9) ---


def test_listar_prazos_portal_sem_processo_numero_devolve_lista_vazia() -> None:
    lead = Lead(id=9, organizacao_id=1, processo_numero=None)
    session = FakeSession([FakeResult(scalar=lead)])
    resultado = asyncio.run(listar_prazos_portal(_request(), _cliente(), session))
    assert resultado == {"prazos": []}


def test_listar_prazos_portal_sem_processo_monitorado_devolve_lista_vazia() -> None:
    lead = Lead(id=9, organizacao_id=1, processo_numero="BR512345678")
    processo = Processo(id=5, numero="BR512345678", titulo="Marca Exemplo")
    session = FakeSession(
        [
            FakeResult(scalar=lead),
            FakeResult(scalar=processo),
            FakeResult(itens=[]),  # nenhum ProcessoMonitorado para essa organização
        ]
    )
    resultado = asyncio.run(listar_prazos_portal(_request(), _cliente(), session))
    assert resultado == {"prazos": []}


def test_listar_prazos_portal_expoe_apenas_campos_seguros() -> None:
    lead = Lead(id=9, organizacao_id=1, processo_numero="BR512345678")
    processo = Processo(id=5, numero="BR512345678", titulo="Marca Exemplo")
    prazo = PrazoJuridico(
        id=42,
        organizacao_id=1,
        processo_monitorado_id=7,
        titulo="Cumprir exigência formal",
        tipo="exigencia",
        origem="motor_rpi",
        data_base=date(2026, 8, 1),
        dias_prazo=60,
        contagem="corridos",
        vencimento_em=datetime(2026, 9, 30, tzinfo=UTC),
        status="pendente",
        prioridade="alta",
        confirmado=True,
        responsavel_id=3,
        confirmado_por_id=3,
        confirmado_por="Operador Interno",
        confirmacao_observacoes="Nota interna que não deve vazar ao cliente.",
        criado_por="motor-juridico",
    )
    session = FakeSession(
        [
            FakeResult(scalar=lead),
            FakeResult(scalar=processo),
            FakeResult(itens=[7]),
            FakeResult(itens=[prazo]),
        ]
    )
    resultado = asyncio.run(listar_prazos_portal(_request(), _cliente(), session))
    assert resultado["prazos"] == [
        {
            "id": 42,
            "tipo": "exigencia",
            "tipo_descricao": "Cumprimento de exigência",
            "titulo": "Cumprir exigência formal",
            "status": "pendente",
            "prioridade": "alta",
            "vencimento_em": datetime(2026, 9, 30, tzinfo=UTC),
            "concluido_em": None,
        }
    ]
    campos_expostos = set(resultado["prazos"][0])
    assert "responsavel_id" not in campos_expostos
    assert "confirmado_por" not in campos_expostos
    assert "confirmacao_observacoes" not in campos_expostos


# --- Fase 1 do plano proposta-financeiro (03/09/2026): blindar o aceite ---


def _proposta_para_assinatura(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "lead_id": 9,
        "numero": "PROP-TEST",
        "escopo": "Registro de marca no INPI",
        "status": "enviada",
        "honorarios": 1500,
        "taxa_gru": 355,
    }
    base.update(kwargs)
    return PropostaComercial(**base)


def test_assinar_proposta_portal_rejeita_quando_validade_expirou() -> None:
    proposta = _proposta_para_assinatura(validade_em=date(2020, 1, 1))
    session = FakeSession([FakeResult(scalar=proposta)])
    try:
        asyncio.run(assinar_proposta_portal(1, _request(), _cliente(), session))
        raise AssertionError("Esperava HTTPException 409 por proposta expirada")
    except HTTPException as erro:
        assert erro.status_code == 409
    assert proposta.status == "enviada"
    assert proposta.public_aceito_em is None


def test_assinar_proposta_portal_ja_aceita_continua_idempotente_mesmo_apos_expirar() -> None:
    ja_aceita_em = datetime(2020, 1, 2, tzinfo=UTC)
    proposta = _proposta_para_assinatura(
        validade_em=date(2020, 1, 1),
        status="aceita",
        public_aceito_em=ja_aceita_em,
        aceito_em=ja_aceita_em,
    )
    session = FakeSession([FakeResult(scalar=proposta)])
    resultado = asyncio.run(assinar_proposta_portal(1, _request(), _cliente(), session))
    assert resultado["ok"] is True
    assert proposta.public_aceito_em == ja_aceita_em


def test_assinar_proposta_portal_dentro_da_validade_prossegue_com_a_assinatura() -> None:
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31))
    session = FakeSession([FakeResult(scalar=proposta)])
    resultado = asyncio.run(assinar_proposta_portal(1, _request(), _cliente(), session))
    assert resultado["ok"] is True
    assert proposta.status == "aceita"


def test_assinar_proposta_portal_sem_validade_definida_nao_e_bloqueada() -> None:
    proposta = _proposta_para_assinatura(validade_em=None)
    session = FakeSession([FakeResult(scalar=proposta)])
    resultado = asyncio.run(assinar_proposta_portal(1, _request(), _cliente(), session))
    assert resultado["ok"] is True
    assert proposta.status == "aceita"
