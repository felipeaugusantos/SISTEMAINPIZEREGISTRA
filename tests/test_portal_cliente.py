import asyncio
import json
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.portal_cliente import (
    ClienteLogin,
    assinar_proposta_portal,
    listar_arquivos_portal_admin,
    listar_prazos_portal,
    listar_processos_portal,
    login_cliente,
    logout_cliente,
    montar_jornada_registro,
    progresso_processo,
    webhook_clicksign,
)
from app.models import (
    ArquivoClientePortal,
    AssinaturaPropostaComercial,
    ClientePortal,
    HistoricoFaseLead,
    Lead,
    PrazoJuridico,
    Processo,
    ProcessoMonitorado,
    PropostaComercial,
)
from tests.conftest import FakeResult, FakeSession, usuario_teste


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


def test_listar_prazos_portal_sem_processo_monitorado_devolve_lista_vazia() -> None:
    # Achado "Ruptura 2" da auditoria completa do CRM (06/09/2026): a busca
    # agora é por ProcessoMonitorado.lead_id (FK real), não mais por
    # igualdade de string entre lead.processo_numero e Processo.numero.
    lead = Lead(id=9, organizacao_id=1)
    session = FakeSession(
        [
            FakeResult(scalar=lead),
            FakeResult(itens=[]),  # nenhum ProcessoMonitorado vinculado a este lead
        ]
    )
    resultado = asyncio.run(listar_prazos_portal(_request(), _cliente(), session))
    assert resultado == {"prazos": []}


def test_listar_prazos_portal_expoe_apenas_campos_seguros() -> None:
    lead = Lead(id=9, organizacao_id=1)
    processo = Processo(id=5, numero="BR512345678", titulo="Marca Exemplo")
    monitorado = ProcessoMonitorado(id=7, organizacao_id=1, processo_id=5, lead_id=9, vinculado_por="teste")
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
            FakeResult(itens=[(monitorado, processo)]),
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


def test_listar_processos_portal_mostra_varios_processos_do_mesmo_lead() -> None:
    # Achado "Ruptura 2" da auditoria completa do CRM (06/09/2026): antes só
    # dava para ver 1 processo por lead (lead.processo_numero era um campo
    # de texto solto); agora aparecem todos os ProcessoMonitorado vinculados.
    lead = Lead(id=9, organizacao_id=1)
    processo_a = Processo(id=5, numero="BR512345678", titulo="Marca A", situacao="deferido")
    processo_b = Processo(id=6, numero="BR987654321", titulo="Marca B", situacao="em tramitação")
    monitorado_a = ProcessoMonitorado(id=7, organizacao_id=1, processo_id=5, lead_id=9, vinculado_por="teste")
    monitorado_b = ProcessoMonitorado(id=8, organizacao_id=1, processo_id=6, lead_id=9, vinculado_por="teste")
    session = FakeSession(
        [
            FakeResult(scalar=lead),
            FakeResult(itens=[(monitorado_a, processo_a), (monitorado_b, processo_b)]),
        ]
    )

    resultado = asyncio.run(listar_processos_portal(_request(), _cliente(), session))

    assert [item["numero"] for item in resultado["processos"]] == ["BR512345678", "BR987654321"]


def test_logout_aplica_tenant_antes_de_revogar_sessao(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import portal_cliente as modulo_portal
    from app.models import SessaoClientePortal

    sessao = SessaoClientePortal(id=4, cliente_id=1, token_hash="hash", expira_em=datetime(2099, 1, 1, tzinfo=UTC))
    cliente = _cliente()
    session = FakeSession([FakeResult(scalar=sessao)], objetos_get=[cliente])
    ordem: list[str] = []

    async def aplicar_tenant_sem_autoflush(_session, organizacao_id: int) -> None:
        assert organizacao_id == cliente.organizacao_id
        assert sessao.revogada_em is None
        ordem.append("tenant")

    monkeypatch.setattr(modulo_portal, "aplicar_contexto_tenant", aplicar_tenant_sem_autoflush)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/portal/logout",
            "headers": [(b"cookie", b"zr_client_session=token")],
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )
    response = Response()

    resultado = asyncio.run(logout_cliente(request, response, session))

    assert resultado == {"ok": True}
    assert ordem == ["tenant"]
    assert sessao.revogada_em is not None
    assert session.commits == 1
    assert "zr_client_session=" in response.headers["set-cookie"]


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


# --- Fase 6 do plano proposta-financeiro (03/09/2026): paridade do webhook Clicksign ---


def _webhook_request(corpo: dict) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/webhooks/clicksign",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )

    async def _body() -> bytes:
        return json.dumps(corpo).encode()

    request.body = _body
    return request


def test_webhook_clicksign_registra_assinatura_e_gera_contratacao_no_primeiro_evento() -> None:
    proposta = _proposta_para_assinatura(
        id=7, status="enviada", dados={"clicksign": {"envelope_id": "env-123"}}
    )
    session = FakeSession(
        [
            FakeResult(scalar=proposta),  # busca por envelope_id
            FakeResult(scalar=None),  # contratação existente? não
        ]
    )
    resultado = asyncio.run(
        webhook_clicksign(
            _webhook_request({"envelope_id": "env-123", "event_id": "evt-1", "status": "document_closed"}),
            session,
            None,
        )
    )
    assert resultado == {"ok": True, "proposta_id": 7}
    assert proposta.status == "aceita"
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert len(assinaturas) == 1
    assert assinaturas[0].provedor == "clicksign"


def test_webhook_clicksign_nao_duplica_assinatura_em_segundo_evento_do_mesmo_envelope() -> None:
    proposta = _proposta_para_assinatura(
        id=7,
        status="aceita",
        aceito_em=datetime(2026, 1, 1, tzinfo=UTC),
        dados={"clicksign": {"envelope_id": "env-123", "ultimo_evento_id": "evt-1"}},
    )
    session = FakeSession(
        [
            FakeResult(scalar=proposta),
            FakeResult(scalar=None),
        ]
    )
    asyncio.run(
        webhook_clicksign(
            _webhook_request({"envelope_id": "env-123", "event_id": "evt-2", "status": "envelope_closed"}),
            session,
            None,
        )
    )
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert assinaturas == []


def test_webhook_clicksign_proposta_nao_encontrada_e_ignorado() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    resultado = asyncio.run(
        webhook_clicksign(
            _webhook_request({"envelope_id": "env-inexistente", "status": "signed"}),
            session,
            None,
        )
    )
    assert resultado == {"ok": True, "ignorado": True}


# --- Achado da validação do Portal do Cliente (17/09/2026): login sem limite
# de tentativas -- diferente do login administrativo, permitia força bruta de
# senha contra contas de ClientePortal. ---


def test_login_cliente_portal_bloqueia_apos_muitas_tentativas() -> None:
    dados = ClienteLogin(email="cliente@empresa.com.br", senha="senha-errada")
    request = _request()
    for _ in range(10):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(login_cliente(dados, request, Response(), FakeSession([])))
        assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(login_cliente(dados, request, Response(), FakeSession([])))
    assert exc_info.value.status_code == 429


# --- Achado da validação do Portal do Cliente (17/09/2026): documentos
# enviados pelo cliente (ArquivoClientePortal) não tinham nenhuma tela
# administrativa equivalente -- a equipe não conseguia ver nem baixar o que
# o cliente enviava pelo portal. ---


def test_listar_arquivos_portal_admin_nega_para_quem_nao_e_responsavel() -> None:
    lead = Lead(id=9, organizacao_id=1, responsavel_id=99)
    usuario = usuario_teste(perfil="comercial")
    session = FakeSession([FakeResult(scalar=lead)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(listar_arquivos_portal_admin(9, session, usuario))
    assert exc_info.value.status_code == 403


def test_listar_arquivos_portal_admin_lista_para_o_responsavel() -> None:
    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    arquivo = ArquivoClientePortal(
        id=5,
        organizacao_id=1,
        lead_id=9,
        cliente_id=1,
        nome="procuracao.pdf",
        caminho="data/portal/1/1/abc-procuracao.pdf",
        content_type="application/pdf",
        tamanho=1024,
        arquivo_hash="hash123",
    )
    session = FakeSession([FakeResult(scalar=lead), FakeResult(itens=[arquivo])])

    resultado = asyncio.run(listar_arquivos_portal_admin(9, session, usuario))

    assert resultado["arquivos"] == [
        {
            "id": 5,
            "nome": "procuracao.pdf",
            "content_type": "application/pdf",
            "tamanho": 1024,
            "hash": "hash123",
            "criado_em": None,
        }
    ]


def test_listar_arquivos_portal_admin_lead_inexistente_retorna_404() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(listar_arquivos_portal_admin(999, session, usuario))
    assert exc_info.value.status_code == 404


# --- Item 1 do pedido de melhorias do cliente final (17/09/2026): linha do
# tempo do processo de registro com % de progresso. ---


def test_progresso_processo_situacao_desconhecida_fica_em_depositado() -> None:
    # Processo recem importado, sem nenhuma movimentacao classificada ainda
    # -- so sabemos com certeza que foi depositado.
    assert progresso_processo(None) == {"percentual": 20, "etapa": "Depositado", "alerta": None, "resultado": "ativo"}
    assert progresso_processo("nao_classificada")["percentual"] == 20
    assert progresso_processo("situacao-inexistente-no-mapa")["percentual"] == 20


def test_progresso_processo_avanca_pelas_etapas_oficiais() -> None:
    assert progresso_processo("publicada")["percentual"] == 40
    assert progresso_processo("em_exame")["percentual"] == 60
    assert progresso_processo("deferida")["percentual"] == 80
    assert progresso_processo("registrada") == {
        "percentual": 100,
        "etapa": "Registro concedido",
        "alerta": None,
        "resultado": "ativo",
    }


def test_progresso_processo_desvios_nao_avancam_nem_retrocedem_mas_geram_alerta() -> None:
    # Achado: exigencia/oposicao/recurso/sobrestamento sao desvios
    # condicionais da jornada real do INPI, nao etapas fixas -- ficam na
    # etapa onde normalmente ocorrem, com um alerta, sem virar um degrau
    # de progresso a parte.
    exigencia = progresso_processo("exigencia")
    assert exigencia["percentual"] == 60
    assert exigencia["alerta"] is not None

    oposicao = progresso_processo("oposicao")
    assert oposicao["percentual"] == 40
    assert oposicao["alerta"] is not None


def test_progresso_processo_desfechos_negativos_nao_aparecem_como_sucesso() -> None:
    indeferida = progresso_processo("indeferida")
    assert indeferida["resultado"] == "negativo"
    assert indeferida["percentual"] < 100

    extinta = progresso_processo("extinta")
    assert extinta["resultado"] == "negativo"


def test_jornada_portal_exibe_funil_inteiro_antes_do_protocolo_sem_chamar_etapas_de_puladas() -> None:
    criado_em = datetime(2026, 9, 10, 12, tzinfo=UTC)
    ganho_em = datetime(2026, 9, 17, 16, 36, tzinfo=UTC)
    lead = Lead(id=358, organizacao_id=1, fase="ganho", criado_em=criado_em)
    historico = [HistoricoFaseLead(fase="ganho", entrou_em=ganho_em)]

    jornada = montar_jornada_registro(lead, historico, [], [])

    assert [item["fase"] for item in jornada] == [
        "contato_inicial",
        "qualificado",
        "relatorio_enviado",
        "proposta_enviada",
        "proposta_aceita",
        "aguardando_pagamento",
        "pagamento_confirmado",
        "ganho",
        "protocolo_inpi",
        "processo_inpi",
    ]
    assert jornada[0]["ocorrido_em"] == criado_em
    assert jornada[1]["situacao"] == "concluida_sem_data"
    assert jornada[7] == {
        "fase": "ganho",
        "label": "Contratação concluída",
        "situacao": "atual",
        "ocorrido_em": ganho_em,
    }
    assert jornada[8]["situacao"] == "pendente"
    assert jornada[9]["situacao"] == "pendente"


def test_jornada_portal_usa_eventos_objetivos_da_proposta_e_do_processo() -> None:
    criado_em = datetime(2026, 9, 1, 12, tzinfo=UTC)
    enviado_em = datetime(2026, 9, 3, 12, tzinfo=UTC)
    aceito_em = datetime(2026, 9, 4, 12, tzinfo=UTC)
    pago_em = datetime(2026, 9, 5, 12, tzinfo=UTC)
    vinculado_em = datetime(2026, 9, 8, 12, tzinfo=UTC)
    lead = Lead(id=9, organizacao_id=1, fase="processo_inpi", criado_em=criado_em)
    proposta = PropostaComercial(
        enviado_em=enviado_em,
        aceito_em=aceito_em,
        pagamento_confirmado_em=pago_em,
    )
    monitorado = ProcessoMonitorado(criado_em=vinculado_em)
    processo = Processo(numero="BR512345678")

    jornada = montar_jornada_registro(lead, [], [proposta], [(monitorado, processo)])
    por_fase = {item["fase"]: item for item in jornada}

    assert por_fase["proposta_enviada"]["ocorrido_em"] == enviado_em
    assert por_fase["proposta_aceita"]["ocorrido_em"] == aceito_em
    assert por_fase["aguardando_pagamento"]["ocorrido_em"] == aceito_em
    assert por_fase["pagamento_confirmado"]["ocorrido_em"] == pago_em
    assert por_fase["protocolo_inpi"]["ocorrido_em"] == vinculado_em
    assert por_fase["processo_inpi"]["situacao"] == "atual"
    assert por_fase["processo_inpi"]["ocorrido_em"] == vinculado_em


def test_listar_processos_portal_inclui_progresso() -> None:
    lead = Lead(id=9, organizacao_id=1)
    processo = Processo(id=5, numero="BR512345678", titulo="Marca A", situacao_normalizada="deferida")
    monitorado = ProcessoMonitorado(id=7, organizacao_id=1, processo_id=5, lead_id=9, vinculado_por="teste")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(itens=[(monitorado, processo)])])

    resultado = asyncio.run(listar_processos_portal(_request(), _cliente(), session))

    assert resultado["processos"][0]["percentual"] == 80
    assert resultado["processos"][0]["etapa"] == "Deferido"
