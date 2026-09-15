import asyncio
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.api.leads_propostas import (
    PropostaStatusInput,
    _atualizar_sla_proposta,
    _prazo_sla_24h,
    _resumir_pesquisas_proposta,
    _tentar_vincular_processo_ao_protocolar,
    aceitar_proposta_publica,
    atualizar_pagamento_proposta,
    atualizar_status_proposta,
    calcular_pagamento_status_proposta,
    confirmar_codigo_proposta,
    criar_contratacao_automatica_proposta,
    criar_nova_versao_proposta,
    sincronizar_pagamento_proposta,
)
from app.auth import hash_token
from app.models import (
    AssinaturaPropostaComercial,
    ContratacaoServico,
    DocumentoLead,
    EventoDominio,
    LancamentoFinanceiro,
    Lead,
    Organizacao,
    ParcelaFinanceira,
    PesquisaMarca,
    Processo,
    ProcessoMonitorado,
    PropostaComercial,
    StatusLead,
    TipoProcesso,
)
from tests.conftest import FakeResult, FakeSession, usuario_teste


def _proposta(**kwargs: object) -> PropostaComercial:
    base: dict = {
        "organizacao_id": 1,
        "lead_id": 1,
        "numero": "PROP-TEST",
        "escopo": "Registro de marca no INPI",
    }
    base.update(kwargs)
    return PropostaComercial(**base)


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


def test_proposta_consolida_varias_marcas_e_classes() -> None:
    pesquisas = [
        PesquisaMarca(marca="NORTE STUDIO", classe_nice="25"),
        PesquisaMarca(marca="NORTE STUDIO", classe_nice="35"),
        PesquisaMarca(marca="NORTE CAFÉ", classe_nice="30"),
    ]

    marcas, classes = _resumir_pesquisas_proposta(pesquisas)

    assert marcas == "NORTE STUDIO; NORTE CAFÉ"
    assert classes == "NORTE STUDIO: NCL 25, 35; NORTE CAFÉ: NCL 30"


def test_proposta_de_uma_marca_preserva_formato_simples() -> None:
    marcas, classes = _resumir_pesquisas_proposta(
        [PesquisaMarca(marca="NORTE STUDIO", classe_nice="25")]
    )

    assert marcas == "NORTE STUDIO"
    assert classes == "25"


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


def _lead_com_email(**kwargs: object) -> Lead:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "nome": "Cliente",
        "email": "cliente@example.com",
        "telefone": "",
        "marca": "NORTE",
        "status": StatusLead.PROPOSTA_ENVIADA,
    }
    base.update(kwargs)
    return Lead(**base)


def test_aceitar_proposta_publica_dentro_da_validade_envia_codigo_por_email() -> None:
    """Achado da orientação jurídica (15/09/2026): o clique em "Aceitar"
    não finaliza mais o aceite direto -- gera e envia um código de 6
    dígitos como segundo fator, e só isso."""
    import app.api.leads_propostas as leads_modulo

    codigos_enviados: list[tuple] = []

    async def _enviar_fake(destinatario: str, nome: str, codigo: str, numero: str) -> None:
        codigos_enviados.append((destinatario, nome, codigo, numero))

    original = leads_modulo.enviar_codigo_confirmacao_proposta
    leads_modulo.enviar_codigo_confirmacao_proposta = _enviar_fake
    try:
        proposta = _proposta_com_token(validade_em=date(2099, 12, 31))
        lead = _lead_com_email()
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=lead)])
        resposta = asyncio.run(
            leads_modulo.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
        )
    finally:
        leads_modulo.enviar_codigo_confirmacao_proposta = original

    assert resposta.status_code == 200
    assert proposta.status == "enviada"
    assert proposta.public_aceito_em is None
    assert proposta.codigo_confirmacao_hash is not None
    assert proposta.codigo_confirmacao_expira_em is not None
    assert len(codigos_enviados) == 1
    assert codigos_enviados[0][0] == "cliente@example.com"
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert assinaturas == []


def test_confirmar_codigo_proposta_com_codigo_certo_finaliza_o_aceite() -> None:
    import app.api.leads_propostas as leads_modulo

    async def _avancar_fake(*_args: object, **_kwargs: object) -> bool:
        return True

    original = leads_modulo.avancar_fase_lead
    leads_modulo.avancar_fase_lead = _avancar_fake
    try:
        proposta = _proposta_com_token(
            validade_em=date(2099, 12, 31),
            codigo_confirmacao_hash=hash_token("123456"),
            codigo_confirmacao_expira_em=datetime.now(UTC) + timedelta(minutes=10),
            codigo_confirmacao_tentativas=0,
        )
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
        resposta = asyncio.run(
            leads_modulo.confirmar_codigo_proposta(
                "token-qualquer", _request_post("/propostas/x/confirmar"), session, "123456"
            )
        )
    finally:
        leads_modulo.avancar_fase_lead = original

    assert resposta.status_code == 200
    assert proposta.status == "aceita"
    assert proposta.public_aceito_em is not None
    assert proposta.codigo_confirmacao_hash is None
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert len(assinaturas) == 1
    assert assinaturas[0].segundo_fator_canal == "email"
    assert assinaturas[0].segundo_fator_confirmado_em is not None


def test_confirmar_codigo_proposta_com_codigo_errado_incrementa_tentativas() -> None:
    proposta = _proposta_com_token(
        validade_em=date(2099, 12, 31),
        codigo_confirmacao_hash=hash_token("123456"),
        codigo_confirmacao_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        codigo_confirmacao_tentativas=0,
    )
    session = FakeSession([FakeResult(scalar=proposta)])

    resposta = asyncio.run(
        confirmar_codigo_proposta("token-qualquer", _request_post("/propostas/x/confirmar"), session, "000000")
    )

    assert resposta.status_code == 200
    assert "incorreto" in resposta.body.decode("utf-8").lower()
    assert proposta.status != "aceita"
    assert proposta.codigo_confirmacao_tentativas == 1
    assert session.commits == 1


def test_confirmar_codigo_proposta_expirado_e_rejeitado() -> None:
    proposta = _proposta_com_token(
        validade_em=date(2099, 12, 31),
        codigo_confirmacao_hash=hash_token("123456"),
        codigo_confirmacao_expira_em=datetime.now(UTC) - timedelta(minutes=1),
        codigo_confirmacao_tentativas=0,
    )
    session = FakeSession([FakeResult(scalar=proposta)])

    resposta = asyncio.run(
        confirmar_codigo_proposta("token-qualquer", _request_post("/propostas/x/confirmar"), session, "123456")
    )

    assert resposta.status_code == 200
    assert "expirado" in resposta.body.decode("utf-8").lower()
    assert proposta.status != "aceita"


def test_confirmar_codigo_proposta_bloqueia_apos_maximo_de_tentativas() -> None:
    proposta = _proposta_com_token(
        validade_em=date(2099, 12, 31),
        codigo_confirmacao_hash=hash_token("123456"),
        codigo_confirmacao_expira_em=datetime.now(UTC) + timedelta(minutes=10),
        codigo_confirmacao_tentativas=5,
    )
    session = FakeSession([FakeResult(scalar=proposta)])

    resposta = asyncio.run(
        confirmar_codigo_proposta("token-qualquer", _request_post("/propostas/x/confirmar"), session, "123456")
    )

    assert resposta.status_code == 200
    assert "tentativas" in resposta.body.decode("utf-8").lower()
    assert proposta.status != "aceita"


def test_visualizar_proposta_publica_exibe_valores_condicoes_e_validade() -> None:
    import app.api.leads_propostas as leads_modulo

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
    import app.api.leads_propostas as leads_modulo

    async def _enviar_fake(*_args: object, **_kwargs: object) -> None:
        return None

    original = leads_modulo.enviar_codigo_confirmacao_proposta
    leads_modulo.enviar_codigo_confirmacao_proposta = _enviar_fake
    try:
        proposta = _proposta_com_token(validade_em=None)
        lead = _lead_com_email()
        session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=lead)])
        resposta = asyncio.run(
            leads_modulo.aceitar_proposta_publica("token-qualquer", _request_post("/propostas/x/aceitar"), session)
        )
    finally:
        leads_modulo.enviar_codigo_confirmacao_proposta = original

    assert resposta.status_code == 200
    assert proposta.codigo_confirmacao_hash is not None


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


# --- Fase 4 do plano proposta-financeiro (03/09/2026): contratação automática no aceite ---


def test_criar_contratacao_automatica_proposta_cria_lancamento_parcela_e_contratacao() -> None:
    proposta = _proposta(id=1, honorarios=1500, taxa_gru=355)
    session = FakeSession([FakeResult(scalar=None)])
    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "link_publico"))
    lancamentos = [obj for obj in session.adicionados if isinstance(obj, LancamentoFinanceiro)]
    parcelas = [obj for obj in session.adicionados if isinstance(obj, ParcelaFinanceira)]
    contratacoes = [obj for obj in session.adicionados if isinstance(obj, ContratacaoServico)]
    assert len(lancamentos) == 1
    assert lancamentos[0].valor_total == 1855
    assert lancamentos[0].proposta_id == 1
    assert lancamentos[0].idempotency_key == "proposta-aceite:1"
    assert len(parcelas) == 1
    assert parcelas[0].valor == 1855
    assert len(contratacoes) == 1
    assert contratacoes[0].servico_id is None
    assert contratacoes[0].proposta_id == 1


def test_criar_contratacao_automatica_proposta_e_idempotente() -> None:
    proposta = _proposta(id=1, honorarios=1500, taxa_gru=355)
    session = FakeSession([FakeResult(scalar=99)])
    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "link_publico"))
    assert session.adicionados == []


def test_criar_contratacao_automatica_proposta_sem_valor_nao_cria_lancamento() -> None:
    proposta = _proposta(id=1, honorarios=0, taxa_gru=0)
    session = FakeSession([])
    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "link_publico"))
    assert [obj for obj in session.adicionados if isinstance(obj, LancamentoFinanceiro)] == []
    assert [obj for obj in session.adicionados if isinstance(obj, ParcelaFinanceira)] == []
    assert [obj for obj in session.adicionados if isinstance(obj, ContratacaoServico)] == []


def test_criar_contratacao_automatica_proposta_sem_valor_deixa_rastro_de_auditoria() -> None:
    """Achado médio da auditoria financeira (15/09/2026): antes disso, uma
    proposta aceita com valor zerado não deixava nenhum rastro -- não dava
    pra diferenciar depois um serviço legítimo gratuito de um erro de
    preenchimento."""
    proposta = _proposta(id=1, honorarios=0, taxa_gru=0)
    session = FakeSession([])
    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "link_publico"))
    eventos = [
        obj
        for obj in session.adicionados
        if isinstance(obj, EventoDominio) and obj.tipo == "financeiro.proposta_sem_valor"
    ]
    assert len(eventos) == 1
    assert eventos[0].payload["proposta_id"] == 1


def test_criar_contratacao_automatica_proposta_absorve_conflito_concorrente() -> None:
    """Achado alto da auditoria financeira (15/09/2026): o SELECT de
    "existente" e o INSERT não são atômicos -- duas aceitações quase
    simultâneas da mesma proposta passam as duas pelo "existente is None".
    A segunda deve absorver o IntegrityError (proteção de última linha é a
    UniqueConstraint em contratacoes_servicos.proposta_id) em vez de deixar
    a exceção derrubar a requisição com 500."""
    proposta = _proposta(id=1, honorarios=1500, taxa_gru=355)
    session = FakeSession([FakeResult(scalar=None)])

    chamadas_flush = {"n": 0}
    flush_original = session.flush

    async def _flush_com_conflito_na_segunda_chamada() -> None:
        chamadas_flush["n"] += 1
        if chamadas_flush["n"] == 1:
            await flush_original()
            return
        raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))

    session.flush = _flush_com_conflito_na_segunda_chamada

    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "link_publico"))

    # Nada persistido: a savepoint (begin_nested) descarta o lançamento, a
    # parcela e a contratação junto com o evento operacional.
    assert session.adicionados == []


def test_aceite_encaminha_oportunidade_ao_financeiro() -> None:
    proposta = _proposta(id=1, status="aceita", honorarios=1500, taxa_gru=355)
    lead = Lead(
        id=1,
        organizacao_id=1,
        nome="Cliente",
        email="cliente@example.com",
        telefone="",
        marca="NORTE",
        fase="proposta_enviada",
        status=StatusLead.PROPOSTA_ENVIADA,
    )
    session = FakeSession([FakeResult(scalar=None), FakeResult(scalar=lead)])

    asyncio.run(criar_contratacao_automatica_proposta(session, proposta, "portal"))

    assert lead.fase == "aguardando_pagamento"
    eventos_financeiros = [
        item
        for item in session.adicionados
        if isinstance(item, EventoDominio) and item.tipo == "financeiro.proposta_recebida"
    ]
    assert len(eventos_financeiros) == 1


def test_pagamento_confirmado_encaminha_oportunidade_ao_juridico() -> None:
    proposta = _proposta(id=10, status="aceita", pagamento_status="pendente")
    lead = Lead(
        id=1,
        organizacao_id=1,
        nome="Cliente",
        email="cliente@example.com",
        telefone="",
        marca="NORTE",
        fase="aguardando_pagamento",
        status=StatusLead.PROPOSTA_ENVIADA,
    )
    documento = DocumentoLead(
        id=1, lead_id=1, organizacao_id=1, tipo="procuracao", status="validado", obrigatorio=True
    )
    session = FakeSession(
        [
            FakeResult(itens=["pago"]),
            FakeResult(itens=[documento]),
            FakeResult(scalar=lead),
        ]
    )

    asyncio.run(sincronizar_pagamento_proposta(session, proposta))

    assert lead.fase == "pagamento_confirmado"
    assert any(
        isinstance(item, EventoDominio) and item.tipo == "juridico.encaminhamento_disponivel"
        for item in session.adicionados
    )


def test_confirmar_codigo_proposta_gera_contratacao_automatica() -> None:
    import app.api.leads_propostas as leads_modulo

    async def _avancar_fake(*_args: object, **_kwargs: object) -> bool:
        return True

    original = leads_modulo.avancar_fase_lead
    leads_modulo.avancar_fase_lead = _avancar_fake
    try:
        proposta = _proposta_com_token(
            validade_em=None,
            codigo_confirmacao_hash=hash_token("123456"),
            codigo_confirmacao_expira_em=datetime.now(UTC) + timedelta(minutes=10),
            codigo_confirmacao_tentativas=0,
        )
        session = FakeSession(
            [
                FakeResult(scalar=proposta),
                FakeResult(scalar=None),
                FakeResult(scalar=None),
            ]
        )
        asyncio.run(
            leads_modulo.confirmar_codigo_proposta(
                "token-qualquer", _request_post("/propostas/x/confirmar"), session, "123456"
            )
        )
    finally:
        leads_modulo.avancar_fase_lead = original

    contratacoes = [obj for obj in session.adicionados if isinstance(obj, ContratacaoServico)]
    assert len(contratacoes) == 1


def test_atualizar_status_proposta_aceita_gera_contratacao_automatica() -> None:
    proposta = _proposta(id=1, status="enviada", honorarios=1500, taxa_gru=355)
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None), FakeResult(scalar=None)])
    asyncio.run(
        atualizar_status_proposta(
            1, PropostaStatusInput(status="aceita"), _request_patch("/propostas/1/status"), session, usuario_teste()
        )
    )
    contratacoes = [obj for obj in session.adicionados if isinstance(obj, ContratacaoServico)]
    assert len(contratacoes) == 1


# --- Fase 5 do plano proposta-financeiro (03/09/2026): numeração de versão consistente ---


def test_criar_nova_versao_proposta_mantem_o_numero_base() -> None:
    anterior = _proposta(id=1, numero="PROP-2026-000010", versao=1)
    session = FakeSession([FakeResult(scalar=anterior)])
    resultado = asyncio.run(
        criar_nova_versao_proposta(1, _request_post("/propostas/1/nova-versao"), session, usuario_teste())
    )
    assert resultado["numero"] == "PROP-2026-000010"
    assert resultado["versao"] == 2


def test_criar_nova_versao_proposta_incrementa_a_partir_de_versao_ja_avancada() -> None:
    anterior = _proposta(id=3, numero="PROP-2026-000010", versao=3)
    session = FakeSession([FakeResult(scalar=anterior)])
    resultado = asyncio.run(
        criar_nova_versao_proposta(3, _request_post("/propostas/3/nova-versao"), session, usuario_teste())
    )
    assert resultado["numero"] == "PROP-2026-000010"
    assert resultado["versao"] == 4


# --- Fase 7 do plano proposta-financeiro (03/09/2026): SLA sobre dado financeiro real ---


def test_sla_nao_libera_quando_proposta_aceita_mas_sem_lancamento_pago() -> None:
    """Fecha explicitamente a vulnerabilidade original: antes bastava um
    PATCH manual em pagamento_status="confirmado" para liberar o SLA/
    protocolo, sem nenhum dinheiro real por trás. Agora, mesmo com a
    proposta "aceita", sem lançamento pago o SLA nunca sai de
    "aguardando_pagamento" -- calcular_pagamento_status_proposta só retorna
    "confirmado" quando há um LancamentoFinanceiro efetivamente "pago"."""
    proposta = _proposta(id=10, status="aceita", pagamento_status="pendente")
    session = FakeSession([FakeResult(itens=[])])  # nenhum lançamento vinculado
    asyncio.run(sincronizar_pagamento_proposta(session, proposta))
    assert proposta.pagamento_status == "pendente"
    assert proposta.sla_inicio_em is None
    assert _atualizar_sla_proposta(proposta) == "aguardando_pagamento"


# --- Achado "Ruptura 1" da auditoria completa do CRM (06/09/2026): o número
# do protocolo digitado ao registrar a proposta era o mesmo que precisava
# ser digitado de novo depois na carteira, só para vincular o processo ao
# lead. ---


def _processo(**kwargs: object) -> Processo:
    base: dict = {
        "id": 100,
        "numero": "935977333",
        "numero_normalizado": "935977333",
        "tipo": TipoProcesso.MARCA,
        "fonte": "RPI 2901",
    }
    base.update(kwargs)
    return Processo(**base)


def test_tentar_vincular_processo_processo_nao_publicado_nao_faz_nada() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    asyncio.run(_tentar_vincular_processo_ao_protocolar(session, 1, 7, "935977333"))

    assert session.adicionados == []
    assert session.commits == 0


def test_tentar_vincular_processo_cria_processo_monitorado_quando_processo_ja_existe() -> None:
    processo = _processo()
    session = FakeSession([FakeResult(scalar=processo), FakeResult(scalar=None)])

    asyncio.run(_tentar_vincular_processo_ao_protocolar(session, 1, 7, "935977333"))

    assert len(session.adicionados) == 1
    monitorado = session.adicionados[0]
    assert isinstance(monitorado, ProcessoMonitorado)
    assert monitorado.processo_id == 100
    assert monitorado.lead_id == 7
    assert monitorado.organizacao_id == 1


def test_tentar_vincular_processo_preenche_lead_id_quando_ja_monitorado_sem_lead() -> None:
    processo = _processo()
    monitorado_existente = ProcessoMonitorado(
        id=55, organizacao_id=1, processo_id=100, lead_id=None, vinculado_por="outro fluxo"
    )
    session = FakeSession([FakeResult(scalar=processo), FakeResult(scalar=monitorado_existente)])

    asyncio.run(_tentar_vincular_processo_ao_protocolar(session, 1, 7, "935977333"))

    assert session.adicionados == []
    assert monitorado_existente.lead_id == 7


def test_tentar_vincular_processo_nao_sobrescreve_lead_ja_vinculado_a_outro() -> None:
    processo = _processo()
    monitorado_existente = ProcessoMonitorado(
        id=55, organizacao_id=1, processo_id=100, lead_id=99, vinculado_por="outro fluxo"
    )
    session = FakeSession([FakeResult(scalar=processo), FakeResult(scalar=monitorado_existente)])

    asyncio.run(_tentar_vincular_processo_ao_protocolar(session, 1, 7, "935977333"))

    assert monitorado_existente.lead_id == 99
