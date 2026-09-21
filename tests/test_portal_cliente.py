import asyncio
import hashlib
import hmac
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.portal_cliente import (
    ClienteLogin,
    RecuperacaoSolicitacao,
    assinar_proposta_portal,
    baixar_logo_cliente_admin,
    baixar_logo_cliente_portal,
    baixar_material_marca_admin,
    baixar_material_marca_portal,
    enviar_logo_cliente_admin,
    enviar_material_marca_admin,
    listar_arquivos_portal_admin,
    listar_materiais_marca_admin,
    listar_materiais_marca_portal,
    listar_prazos_portal,
    listar_processos_portal,
    login_cliente,
    logo_cliente_url,
    logout_cliente,
    montar_jornada_registro,
    progresso_processo,
    remover_logo_cliente_admin,
    remover_material_marca_admin,
    solicitar_recuperacao_portal,
    webhook_clicksign,
)
from app.models import (
    ArquivoClientePortal,
    AssinaturaPropostaComercial,
    ClientePortal,
    HistoricoFaseLead,
    Lead,
    MaterialMarcaCliente,
    Organizacao,
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


class _ArquivoFake:
    """Dublê mínimo de UploadFile para testes de upload -- só o que os
    endpoints de material de marca realmente leem (size/filename/content_type
    e read() assíncrono)."""

    def __init__(self, conteudo: bytes = b"conteudo-fake", filename: str = "logo.png") -> None:
        self.size = len(conteudo)
        self.filename = filename
        self.content_type = "image/png"
        self._conteudo = conteudo

    async def read(self, _tamanho: int | None = None) -> bytes:
        return self._conteudo


def _arquivo_fake() -> _ArquivoFake:
    return _ArquivoFake()


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


_WEBHOOK_SECRET_TESTE = "segredo-de-teste"


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


def _assinatura_webhook(corpo: dict, segredo: str = _WEBHOOK_SECRET_TESTE) -> str:
    body = json.dumps(corpo).encode()
    return hmac.new(segredo.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _configurar_segredo_webhook(monkeypatch: pytest.MonkeyPatch, secret: str = _WEBHOOK_SECRET_TESTE) -> None:
    import app.api.portal_cliente as modulo_portal

    monkeypatch.setattr(
        modulo_portal,
        "configuracao_clicksign",
        lambda org=None: {"enabled": True, "base_url": "", "token": "", "secret": secret},
    )


def test_webhook_clicksign_registra_assinatura_e_gera_contratacao_no_primeiro_evento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurar_segredo_webhook(monkeypatch)
    proposta = _proposta_para_assinatura(
        id=7, status="enviada", dados={"clicksign": {"envelope_id": "env-123"}}
    )
    session = FakeSession(
        [
            FakeResult(scalar=proposta),  # busca por envelope_id
            FakeResult(scalar=None),  # contratação existente? não
        ]
    )
    corpo = {"envelope_id": "env-123", "event_id": "evt-1", "status": "document_closed"}
    resultado = asyncio.run(
        webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo))
    )
    assert resultado == {"ok": True, "proposta_id": 7}
    assert proposta.status == "aceita"
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert len(assinaturas) == 1
    assert assinaturas[0].provedor == "clicksign"


def test_webhook_clicksign_nao_duplica_assinatura_em_segundo_evento_do_mesmo_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configurar_segredo_webhook(monkeypatch)
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
    corpo = {"envelope_id": "env-123", "event_id": "evt-2", "status": "envelope_closed"}
    asyncio.run(webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo)))
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert assinaturas == []


def test_webhook_clicksign_proposta_nao_encontrada_e_ignorado(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar_segredo_webhook(monkeypatch)
    session = FakeSession([FakeResult(scalar=None)])
    corpo = {"envelope_id": "env-inexistente", "status": "signed"}
    resultado = asyncio.run(
        webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo))
    )
    assert resultado == {"ok": True, "ignorado": True}


def test_webhook_clicksign_recusa_sem_segredo_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado crítico da Fase 8 (auditoria jurídica, 15/09/2026): sem
    # webhook_secret configurado, o webhook aceitava QUALQUER requisição
    # sem autenticação nenhuma (fail-open) -- bastava saber o envelope_id
    # (previsível) pra forjar "documento assinado" e disparar a contratação
    # automática. Agora falha fechado: sem segredo, nada passa, mesmo que o
    # payload em si seja válido e o envelope exista de verdade.
    _configurar_segredo_webhook(monkeypatch, secret="")
    proposta = _proposta_para_assinatura(id=7, status="enviada", dados={"clicksign": {"envelope_id": "env-123"}})
    session = FakeSession([FakeResult(scalar=proposta)], objetos_get=[Organizacao(id=1, nome="Teste", slug="teste")])
    corpo = {"envelope_id": "env-123", "status": "signed"}

    with pytest.raises(HTTPException) as erro:
        asyncio.run(webhook_clicksign(_webhook_request(corpo), session, None))

    assert erro.value.status_code == 401
    assert proposta.status == "enviada"  # recusado antes de qualquer mutação
    assert session.commits == 0


def test_webhook_clicksign_recusa_assinatura_invalida(monkeypatch: pytest.MonkeyPatch) -> None:
    _configurar_segredo_webhook(monkeypatch)
    proposta = _proposta_para_assinatura(id=7, status="enviada", dados={"clicksign": {"envelope_id": "env-123"}})
    session = FakeSession([FakeResult(scalar=proposta)], objetos_get=[Organizacao(id=1, nome="Teste", slug="teste")])
    corpo = {"envelope_id": "env-123", "status": "signed"}

    with pytest.raises(HTTPException) as erro:
        asyncio.run(webhook_clicksign(_webhook_request(corpo), session, "sha256=assinatura-forjada"))

    assert erro.value.status_code == 401
    assert proposta.status == "enviada"
    assert session.commits == 0


def test_webhook_clicksign_usa_segredo_da_organizacao_dona_do_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achados altos da Fase 8 (auditoria jurídica, 15/09/2026): o webhook
    # ficava travado em default_organization_id -- tanto pro tenant quanto
    # pro segredo de validação -- então uma organização não-padrão com sua
    # própria conta/segredo do Clicksign nunca tinha o pagamento reconhecido
    # por aqui. Agora resolve a organização dona do envelope primeiro
    # (busca cross-tenant, só leitura) e valida a assinatura com o segredo
    # daquela organização específica, não o do platform-wide default.
    import app.api.portal_cliente as modulo_portal

    organizacao_nao_padrao = Organizacao(id=42, nome="Escritório B", slug="escritorio-b")
    segredo_org_42 = "segredo-da-organizacao-42"

    def configuracao_por_organizacao(org=None):
        secret = segredo_org_42 if org is not None and org.id == 42 else "segredo-errado-do-default"
        return {"enabled": True, "base_url": "", "token": "", "secret": secret}

    monkeypatch.setattr(modulo_portal, "configuracao_clicksign", configuracao_por_organizacao)

    proposta = _proposta_para_assinatura(
        id=9, organizacao_id=42, status="enviada", dados={"clicksign": {"envelope_id": "env-999"}}
    )
    session = FakeSession(
        [
            FakeResult(scalar=proposta),  # busca cross-tenant por envelope_id
            FakeResult(scalar=None),  # contratação existente? não
        ],
        objetos_get=[organizacao_nao_padrao],
    )
    corpo = {"envelope_id": "env-999", "event_id": "evt-1", "status": "document_closed"}

    resultado = asyncio.run(
        webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo, segredo=segredo_org_42))
    )

    assert resultado == {"ok": True, "proposta_id": 9}
    assert proposta.status == "aceita"


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


# --- Item 2 do pedido de melhorias do cliente final (17/09/2026): área de
# Identidade Visual por cliente. Escopo definido com o usuário: só a equipe
# interna cadastra materiais (logo, manual de marca, artes prontas); o
# cliente só visualiza e baixa no portal. ---


def test_listar_materiais_marca_admin_nega_para_quem_nao_e_responsavel() -> None:
    lead = Lead(id=9, organizacao_id=1, responsavel_id=99)
    usuario = usuario_teste(perfil="comercial")
    session = FakeSession([FakeResult(scalar=lead)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(listar_materiais_marca_admin(9, session, usuario))
    assert exc_info.value.status_code == 403


def test_listar_materiais_marca_admin_lista_para_o_responsavel() -> None:
    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    material = MaterialMarcaCliente(
        id=3,
        organizacao_id=1,
        lead_id=9,
        nome="manual-de-marca.pdf",
        descricao="Manual de identidade visual",
        caminho="data/materiais-marca/1/9/abc-manual-de-marca.pdf",
        content_type="application/pdf",
        tamanho=2048,
        arquivo_hash="hash456",
    )
    session = FakeSession([FakeResult(scalar=lead), FakeResult(itens=[material])])

    resultado = asyncio.run(listar_materiais_marca_admin(9, session, usuario))

    assert resultado["materiais"] == [
        {
            "id": 3,
            "nome": "manual-de-marca.pdf",
            "descricao": "Manual de identidade visual",
            "content_type": "application/pdf",
            "tamanho": 2048,
            "criado_em": None,
        }
    ]


def test_listar_materiais_marca_admin_lead_inexistente_retorna_404() -> None:
    usuario = usuario_teste()
    session = FakeSession([FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(listar_materiais_marca_admin(999, session, usuario))
    assert exc_info.value.status_code == 404


def test_enviar_material_marca_admin_nega_para_quem_nao_e_responsavel() -> None:
    lead = Lead(id=9, organizacao_id=1, responsavel_id=99)
    usuario = usuario_teste(perfil="comercial")
    session = FakeSession([FakeResult(scalar=lead)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            enviar_material_marca_admin(9, _request(), session, usuario, arquivo=_arquivo_fake(), descricao=None)
        )
    assert exc_info.value.status_code == 403


def test_enviar_material_marca_admin_salva_e_audita(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.portal_cliente as modulo_portal

    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    session = FakeSession([FakeResult(scalar=lead)])

    async def escanear_ok(_conteudo: bytes) -> None:
        return None

    monkeypatch.setattr(modulo_portal, "escanear_upload_ou_rejeitar", escanear_ok)
    monkeypatch.setattr(modulo_portal, "save_bytes", lambda chave, _conteudo: f"data/{chave}")

    resultado = asyncio.run(
        enviar_material_marca_admin(
            9, _request(), session, usuario, arquivo=_arquivo_fake(), descricao="  Logo em PNG  "
        )
    )

    assert resultado["nome"] == "logo.png"
    item = session.adicionados[0]
    assert isinstance(item, MaterialMarcaCliente)
    assert item.organizacao_id == 1 and item.lead_id == 9
    assert item.descricao == "Logo em PNG"
    assert item.enviado_por_id == usuario.id
    assert session.commits == 1


def test_baixar_material_marca_admin_material_inexistente_retorna_404() -> None:
    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(baixar_material_marca_admin(9, 3, _request(), session, usuario))
    assert exc_info.value.status_code == 404


def test_baixar_material_marca_admin_encontra_arquivo_salvo_localmente(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Achado do usuário (20/09/2026, lead "Tactical Cloud"): baixar um
    # material de marca já enviado devolvia 404 "Material não encontrado"
    # mesmo com o arquivo existindo no disco. Causa: a checagem de
    # path-traversal aqui usava Path("data") hardcoded, mas save_bytes()
    # grava em STORAGE_LOCAL_ROOT (padrão "data/uploads") -- o "uploads"
    # nunca batia, então todo download local falhava sempre, não só pra
    # este cliente.
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))
    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    caminho_real = tmp_path / "materiais-marca" / "1" / "9" / "logo.png"
    caminho_real.parent.mkdir(parents=True)
    caminho_real.write_bytes(b"conteudo-fake")
    material = MaterialMarcaCliente(
        id=3,
        organizacao_id=1,
        lead_id=9,
        nome="logo.png",
        caminho=str(caminho_real),
        content_type="image/png",
    )
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=material)])

    resultado = asyncio.run(baixar_material_marca_admin(9, 3, _request(), session, usuario))

    assert Path(resultado.path) == caminho_real
    assert session.commits == 1


def test_remover_material_marca_admin_remove_e_audita(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.portal_cliente as modulo_portal

    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    material = MaterialMarcaCliente(id=3, organizacao_id=1, lead_id=9, nome="logo.png", caminho="data/x")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=material)])
    caminhos_apagados: list[str] = []
    monkeypatch.setattr(modulo_portal, "delete_object", caminhos_apagados.append)

    resultado = asyncio.run(remover_material_marca_admin(9, 3, _request(), session, usuario))

    assert resultado == {"removido": True}
    assert session.deletados == [material]
    assert session.commits == 1
    assert caminhos_apagados == ["data/x"]


def test_remover_material_marca_admin_material_inexistente_retorna_404() -> None:
    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    session = FakeSession([FakeResult(scalar=lead), FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(remover_material_marca_admin(9, 3, _request(), session, usuario))
    assert exc_info.value.status_code == 404


def test_listar_materiais_marca_portal_escopado_ao_cliente() -> None:
    cliente = _cliente()
    material = MaterialMarcaCliente(
        id=3,
        organizacao_id=1,
        lead_id=9,
        nome="manual-de-marca.pdf",
        descricao=None,
        caminho="x",
        content_type="application/pdf",
        tamanho=2048,
    )
    session = FakeSession([FakeResult(itens=[material])])

    resultado = asyncio.run(listar_materiais_marca_portal(_request(), cliente, session))

    assert resultado["materiais"][0]["nome"] == "manual-de-marca.pdf"
    assert session.commits == 1


def test_baixar_material_marca_portal_material_inexistente_retorna_404() -> None:
    cliente = _cliente()
    session = FakeSession([FakeResult(scalar=None)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(baixar_material_marca_portal(3, _request(), cliente, session))
    assert exc_info.value.status_code == 404


def test_baixar_material_marca_portal_encontra_arquivo_salvo_localmente(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Mesmo achado do lado do cliente (download direto do portal, não pelo
    # admin) -- ver test_baixar_material_marca_admin_encontra_arquivo_salvo_localmente.
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))
    cliente = _cliente()
    caminho_real = tmp_path / "materiais-marca" / "1" / "9" / "manual-de-marca.pdf"
    caminho_real.parent.mkdir(parents=True)
    caminho_real.write_bytes(b"conteudo-fake")
    material = MaterialMarcaCliente(
        id=3,
        organizacao_id=1,
        lead_id=9,
        nome="manual-de-marca.pdf",
        caminho=str(caminho_real),
        content_type="application/pdf",
    )
    session = FakeSession([FakeResult(scalar=material)])

    resultado = asyncio.run(baixar_material_marca_portal(3, _request(), cliente, session))

    assert Path(resultado.path) == caminho_real


# --- Item 4/5 do pedido de melhorias do cliente final (17/09/2026): logo do
# cliente exibida dinamicamente na mão do personagem no portal. ---


def test_logo_cliente_url_sem_asset_retorna_none() -> None:
    assert logo_cliente_url(Lead(id=9, organizacao_id=1)) is None
    assert logo_cliente_url(Lead(id=9, organizacao_id=1, logo_cliente={})) is None


def test_logo_cliente_url_com_asset_usa_hash_como_cache_bust() -> None:
    lead = Lead(id=9, organizacao_id=1, logo_cliente={"sha256": "abcdef0123456789" + "0" * 40})
    assert logo_cliente_url(lead) == "/v1/portal/logo-cliente?v=abcdef0123456789"


def test_enviar_logo_cliente_admin_nega_para_quem_nao_e_responsavel() -> None:
    lead = Lead(id=9, organizacao_id=1, responsavel_id=99)
    usuario = usuario_teste(perfil="comercial")
    session = FakeSession([FakeResult(scalar=lead)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(enviar_logo_cliente_admin(9, _request(), session, usuario, arquivo=_arquivo_fake()))
    assert exc_info.value.status_code == 403


def test_enviar_logo_cliente_admin_salva_e_audita(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.portal_cliente as modulo_portal

    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    session = FakeSession([FakeResult(scalar=lead)])
    monkeypatch.setattr(
        "app.api.confiabilidade.normalizar_logo", lambda _conteudo: (b"png-normalizado", 200, 200)
    )
    monkeypatch.setattr(modulo_portal, "save_bytes", lambda chave, _conteudo: f"data/{chave}")

    resultado = asyncio.run(enviar_logo_cliente_admin(9, _request(), session, usuario, arquivo=_arquivo_fake()))

    assert resultado["logo_url"] is not None
    assert lead.logo_cliente["largura"] == 200
    assert lead.logo_cliente["atualizado_por"] == usuario.email
    assert session.commits == 1


def test_enviar_logo_cliente_admin_imagem_invalida_retorna_422(monkeypatch: pytest.MonkeyPatch) -> None:
    def _rejeitar(_conteudo: bytes) -> tuple[bytes, int, int]:
        raise ValueError("Envie uma imagem PNG, JPEG ou WebP")

    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    session = FakeSession([FakeResult(scalar=lead)])
    monkeypatch.setattr("app.api.confiabilidade.normalizar_logo", _rejeitar)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(enviar_logo_cliente_admin(9, _request(), session, usuario, arquivo=_arquivo_fake()))
    assert exc_info.value.status_code == 422


def test_baixar_logo_cliente_admin_sem_logo_retorna_404() -> None:
    usuario = usuario_teste(perfil="comercial")
    lead = Lead(id=9, organizacao_id=1, responsavel_id=usuario.id)
    session = FakeSession([FakeResult(scalar=lead)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(baixar_logo_cliente_admin(9, session, usuario))
    assert exc_info.value.status_code == 404


def test_remover_logo_cliente_admin_remove_arquivo_e_limpa_campo(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.portal_cliente as modulo_portal

    usuario = usuario_teste(perfil="comercial")
    lead = Lead(
        id=9,
        organizacao_id=1,
        responsavel_id=usuario.id,
        logo_cliente={"localizacao": "data/logo-cliente/lead-9/x.png"},
    )
    session = FakeSession([FakeResult(scalar=lead)])
    caminhos_apagados: list[str] = []
    monkeypatch.setattr(modulo_portal, "delete_object", caminhos_apagados.append)

    resultado = asyncio.run(remover_logo_cliente_admin(9, _request(), session, usuario))

    assert resultado == {"status": "ok"}
    assert lead.logo_cliente is None
    assert caminhos_apagados == ["data/logo-cliente/lead-9/x.png"]


def test_baixar_logo_cliente_portal_sem_logo_retorna_404() -> None:
    cliente = _cliente()
    lead = Lead(id=9, organizacao_id=1)
    session = FakeSession([FakeResult(scalar=lead)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(baixar_logo_cliente_portal(cliente, session))
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


# --- Achado da varredura ampla do sistema (18/09/2026): falha de envio do
# e-mail de recuperação era engolida sem log nenhum -- a resposta ao cliente
# continua indistinguível (não revela se a conta existe), mas agora a
# falha fica visível para a equipe. ---


def test_solicitar_recuperacao_loga_falha_de_envio_sem_mudar_resposta(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.api.portal_cliente as modulo_portal

    cliente = _cliente()
    session = FakeSession([FakeResult(scalar=cliente)])

    async def falhar_envio(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("SMTP indisponível")

    monkeypatch.setattr(modulo_portal, "enviar_recuperacao_portal", falhar_envio)

    with caplog.at_level("ERROR", logger="ze_registra.portal_cliente"):
        resultado = asyncio.run(
            solicitar_recuperacao_portal(RecuperacaoSolicitacao(email="cliente@empresa.com.br"), _request(), session)
        )

    assert resultado == {"status": "ok", "mensagem": "Se a conta existir, a recuperação foi criada."}
    assert any("recuperação" in registro.message for registro in caplog.records)
    assert session.commits == 1
