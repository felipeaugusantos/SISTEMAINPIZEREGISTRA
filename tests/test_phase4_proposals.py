import asyncio
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from starlette.requests import Request

from app.api.leads import (
    PropostaStatusInput,
    _atualizar_sla_proposta,
    _prazo_sla_24h,
    aceitar_proposta_publica,
    atualizar_pagamento_proposta,
    atualizar_status_proposta,
    calcular_pagamento_status_proposta,
    sincronizar_pagamento_proposta,
)
from app.models import AssinaturaPropostaComercial, DocumentoLead, Organizacao, PropostaComercial
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _proposta(**kwargs: object) -> PropostaComercial:
    proposta = PropostaComercial(
        organizacao_id=1,
        lead_id=1,
        numero="PROP-TEST",
        escopo="Registro de marca no INPI",
        **kwargs,
    )
    return proposta


def _request_post(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_prazo_sla_e_24_horas() -> None:
    inicio = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    assert _prazo_sla_24h(inicio) == inicio + timedelta(hours=24)


def test_sla_aguarda_pagamento_apos_aceite() -> None:
    proposta = _proposta(status="aceita")
    assert _atualizar_sla_proposta(proposta) == "aguardando_pagamento"


def test_sla_em_prazo_apos_pagamento() -> None:
    inicio = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    proposta = _proposta(
        status="aceita",
        pagamento_status="confirmado",
        sla_inicio_em=inicio,
        sla_prazo_em=_prazo_sla_24h(inicio),
    )
    assert _atualizar_sla_proposta(proposta, inicio + timedelta(hours=1)) == "em_prazo"


def test_sla_vencido_sem_protocolo() -> None:
    inicio = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    proposta = _proposta(
        status="aceita",
        pagamento_status="confirmado",
        sla_inicio_em=inicio,
        sla_prazo_em=_prazo_sla_24h(inicio),
    )
    assert _atualizar_sla_proposta(proposta, inicio + timedelta(hours=25)) == "vencido"


def test_protocolo_concluido_tem_precedencia() -> None:
    proposta = _proposta(status="aceita", protocolo_em=datetime.now(UTC))
    assert _atualizar_sla_proposta(proposta) == "protocolado"


def test_proposta_pode_preservar_a_pesquisa_de_origem() -> None:
    proposta = _proposta(pesquisa_id="12345678-1234-1234-1234-123456789abc")
    assert proposta.pesquisa_id == "12345678-1234-1234-1234-123456789abc"


# --- Fase 1 do plano proposta-financeiro (03/09/2026): blindar o aceite ---


def _proposta_com_token(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "id": 1,
        "status": "enviada",
        "public_token_hash": "hash-qualquer",
        "public_token_expira_em": datetime.now(UTC) + timedelta(days=1),
        "honorarios": 1500,
        "taxa_gru": 355,
        "condicoes_pagamento": "À vista",
    }
    base.update(kwargs)
    return _proposta(**base)


def test_aceitar_proposta_publica_rejeita_quando_validade_expirou() -> None:
    proposta = _proposta_com_token(validade_em=date(2020, 1, 1))
    session = FakeSession([FakeResult(scalar=proposta)])
    resposta = asyncio.run(aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session))
    assert resposta.status_code == 409
    assert proposta.status == "enviada"
    assert proposta.public_aceito_em is None
    assert session.adicionados == []


def test_aceitar_proposta_publica_ja_aceita_continua_idempotente_mesmo_apos_expirar() -> None:
    ja_aceita_em = datetime(2020, 1, 2, tzinfo=UTC)
    proposta = _proposta_com_token(
        validade_em=date(2020, 1, 1),
        status="aceita",
        public_aceito_em=ja_aceita_em,
        aceito_em=ja_aceita_em,
    )
    session = FakeSession([FakeResult(scalar=proposta)])
    resposta = asyncio.run(aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session))
    assert resposta.status_code == 200
    assert proposta.public_aceito_em == ja_aceita_em


def test_aceitar_proposta_publica_dentro_da_validade_prossegue_com_o_aceite() -> None:
    import app.api.leads as leads_modulo

    async def _avancar_fake(*_args: object, **_kwargs: object) -> bool:
        return True

    original = leads_modulo.avancar_fase_lead
    leads_modulo.avancar_fase_lead = _avancar_fake
    try:
        proposta = _proposta_com_token(validade_em=date(2099, 12, 31))
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
        resposta = asyncio.run(
            leads_modulo.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
        )
    finally:
        leads_modulo.avancar_fase_lead = original

    assert resposta.status_code == 200
    assert proposta.status == "aceita"
    assert proposta.public_aceito_em is not None
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert len(assinaturas) == 1


def test_visualizar_proposta_publica_exibe_valores_condicoes_e_validade() -> None:
    import app.api.leads as leads_modulo

    proposta = _proposta_com_token(marca="ACME", classes="35", validade_em=date(2099, 12, 31))
    session = FakeSession([FakeResult(scalar=proposta)])

    async def _get_fake(*_args: object, **_kwargs: object) -> Organizacao:
        return Organizacao(id=1, nome="Zé Registra", slug="ze-registra")

    session.get = _get_fake
    resultado = asyncio.run(leads_modulo.visualizar_proposta_publica("token-qualquer", session))
    corpo = resultado.body.decode("utf-8")
    assert "1.500,00" in corpo
    assert "355,00" in corpo
    assert "1.855,00" in corpo
    assert "À vista" in corpo
    assert "31/12/2099" in corpo


def test_aceitar_proposta_publica_sem_validade_definida_nao_e_bloqueada() -> None:
    import app.api.leads as leads_modulo

    async def _avancar_fake(*_args: object, **_kwargs: object) -> bool:
        return True

    original = leads_modulo.avancar_fase_lead
    leads_modulo.avancar_fase_lead = _avancar_fake
    try:
        proposta = _proposta_com_token(validade_em=None)
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
        resposta = asyncio.run(
            leads_modulo.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
        )
    finally:
        leads_modulo.avancar_fase_lead = original

    assert resposta.status_code == 200
    assert proposta.status == "aceita"


def _request_patch(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


# --- Fase 2 do plano proposta-financeiro (03/09/2026): máquina de estados ---


def test_atualizar_status_proposta_bloqueia_transicao_invalida() -> None:
    proposta = _proposta(id=1, status="aceita")
    session = FakeSession([FakeResult(scalar=proposta)])
    try:
        asyncio.run(
            atualizar_status_proposta(
                1,
                PropostaStatusInput(status="rascunho"),
                _request_patch("/propostas/1/status"),
                session,
                usuario_teste(),
            )
        )
        raise AssertionError("Esperava HTTPException 422 para transição aceita→rascunho")
    except HTTPException as erro:
        assert erro.status_code == 422
    assert proposta.status == "aceita"


def test_atualizar_status_proposta_estado_terminal_e_final() -> None:
    proposta = _proposta(id=1, status="cancelada")
    session = FakeSession([FakeResult(scalar=proposta)])
    try:
        asyncio.run(
            atualizar_status_proposta(
                1,
                PropostaStatusInput(status="enviada"),
                _request_patch("/propostas/1/status"),
                session,
                usuario_teste(),
            )
        )
        raise AssertionError("Esperava HTTPException 422 — cancelada é estado terminal")
    except HTTPException as erro:
        assert erro.status_code == 422


def test_atualizar_status_proposta_permite_manter_o_mesmo_status() -> None:
    proposta = _proposta(id=1, status="aceita")
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
    resultado = asyncio.run(
        atualizar_status_proposta(
            1, PropostaStatusInput(status="aceita"), _request_patch("/propostas/1/status"), session, usuario_teste()
        )
    )
    assert resultado["status"] == "aceita"


def test_atualizar_status_proposta_transicao_valida_prossegue() -> None:
    proposta = _proposta(id=1, status="rascunho")
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
    resultado = asyncio.run(
        atualizar_status_proposta(
            1, PropostaStatusInput(status="enviada"), _request_patch("/propostas/1/status"), session, usuario_teste()
        )
    )
    assert resultado["status"] == "enviada"
    assert proposta.enviado_em is not None


def test_atualizar_status_proposta_cancelamento_exige_motivo() -> None:
    proposta = _proposta(id=1, status="enviada")
    session = FakeSession([FakeResult(scalar=proposta)])
    try:
        asyncio.run(
            atualizar_status_proposta(
                1,
                PropostaStatusInput(status="cancelada"),
                _request_patch("/propostas/1/status"),
                session,
                usuario_teste(),
            )
        )
        raise AssertionError("Esperava HTTPException 422 por falta de motivo")
    except HTTPException as erro:
        assert erro.status_code == 422
    assert proposta.status == "enviada"


def test_atualizar_status_proposta_cancelamento_com_motivo_registra_no_historico() -> None:
    proposta = _proposta(id=1, status="enviada")
    session = FakeSession([FakeResult(scalar=proposta)])
    resultado = asyncio.run(
        atualizar_status_proposta(
            1,
            PropostaStatusInput(status="cancelada", motivo="Cliente desistiu do registro."),
            _request_patch("/propostas/1/status"),
            session,
            usuario_teste(),
        )
    )
    assert resultado["status"] == "cancelada"
    assert proposta.dados["cancelamento"]["motivo"] == "Cliente desistiu do registro."


# --- Fase 3 do plano proposta-financeiro (03/09/2026): financeiro como fonte de verdade ---


def test_calcular_pagamento_status_proposta_sem_lancamento_e_pendente() -> None:
    session = FakeSession([FakeResult(itens=[])])
    resultado = asyncio.run(calcular_pagamento_status_proposta(session, 1, 10))
    assert resultado == "pendente"


def test_calcular_pagamento_status_proposta_todos_pagos_e_confirmado() -> None:
    session = FakeSession([FakeResult(itens=["pago", "pago"])])
    resultado = asyncio.run(calcular_pagamento_status_proposta(session, 1, 10))
    assert resultado == "confirmado"


def test_calcular_pagamento_status_proposta_pago_e_aberto_e_parcial() -> None:
    session = FakeSession([FakeResult(itens=["pago", "aberto"])])
    resultado = asyncio.run(calcular_pagamento_status_proposta(session, 1, 10))
    assert resultado == "parcial"


def test_calcular_pagamento_status_proposta_so_cancelados_e_cancelado() -> None:
    session = FakeSession([FakeResult(itens=["cancelado"])])
    resultado = asyncio.run(calcular_pagamento_status_proposta(session, 1, 10))
    assert resultado == "cancelado"


def test_calcular_pagamento_status_proposta_ignora_cancelados_e_considera_ativos() -> None:
    session = FakeSession([FakeResult(itens=["cancelado", "aberto"])])
    resultado = asyncio.run(calcular_pagamento_status_proposta(session, 1, 10))
    assert resultado == "pendente"


def test_sincronizar_pagamento_proposta_sem_mudanca_nao_altera_nada() -> None:
    proposta = _proposta(id=10, status="rascunho", pagamento_status="pendente")
    session = FakeSession([FakeResult(itens=[])])
    asyncio.run(sincronizar_pagamento_proposta(session, proposta))
    assert proposta.pagamento_status == "pendente"


def test_sincronizar_pagamento_proposta_confirma_e_libera_sla_quando_aceita_e_docs_ok() -> None:
    proposta = _proposta(id=10, status="aceita", pagamento_status="pendente")
    documento = DocumentoLead(
        id=1, lead_id=1, organizacao_id=1, tipo="procuracao", status="validado", obrigatorio=True
    )
    session = FakeSession(
        [
            FakeResult(itens=["pago"]),
            FakeResult(itens=[documento]),
        ]
    )
    asyncio.run(sincronizar_pagamento_proposta(session, proposta))
    assert proposta.pagamento_status == "confirmado"
    assert proposta.sla_inicio_em is not None
    assert proposta.sla_status == "em_prazo"


def test_sincronizar_pagamento_proposta_limpa_campos_de_confirmacao_ao_deixar_de_ser_confirmado() -> None:
    proposta = _proposta(
        id=10,
        status="aceita",
        pagamento_status="confirmado",
        pagamento_confirmado_por_id=5,
        pagamento_confirmado_por="Alguém",
        pagamento_confirmado_ip_hash="hash",
    )
    session = FakeSession(
        [
            FakeResult(itens=[]),
            FakeResult(itens=[]),
        ]
    )
    asyncio.run(sincronizar_pagamento_proposta(session, proposta))
    assert proposta.pagamento_status == "pendente"
    assert proposta.pagamento_confirmado_por_id is None
    assert proposta.pagamento_confirmado_por is None
    assert proposta.pagamento_confirmado_ip_hash is None


def test_atualizar_pagamento_proposta_recalcula_a_partir_do_financeiro() -> None:
    proposta = _proposta(id=1, status="aceita", pagamento_status="pendente")
    documento = DocumentoLead(
        id=1, lead_id=1, organizacao_id=1, tipo="procuracao", status="validado", obrigatorio=True
    )
    session = FakeSession(
        [
            FakeResult(scalar=proposta),
            FakeResult(itens=["pago"]),
            FakeResult(itens=[documento]),
        ]
    )
    resultado = asyncio.run(
        atualizar_pagamento_proposta(1, _request_patch("/propostas/1/pagamento"), session, usuario_teste())
    )
    assert resultado["pagamento_status"] == "confirmado"
