import asyncio
import hashlib
import hmac
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.api.portal_cliente import (
    AssinarComCodigoInput,
    ClienteLogin,
    RecuperacaoRedefinicao,
    RecuperacaoSolicitacao,
    _documento_portal_pronto_para_assinar,
    _hash_assinatura_documento,
    _hash_assinatura_proposta,
    _serializar_parcela_portal,
    assinar_documento_portal,
    assinar_proposta_portal,
    baixar_documento_portal,
    baixar_logo_cliente_admin,
    baixar_logo_cliente_portal,
    baixar_material_marca_admin,
    baixar_material_marca_portal,
    criar_acesso_cliente,
    enviar_logo_cliente_admin,
    enviar_material_marca_admin,
    exigir_csrf_portal,
    listar_arquivos_portal_admin,
    listar_materiais_marca_admin,
    listar_materiais_marca_portal,
    listar_prazos_portal,
    listar_processos_portal,
    login_cliente,
    logo_cliente_url,
    logout_cliente,
    montar_jornada_registro,
    montar_macroetapas,
    obter_cliente_portal,
    progresso_processo,
    redefinir_acesso_portal,
    remover_logo_cliente_admin,
    remover_material_marca_admin,
    solicitar_codigo_assinatura_documento_portal,
    solicitar_codigo_assinatura_proposta_portal,
    solicitar_recuperacao_portal,
    webhook_clicksign,
)
from app.auth import hash_senha, hash_token
from app.models import (
    ArquivoClientePortal,
    AssinaturaDocumentoLead,
    AssinaturaPropostaComercial,
    ClientePortal,
    CodigoConfirmacaoPortal,
    DocumentoLead,
    HistoricoFaseLead,
    Lead,
    MaterialMarcaCliente,
    Organizacao,
    ParcelaFinanceira,
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


def _request_mutavel(method: str = "POST", csrf_token: str | None = None) -> Request:
    headers = [(b"x-csrf-token", csrf_token.encode())] if csrf_token else []
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/v1/portal/mensagens",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _cliente() -> ClientePortal:
    return ClientePortal(id=1, organizacao_id=1, lead_id=9, email="cliente@empresa.test", ativo=True)


_CODIGO_CONFIRMACAO_TESTE = "123456"


def _codigo_confirmacao(recurso_tipo: str, recurso: object, *, cliente_id: int = 1) -> CodigoConfirmacaoPortal:
    """Registro de código de confirmação já válido pra assinatura no
    portal (Fase 13.2, 23/09/2026) -- consumido por
    _validar_codigo_confirmacao_portal, chamado antes de
    assinar_proposta_portal/assinar_documento_portal mutarem qualquer
    coisa. recurso_hash calculado com a mesma função de produção (achado
    do Codex no PR #120), pra não descolar do que o endpoint realmente
    compara."""
    hash_fn = _hash_assinatura_proposta if recurso_tipo == "proposta" else _hash_assinatura_documento
    return CodigoConfirmacaoPortal(
        id=1,
        organizacao_id=1,
        cliente_id=cliente_id,
        recurso_tipo=recurso_tipo,
        recurso_id=recurso.id,
        recurso_hash=hash_fn(recurso),
        codigo_hash=hash_token(_CODIGO_CONFIRMACAO_TESTE),
        expira_em=datetime(2099, 1, 1, tzinfo=UTC),
        tentativas=0,
    )


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


# Achado baixo da auditoria fina do Portal do Cliente (Fase 13.2,
# 23/09/2026): logout_cliente era a única mutação do arquivo sem exigir
# CSRF (não passava por ClientCsrfDep) -- um site malicioso podia forjar
# o POST (o cookie de sessão é enviado automaticamente) e derrubar a
# sessão do cliente sem interação. Agora depende de ClientCsrfDep, que já
# resolve a sessão/cliente e confere o token double-submit; a ordem
# tenant-antes-de-revogar passou a ser garantida pela própria cadeia de
# dependências do FastAPI (obter_cliente_portal roda por completo antes
# de exigir_csrf_portal, que roda por completo antes do corpo de
# logout_cliente) em vez de sequenciamento manual dentro da função.


def test_criar_acesso_cliente_ja_existente_revoga_sessoes_antigas() -> None:
    # Achado médio da auditoria fina do Portal do Cliente (Fase 13.3,
    # 23/09/2026): reemitir acesso (nova senha) pra um cliente já
    # existente trocava a senha mas deixava sessões antigas ainda válidas
    # -- diferente de redefinir_acesso_portal (autorredefinição), que já
    # revogava. Motivo comum de gerar senha nova é suspeita de conta
    # comprometida; uma sessão antiga viva anularia o propósito.
    lead = Lead(id=9, organizacao_id=1, nome="Empresa Teste", email="empresa@teste.com.br", responsavel_id=1)
    cliente_existente = _cliente()
    from app.models import SessaoClientePortal

    sessao_antiga = SessaoClientePortal(
        id=5, cliente_id=cliente_existente.id, token_hash="hash-antigo", expira_em=datetime(2099, 1, 1, tzinfo=UTC)
    )
    session = FakeSession(
        [FakeResult(scalar=lead), FakeResult(scalar=cliente_existente), FakeResult(itens=[sessao_antiga])]
    )
    usuario = usuario_teste(perfil="administrador")

    resultado = asyncio.run(criar_acesso_cliente(9, _request(), session, usuario))

    assert "senha_temporaria" in resultado
    assert sessao_antiga.revogada_em is not None
    # Achado P1 do Codex no PR #122: o marcador de geração de senha é o
    # que fecha a corrida de verdade -- ver test_obter_cliente_portal_*.
    assert cliente_existente.senha_alterada_em is not None


def _request_com_sessao_portal(token: str = "token-de-sessao-teste") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/portal/me",
            "headers": [(b"cookie", f"zr_client_session={token}".encode())],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_obter_cliente_portal_aceita_sessao_com_marcador_atual() -> None:
    from app.models import SessaoClientePortal

    agora = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    cliente = _cliente()
    cliente.senha_alterada_em = agora
    sessao = SessaoClientePortal(
        id=1,
        cliente_id=cliente.id,
        token_hash=hash_token("token-de-sessao-teste"),
        expira_em=datetime(2099, 1, 1, tzinfo=UTC),
        senha_versao_no_login=agora,
    )
    session = FakeSession([FakeResult(scalar=sessao), FakeResult(scalar=cliente)])

    resultado = asyncio.run(obter_cliente_portal(_request_com_sessao_portal(), session))

    assert resultado is cliente


def test_obter_cliente_portal_rejeita_sessao_de_antes_da_troca_de_senha() -> None:
    # Achado P1 do Codex no PR #122 (Fase 13.3, 23/09/2026): revogar
    # sessões ativas (marcar revogada_em) no instante da troca de senha é
    # uma corrida -- um login concorrente com a senha antiga pode validar
    # antes da troca e só comitar a sessão depois do SELECT de revogação
    # já ter tirado o retrato. O marcador de geração (senha_versao_no_login
    # vs. cliente.senha_alterada_em) fecha a corrida de vez: é conferido em
    # toda requisição, não só no instante da troca.
    from app.models import SessaoClientePortal

    cliente = _cliente()
    cliente.senha_alterada_em = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    sessao_de_antes_da_troca = SessaoClientePortal(
        id=1,
        cliente_id=cliente.id,
        token_hash=hash_token("token-de-sessao-teste"),
        expira_em=datetime(2099, 1, 1, tzinfo=UTC),
        senha_versao_no_login=None,
        revogada_em=None,
    )
    session = FakeSession([FakeResult(scalar=sessao_de_antes_da_troca), FakeResult(scalar=cliente)])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(obter_cliente_portal(_request_com_sessao_portal(), session))
    assert exc_info.value.status_code == 401


def test_logout_revoga_sessao_e_limpa_cookie() -> None:
    from app.models import SessaoClientePortal

    sessao = SessaoClientePortal(id=4, cliente_id=1, token_hash="hash", expira_em=datetime(2099, 1, 1, tzinfo=UTC))
    cliente = _cliente()
    session = FakeSession()
    request = _request_mutavel(method="POST")
    request.state.portal_sessao = sessao
    response = Response()

    resultado = asyncio.run(logout_cliente(request, response, cliente, session))

    assert resultado == {"ok": True}
    assert sessao.revogada_em is not None
    assert session.commits == 1
    cookies = response.headers.getlist("set-cookie")
    assert any("zr_client_session=" in cookie for cookie in cookies)
    # Achado baixo da auditoria fina do Portal do Cliente (Fase 13.3,
    # 23/09/2026): o cookie CSRF (zr_portal_csrf) ficava órfão no logout --
    # só o de sessão era apagado.
    assert any("zr_portal_csrf=" in cookie for cookie in cookies)


# --- Fase 1 do plano proposta-financeiro (03/09/2026): blindar o aceite ---


# Proposta já classificada no Financeiro: o aceite gera os títulos a receber
# (2 flush() em criar_contratacao_automatica_proposta).
_PLANOS_CONTABEIS_TESTE = {"conta_contabil_honorarios_id": 11, "conta_contabil_taxa_gru_id": 12}


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
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    try:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
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
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_codigo_confirmacao("proposta", proposta))])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    resultado = asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert resultado["ok"] is True
    assert proposta.public_aceito_em == ja_aceita_em


def test_assinar_proposta_portal_dentro_da_validade_prossegue_com_a_assinatura() -> None:
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31))
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_codigo_confirmacao("proposta", proposta))])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    resultado = asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert resultado["ok"] is True
    assert proposta.status == "aceita"
    # Achado do Codex no PR #120: a assinatura no portal já passou pelo
    # código de confirmação -- precisa registrar essa evidência, senão
    # fica indistinguível de uma assinatura sem segundo fator na auditoria.
    assinatura = next(obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial))
    assert assinatura.segundo_fator_canal == "email"
    assert assinatura.segundo_fator_confirmado_em is not None


def test_assinar_proposta_portal_sem_validade_definida_nao_e_bloqueada() -> None:
    proposta = _proposta_para_assinatura(validade_em=None)
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_codigo_confirmacao("proposta", proposta))])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    resultado = asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert resultado["ok"] is True
    assert proposta.status == "aceita"


def test_assinar_proposta_portal_rejeita_codigo_incorreto() -> None:
    # Achado médio da auditoria fina do Portal do Cliente (Fase 13.2,
    # 23/09/2026, decisão do usuário): segundo fator por e-mail antes de
    # assinar -- código errado não pode deixar a assinatura passar.
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31))
    codigo = _codigo_confirmacao("proposta", proposta)
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=codigo)])
    dados = AssinarComCodigoInput(codigo="000000")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert exc_info.value.status_code == 400
    assert codigo.tentativas == 1
    assert proposta.status == "enviada"
    assert proposta.public_aceito_em is None


def test_assinar_proposta_portal_absorve_conflito_de_assinatura_concorrente(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): um duplo
    # clique no botão de assinar no portal passa duas requisições quase
    # simultâneas pelo "public_aceito_em is None". A segunda deve absorver
    # o IntegrityError (UniqueConstraint proposta_id+versao da migration
    # d4e5f6a7b8c9) em vez de devolver 500.
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31), dados=dict(_PLANOS_CONTABEIS_TESTE))
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=_codigo_confirmacao("proposta", proposta))])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    chamadas_flush = {"n": 0}
    flush_original = session.flush

    async def _flush_com_conflito_na_terceira_chamada() -> None:
        chamadas_flush["n"] += 1
        if chamadas_flush["n"] <= 2:
            await flush_original()
            return
        raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))

    session.flush = _flush_com_conflito_na_terceira_chamada

    resultado = asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))

    assert resultado["ok"] is True
    assert proposta.status == "aceita"
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert assinaturas == []


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


def test_webhook_clicksign_absorve_conflito_de_assinatura_concorrente(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): o Clicksign
    # reenvia o mesmo webhook em retry -- duas entregas quase simultâneas
    # passam as duas pelo "aceito_em is None" e tentam inserir duas linhas
    # de evidência pra mesma versão da proposta. A segunda deve absorver o
    # IntegrityError (proteção de última linha é a UniqueConstraint
    # proposta_id+versao da migration d4e5f6a7b8c9) em vez de devolver 500.
    _configurar_segredo_webhook(monkeypatch)
    proposta = _proposta_para_assinatura(id=7, status="enviada", dados={"clicksign": {"envelope_id": "env-123"}, **_PLANOS_CONTABEIS_TESTE})
    session = FakeSession(
        [
            FakeResult(scalar=proposta),  # busca por envelope_id
            FakeResult(scalar=None),  # contratação existente? não
        ]
    )
    chamadas_flush = {"n": 0}
    flush_original = session.flush

    async def _flush_com_conflito_na_terceira_chamada() -> None:
        # criar_contratacao_automatica_proposta já faz 2 flush() (lançamento
        # + fim do bloco) antes do flush da assinatura em webhook_clicksign.
        chamadas_flush["n"] += 1
        if chamadas_flush["n"] <= 2:
            await flush_original()
            return
        raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))

    session.flush = _flush_com_conflito_na_terceira_chamada
    corpo = {"envelope_id": "env-123", "event_id": "evt-1", "status": "document_closed"}

    resultado = asyncio.run(webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo)))

    assert resultado == {"ok": True, "proposta_id": 7}
    assert proposta.status == "aceita"
    # A savepoint (begin_nested) descarta só a assinatura em conflito --
    # nada quebra e o restante da requisição continua idempotente.
    assinaturas = [obj for obj in session.adicionados if isinstance(obj, AssinaturaPropostaComercial)]
    assert assinaturas == []


def test_webhook_clicksign_redige_dados_pessoais_do_payload_bruto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado médio da Fase 8 (auditoria jurídica, 15/09/2026): o payload
    # bruto do webhook (nome/e-mail/CPF/telefone/IP do signatário) ficava
    # salvo sem redação em PropostaComercial.dados["clicksign"]["ultimo_evento"].
    _configurar_segredo_webhook(monkeypatch)
    proposta = _proposta_para_assinatura(id=7, status="enviada", dados={"clicksign": {"envelope_id": "env-123"}})
    session = FakeSession(
        [
            FakeResult(scalar=proposta),
            FakeResult(scalar=None),
        ]
    )
    corpo = {
        "envelope_id": "env-123",
        "event_id": "evt-1",
        "status": "document_closed",
        "data": {
            "signers": [{"name": "Fulano de Tal", "email": "fulano@example.com", "documentation": "12345678900"}],
        },
    }
    asyncio.run(webhook_clicksign(_webhook_request(corpo), session, _assinatura_webhook(corpo)))
    salvo = proposta.dados["clicksign"]["ultimo_evento"]
    signatario = salvo["data"]["signers"][0]
    assert signatario["name"] == "[redigido]"
    assert signatario["email"] == "[redigido]"
    assert signatario["documentation"] == "[redigido]"
    # Chaves não sensíveis (status/ids) continuam legíveis para diagnóstico.
    assert salvo["status"] == "document_closed"
    assert salvo["envelope_id"] == "env-123"


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


# --- Achado alto da auditoria do Portal do Cliente (Fase 9, 21/09/2026):
# só havia rate-limit por IP, sem bloqueio da própria conta -- um atacante
# rotacionando IPs podia tentar senha contra um cliente indefinidamente. ---


def test_login_cliente_bloqueia_conta_apos_5_tentativas_mesmo_com_ips_diferentes() -> None:
    dados = ClienteLogin(email="cliente@empresa.com.br", senha="senha-errada")
    # A mesma conta (mesma linha ClientePortal) é reaproveitada em todas as
    # tentativas -- só o IP muda, simulando um atacante rotacionando IPs
    # pra nunca bater no rate-limit (10/60s por IP), que sozinho não bastava.
    cliente = ClientePortal(
        id=1,
        organizacao_id=1,
        lead_id=9,
        email="cliente@empresa.com.br",
        senha_hash="hash-invalido",
        ativo=True,
        tentativas_falhas=0,
    )
    for tentativa in range(5):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/portal/login",
                "headers": [],
                "client": (f"10.0.0.{tentativa}", 12345),
                "scheme": "http",
                "server": ("testserver", 80),
            }
        )
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(login_cliente(dados, request, Response(), FakeSession([FakeResult(scalar=cliente)])))
        assert exc_info.value.status_code == 401
        assert cliente.tentativas_falhas == (tentativa + 1) % 5
    assert cliente.bloqueado_ate is not None

    cliente_bloqueado = ClientePortal(
        id=1,
        organizacao_id=1,
        lead_id=9,
        email="cliente@empresa.com.br",
        # Senha certa desta vez -- mesmo assim deve ser recusado, porque a
        # 5ª tentativa acima já deixou a conta bloqueada.
        senha_hash=hash_senha("Senha-Correta-123"),
        ativo=True,
        bloqueado_ate=datetime.now(UTC) + timedelta(minutes=15),
    )
    request_final = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/portal/login",
            "headers": [],
            "client": ("10.0.0.99", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    dados_certos = ClienteLogin(email="cliente@empresa.com.br", senha="Senha-Correta-123")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            login_cliente(dados_certos, request_final, Response(), FakeSession([FakeResult(scalar=cliente_bloqueado)]))
        )
    assert exc_info.value.status_code == 401


def test_login_cliente_com_sucesso_zera_tentativas_e_gera_par_csrf() -> None:
    cliente = ClientePortal(
        id=1,
        organizacao_id=1,
        lead_id=9,
        email="cliente@empresa.com.br",
        senha_hash=hash_senha("Senha-Correta-123"),
        ativo=True,
        tentativas_falhas=3,
    )
    dados = ClienteLogin(email="cliente@empresa.com.br", senha="Senha-Correta-123")
    session = FakeSession([FakeResult(scalar=cliente)])
    response = Response()

    resultado = asyncio.run(login_cliente(dados, _request(), response, session))

    assert resultado == {"cliente": {"id": 1, "lead_id": 9, "nome": None, "email": "cliente@empresa.com.br"}}
    assert cliente.tentativas_falhas == 0
    assert cliente.bloqueado_ate is None
    sessao_criada = session.adicionados[0]
    assert sessao_criada.csrf_hash is not None
    # Achado P1 do Codex no PR #122 (Fase 13.3, 23/09/2026): a sessão
    # guarda o marcador de geração de senha vigente no login.
    assert sessao_criada.senha_versao_no_login == cliente.senha_alterada_em
    cookies = response.headers.getlist("set-cookie")
    assert any("zr_client_session=" in cookie and "HttpOnly" in cookie for cookie in cookies)
    assert any("zr_portal_csrf=" in cookie and "HttpOnly" not in cookie for cookie in cookies)


# --- Achado médio da auditoria do Portal do Cliente (Fase 9, 21/09/2026):
# nenhuma mutação do portal exigia CSRF, diferente do painel administrativo. ---


def test_exigir_csrf_portal_bloqueia_requisicao_sem_token() -> None:
    request = _request_mutavel(csrf_token=None)
    request.state.portal_csrf_hash = hash_token("token-real")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(exigir_csrf_portal(request, _cliente()))
    assert exc_info.value.status_code == 403


def test_exigir_csrf_portal_bloqueia_token_incorreto() -> None:
    request = _request_mutavel(csrf_token="token-errado")
    request.state.portal_csrf_hash = hash_token("token-real")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(exigir_csrf_portal(request, _cliente()))
    assert exc_info.value.status_code == 403


def test_exigir_csrf_portal_bloqueia_sessao_sem_par_csrf() -> None:
    """Sessões criadas antes da migration f2a3b4c5d6e7 não têm csrf_hash --
    tratadas como inválidas em vez de aceitas por omissão."""
    request = _request_mutavel(csrf_token="qualquer-coisa")
    request.state.portal_csrf_hash = None
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(exigir_csrf_portal(request, _cliente()))
    assert exc_info.value.status_code == 403


def test_exigir_csrf_portal_aceita_token_correto() -> None:
    request = _request_mutavel(csrf_token="token-real")
    request.state.portal_csrf_hash = hash_token("token-real")
    cliente = _cliente()
    resultado = asyncio.run(exigir_csrf_portal(request, cliente))
    assert resultado is cliente


def test_exigir_csrf_portal_ignora_metodos_seguros() -> None:
    request = _request_mutavel(method="GET", csrf_token=None)
    request.state.portal_csrf_hash = None
    cliente = _cliente()
    resultado = asyncio.run(exigir_csrf_portal(request, cliente))
    assert resultado is cliente


# --- Achado médio da auditoria do Portal do Cliente (Fase 9, 21/09/2026):
# assinar_documento_portal não verificava se o documento já tinha sido
# preenchido pela equipe -- diferente de assinar_proposta_portal, que exige
# um status válido antes de aceitar a assinatura. ---


def test_assinar_documento_portal_bloqueia_quando_status_ainda_e_pendente() -> None:
    documento = DocumentoLead(
        id=3, organizacao_id=1, lead_id=9, tipo="procuracao", status="pendente", numero=None, data=None
    )
    session = FakeSession([FakeResult(scalar=documento)])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(assinar_documento_portal(3, _request(), dados, _cliente(), session))
    assert exc_info.value.status_code == 409
    assert "pronto" in exc_info.value.detail.lower()


def test_assinar_documento_portal_bloqueia_quando_numero_ou_data_vazios() -> None:
    documento = DocumentoLead(
        id=3, organizacao_id=1, lead_id=9, tipo="procuracao", status="recebido", numero=None, data=date(2026, 9, 1)
    )
    session = FakeSession([FakeResult(scalar=documento)])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(assinar_documento_portal(3, _request(), dados, _cliente(), session))
    assert exc_info.value.status_code == 409
    assert "incompleto" in exc_info.value.detail.lower()


def test_assinar_documento_portal_aceita_quando_pronto() -> None:
    documento = DocumentoLead(
        id=3,
        organizacao_id=1,
        lead_id=9,
        tipo="procuracao",
        status="recebido",
        numero="123",
        data=date(2026, 9, 1),
        versao=1,
    )
    session = FakeSession([FakeResult(scalar=documento), FakeResult(scalar=_codigo_confirmacao("documento", documento))])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    resultado = asyncio.run(assinar_documento_portal(3, _request(), dados, _cliente(), session))
    assert resultado["ok"] is True
    assert documento.assinado_em is not None
    # Achado do Codex no PR #120: mesma evidência de segundo fator de
    # AssinaturaPropostaComercial, agora também em AssinaturaDocumentoLead.
    assinatura = next(obj for obj in session.adicionados if isinstance(obj, AssinaturaDocumentoLead))
    assert assinatura.segundo_fator_canal == "email"
    assert assinatura.segundo_fator_confirmado_em is not None


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


# --- Achado do usuário (21/09/2026): baixar_documento_portal sempre 404ava
# -- lia documento.caminho, um atributo que não existia no modelo antes de
# DocumentoLead ganhar upload de arquivo de verdade. ---


def test_baixar_documento_portal_encontra_arquivo_salvo_localmente(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))
    cliente = _cliente()
    caminho_real = tmp_path / "documentos-lead" / "1" / "9" / "procuracao" / "arquivo.pdf"
    caminho_real.parent.mkdir(parents=True)
    caminho_real.write_bytes(b"conteudo-real")
    documento = DocumentoLead(
        id=3,
        organizacao_id=1,
        lead_id=9,
        tipo="procuracao",
        caminho=str(caminho_real),
        content_type="application/pdf",
    )
    session = FakeSession([FakeResult(scalar=documento)])

    resultado = asyncio.run(baixar_documento_portal(3, _request(), cliente, session))

    assert Path(resultado.path) == caminho_real
    # Achado do usuário (21/09/2026): sem a extensão no filename, o sistema
    # operacional não sabia com o que abrir o arquivo baixado.
    assert resultado.filename == "procuracao.pdf"


def test_baixar_documento_portal_s3_sanitiza_extensao_fora_de_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    # Achado do Codex review (PR #95): um Content-Disposition montado à mão
    # só aceita latin-1 -- um nome de arquivo original com extensão fora de
    # ASCII (ex.: caracteres chineses) quebrava o download com 500
    # (UnicodeEncodeError) em vez de simplesmente sanitizar a extensão.
    import app.api.portal_cliente as modulo_portal

    monkeypatch.setattr(modulo_portal, "read_bytes", lambda caminho: b"conteudo-s3")
    documento = DocumentoLead(
        id=3,
        organizacao_id=1,
        lead_id=9,
        tipo="procuracao",
        caminho="s3://bucket/documentos-lead/1/9/procuracao/arquivo.测试",
        content_type="application/pdf",
    )
    session = FakeSession([FakeResult(scalar=documento)])

    resultado = asyncio.run(baixar_documento_portal(3, _request(), _cliente(), session))

    assert resultado.headers["content-disposition"] == 'attachment; filename="procuracao"'


def test_baixar_documento_portal_sem_arquivo_retorna_404() -> None:
    documento = DocumentoLead(id=3, organizacao_id=1, lead_id=9, tipo="procuracao", caminho=None)
    session = FakeSession([FakeResult(scalar=documento)])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(baixar_documento_portal(3, _request(), _cliente(), session))

    assert exc.value.status_code == 404


def test_baixar_documento_portal_nega_caminho_fora_da_raiz(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(tmp_path))
    caminho_fora = tmp_path.parent / "arquivo-fora.pdf"
    caminho_fora.write_bytes(b"nao deveria ser servido")
    documento = DocumentoLead(
        id=3, organizacao_id=1, lead_id=9, tipo="procuracao", caminho=str(caminho_fora), content_type="application/pdf"
    )
    session = FakeSession([FakeResult(scalar=documento)])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(baixar_documento_portal(3, _request(), _cliente(), session))

    assert exc.value.status_code == 404
    caminho_fora.unlink()


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


def test_macroetapas_sem_processo_mostra_so_onboarding_atual() -> None:
    # Revisão do pedido do usuário (21/09/2026): sem processo vinculado
    # ainda, a jornada unificada mostra só a macroetapa 1 em andamento -- as
    # macroetapas 2 a 5 (oficiais do INPI) ficam "pendente", sem inventar
    # progresso que ainda não aconteceu.
    lead = Lead(id=1, organizacao_id=1, fase="proposta_enviada", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])

    blocos = montar_macroetapas(lead, jornada, [], [])

    assert len(blocos) == 1
    macroetapas = blocos[0]["macroetapas"]
    assert [item["indice"] for item in macroetapas] == [1, 2, 3, 4, 5]
    assert macroetapas[0]["situacao"] == "atual"
    assert [item["situacao"] for item in macroetapas[1:]] == ["pendente"] * 4
    assert all(item["sub_eventos"] == [] for item in macroetapas[1:])


def test_macroetapas_marca_onboarding_concluido_quando_ha_processo() -> None:
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    processo = Processo(numero="BR912345678", situacao_normalizada="em_exame")
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))

    blocos = montar_macroetapas(lead, jornada, [(monitorado, processo)], [])

    assert blocos[0]["macroetapas"][0]["situacao"] == "concluida"


def test_macroetapas_em_exame_mostra_previsao_so_na_etapa_atual() -> None:
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    processo = Processo(numero="BR912345678", situacao_normalizada="em_exame")
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))

    macroetapas = montar_macroetapas(lead, jornada, [(monitorado, processo)], [])[0]["macroetapas"]

    exame = next(item for item in macroetapas if item["indice"] == 4)
    assert exame["situacao"] == "atual"
    assert exame["previsao"] == "Previsão média: 8 a 14 meses"
    assert macroetapas[0]["previsao"] is None
    assert macroetapas[4]["previsao"] is None


def test_macroetapas_exigencia_e_oposicao_viram_alerta_sem_desviar_etapa() -> None:
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))

    exigencia = montar_macroetapas(
        lead, jornada, [(monitorado, Processo(numero="BR1", situacao_normalizada="exigencia"))], []
    )[0]["macroetapas"]
    assert next(item for item in exigencia if item["indice"] == 4)["alerta"] is not None
    assert [item["indice"] for item in exigencia if item["situacao"] == "atual"] == [4]

    oposicao = montar_macroetapas(
        lead, jornada, [(monitorado, Processo(numero="BR2", situacao_normalizada="oposicao"))], []
    )[0]["macroetapas"]
    assert next(item for item in oposicao if item["indice"] == 3)["alerta"] is not None
    assert [item["indice"] for item in oposicao if item["situacao"] == "atual"] == [3]


def test_macroetapas_indeferida_encerra_como_concluida_com_alerta() -> None:
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    processo = Processo(numero="BR1", situacao_normalizada="indeferida")
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))

    macroetapas = montar_macroetapas(lead, jornada, [(monitorado, processo)], [])[0]["macroetapas"]

    exame = next(item for item in macroetapas if item["indice"] == 4)
    assert exame["situacao"] == "concluida"
    assert exame["alerta"] == "Pedido indeferido"
    assert next(item for item in macroetapas if item["indice"] == 5)["situacao"] == "pendente"


def test_macroetapas_registrada_fica_100_por_cento_concluida() -> None:
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    processo = Processo(numero="BR1", situacao_normalizada="registrada")
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))

    macroetapas = montar_macroetapas(lead, jornada, [(monitorado, processo)], [])[0]["macroetapas"]

    assert all(item["situacao"] == "concluida" for item in macroetapas)


def test_macroetapas_anexa_documento_ao_sub_evento_certo_quando_processo_unico() -> None:
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    processo = Processo(numero="BR1", situacao_normalizada="registrada", data_deposito=date(2026, 8, 1))
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))
    certificado = DocumentoLead(tipo="certificado", status="assinado", assinado_em=datetime(2026, 9, 20, tzinfo=UTC))
    protocolo = DocumentoLead(tipo="protocolo", status="assinado", data=date(2026, 8, 2))

    macroetapas = montar_macroetapas(lead, jornada, [(monitorado, processo)], [certificado, protocolo])[0][
        "macroetapas"
    ]

    macro5 = next(item for item in macroetapas if item["indice"] == 5)
    assert any(evento["label"] == "Certificado de registro" for evento in macro5["sub_eventos"])
    macro2 = next(item for item in macroetapas if item["indice"] == 2)
    labels_macro2 = [evento["label"] for evento in macro2["sub_eventos"]]
    assert "Pedido depositado" in labels_macro2
    assert "Comprovante de protocolo" in labels_macro2


def test_macroetapas_nao_anexa_documento_quando_lead_tem_mais_de_um_processo() -> None:
    # Achado do desenho desta jornada: DocumentoLead é por lead, não por
    # processo -- com duas marcas no mesmo lead não dá pra saber a qual
    # delas o documento pertence, então nenhum sub-evento de documento
    # aparece em nenhum dos dois blocos.
    lead = Lead(id=1, organizacao_id=1, fase="ganho", criado_em=datetime(2026, 9, 1, tzinfo=UTC))
    jornada = montar_jornada_registro(lead, [], [], [])
    monitorado = ProcessoMonitorado(criado_em=datetime(2026, 9, 10, tzinfo=UTC))
    processo_a = Processo(numero="BR1", situacao_normalizada="registrada")
    processo_b = Processo(numero="BR2", situacao_normalizada="publicada")
    certificado = DocumentoLead(tipo="certificado", status="assinado")

    blocos = montar_macroetapas(
        lead, jornada, [(monitorado, processo_a), (monitorado, processo_b)], [certificado]
    )

    assert len(blocos) == 2
    for bloco in blocos:
        assert all(item["sub_eventos"] == [] for item in bloco["macroetapas"] if item["indice"] != 1)


def test_listar_processos_portal_inclui_progresso() -> None:
    lead = Lead(id=9, organizacao_id=1)
    processo = Processo(id=5, numero="BR512345678", titulo="Marca A", situacao_normalizada="deferida")
    monitorado = ProcessoMonitorado(id=7, organizacao_id=1, processo_id=5, lead_id=9, vinculado_por="teste")
    session = FakeSession([FakeResult(scalar=lead), FakeResult(itens=[(monitorado, processo)])])

    resultado = asyncio.run(listar_processos_portal(_request(), _cliente(), session))

    assert resultado["processos"][0]["percentual"] == 80
    assert resultado["processos"][0]["etapa"] == "Deferido"


# --- Achado médio da auditoria fina do Portal do Cliente (Fase 13.2,
# 23/09/2026, decisão do usuário): assinar proposta/documento no portal
# passa a exigir um código de 6 dígitos por e-mail antes de confirmar
# (mesmo padrão do aceite público de proposta, app.api.leads_propostas). ---


def test_solicitar_codigo_assinatura_proposta_portal_envia_e_persiste(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.portal_cliente as modulo_portal

    proposta = _proposta_para_assinatura()
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
    enviados = []

    async def capturar_envio(
        destinatario: str, nome: str, codigo: str, descricao: str, organizacao_nome: str | None = None
    ) -> None:
        enviados.append((destinatario, nome, codigo, descricao))

    monkeypatch.setattr(modulo_portal, "enviar_codigo_confirmacao_portal", capturar_envio)

    resultado = asyncio.run(solicitar_codigo_assinatura_proposta_portal(1, _request(), _cliente(), session))

    assert resultado["status"] == "ok"
    assert len(enviados) == 1
    assert enviados[0][0] == "cliente@empresa.test"
    assert "PROP-TEST" in enviados[0][3]
    novos_codigos = [obj for obj in session.adicionados if isinstance(obj, CodigoConfirmacaoPortal)]
    assert len(novos_codigos) == 1
    assert novos_codigos[0].recurso_tipo == "proposta"
    assert novos_codigos[0].recurso_id == 1
    assert novos_codigos[0].recurso_hash == _hash_assinatura_proposta(proposta)
    assert novos_codigos[0].codigo_hash == hash_token(enviados[0][2])


def test_solicitar_codigo_assinatura_proposta_portal_propaga_falha_de_envio(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.portal_cliente as modulo_portal

    proposta = _proposta_para_assinatura()
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])

    async def falhar_envio(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("SMTP indisponível")

    monkeypatch.setattr(modulo_portal, "enviar_codigo_confirmacao_portal", falhar_envio)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(solicitar_codigo_assinatura_proposta_portal(1, _request(), _cliente(), session))
    assert exc_info.value.status_code == 502


def test_solicitar_codigo_assinatura_documento_portal_nao_encontrado() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(solicitar_codigo_assinatura_documento_portal(3, _request(), _cliente(), session))
    assert exc_info.value.status_code == 404


def test_solicitar_codigo_assinatura_documento_portal_bloqueia_documento_nao_pronto() -> None:
    # Achado baixo da Fase 13.6 (23/09/2026): o botão "Assinar" aparecia na
    # tela pra qualquer documento não assinado, inclusive um recém-criado
    # ainda "pendente" e sem número/data -- o cliente só descobria que não
    # dava pra assinar depois de pedir o código por e-mail (gastando o
    # limite de 1 pedido/minuto à toa). Agora bloqueia antes de mandar o
    # código, com a mesma validação usada em assinar_documento_portal.
    documento = DocumentoLead(
        id=3, organizacao_id=1, lead_id=9, tipo="procuracao", status="pendente", numero=None, data=None
    )
    session = FakeSession([FakeResult(scalar=documento)])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(solicitar_codigo_assinatura_documento_portal(3, _request(), _cliente(), session))
    assert exc_info.value.status_code == 409
    assert "pronto" in exc_info.value.detail.lower()


def test_documento_portal_pronto_para_assinar_reflete_a_mesma_validacao() -> None:
    # A mesma checagem exposta como booleano em /v1/portal/resumo
    # (campo "pronto_para_assinar") pra decidir se o front mostra o botão.
    pendente = DocumentoLead(
        id=1, organizacao_id=1, lead_id=9, tipo="procuracao", status="pendente", numero=None, data=None
    )
    incompleto = DocumentoLead(
        id=2, organizacao_id=1, lead_id=9, tipo="procuracao", status="recebido", numero=None, data=date(2026, 9, 1)
    )
    expirado = DocumentoLead(
        id=3,
        organizacao_id=1,
        lead_id=9,
        tipo="procuracao",
        status="recebido",
        numero="123",
        data=date(2020, 1, 1),
        validade_em=date(2020, 6, 1),
    )
    pronto = DocumentoLead(
        id=4, organizacao_id=1, lead_id=9, tipo="procuracao", status="recebido", numero="123", data=date(2026, 9, 1)
    )
    assert _documento_portal_pronto_para_assinar(pendente) is False
    assert _documento_portal_pronto_para_assinar(incompleto) is False
    assert _documento_portal_pronto_para_assinar(expirado) is False
    assert _documento_portal_pronto_para_assinar(pronto) is True


def test_assinar_proposta_portal_rejeita_sem_codigo_solicitado() -> None:
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31))
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=None)])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert exc_info.value.status_code == 400
    assert proposta.status == "enviada"


def test_assinar_proposta_portal_rejeita_codigo_expirado() -> None:
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31))
    codigo = _codigo_confirmacao("proposta", proposta)
    codigo.expira_em = datetime(2020, 1, 1, tzinfo=UTC)
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=codigo)])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert exc_info.value.status_code == 400
    assert proposta.status == "enviada"


def test_assinar_proposta_portal_rejeita_quando_proposta_mudou_apos_pedir_codigo() -> None:
    # Achado do Codex no PR #120: o código é amarrado ao conteúdo vigente
    # no momento do pedido -- se a proposta mudar antes da confirmação
    # (ex.: operador edita honorários), o código antigo não pode
    # continuar valendo pra uma versão diferente da que o cliente viu.
    proposta = _proposta_para_assinatura(validade_em=date(2099, 12, 31))
    codigo = _codigo_confirmacao("proposta", proposta)
    codigo.recurso_hash = "hash-de-uma-versao-anterior-da-proposta"
    session = FakeSession([FakeResult(scalar=proposta), FakeResult(scalar=codigo)])
    dados = AssinarComCodigoInput(codigo=_CODIGO_CONFIRMACAO_TESTE)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(assinar_proposta_portal(1, _request(), dados, _cliente(), session))
    assert exc_info.value.status_code == 409
    assert proposta.status == "enviada"


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

    # Achado médio da Fase 9 (21/09/2026): o envio agora roda como
    # BackgroundTask (depois da resposta, fechando o oráculo de tempo) --
    # aqui a task é executada manualmente pra continuar testando o log.
    background_tasks = BackgroundTasks()

    async def _fluxo() -> dict:
        resultado = await solicitar_recuperacao_portal(
            RecuperacaoSolicitacao(email="cliente@empresa.com.br"), _request(), session, background_tasks
        )
        await background_tasks()
        return resultado

    with caplog.at_level("ERROR", logger="ze_registra.portal_cliente"):
        resultado = asyncio.run(_fluxo())

    assert resultado == {"status": "ok", "mensagem": "Se a conta existir, a recuperação foi criada."}
    assert any("recuperação" in registro.message for registro in caplog.records)
    assert session.commits == 1


# --- Achado médio da auditoria do Portal do Cliente (Fase 9, 21/09/2026):
# nenhum rate limit em recuperacao/solicitar -- permitia mail-bombing de um
# cliente-alvo em escala (e escalava o oráculo de tempo corrigido acima). ---


def test_solicitar_recuperacao_tem_rate_limit() -> None:
    background_tasks = BackgroundTasks()
    dados = RecuperacaoSolicitacao(email="inexistente@empresa.com.br")
    for _ in range(5):
        session = FakeSession([FakeResult(scalar=None)])
        asyncio.run(solicitar_recuperacao_portal(dados, _request(), session, background_tasks))
    with pytest.raises(HTTPException) as exc_info:
        session = FakeSession([FakeResult(scalar=None)])
        asyncio.run(solicitar_recuperacao_portal(dados, _request(), session, background_tasks))
    assert exc_info.value.status_code == 429


def test_redefinir_acesso_portal_revoga_sessoes_e_limpa_cookies() -> None:
    from app.models import RecuperacaoClientePortal, SessaoClientePortal

    cliente = _cliente()
    registro = RecuperacaoClientePortal(
        id=1,
        cliente_id=cliente.id,
        token_hash=hash_token("token-valido-com-tamanho-suficiente"),
        expira_em=datetime(2099, 1, 1, tzinfo=UTC),
    )
    sessao_antiga = SessaoClientePortal(
        id=9, cliente_id=cliente.id, token_hash="hash-antigo", expira_em=datetime(2099, 1, 1, tzinfo=UTC)
    )
    # Ordem: SELECT do token (scalar), UPDATE ... RETURNING id (first), SELECT
    # das sessões a revogar. O cliente vem por session.get.
    session = FakeSession(
        [FakeResult(scalar=registro), FakeResult(itens=[(registro.id,)]), FakeResult(itens=[sessao_antiga])],
        objetos_get=[cliente],
    )
    dados = RecuperacaoRedefinicao(token="token-valido-com-tamanho-suficiente", nova_senha="Senha-Correta-123")
    response = Response()

    resultado = asyncio.run(redefinir_acesso_portal(dados, _request(), response, session))

    assert resultado["status"] == "ok"
    assert sessao_antiga.revogada_em is not None
    cookies = response.headers.getlist("set-cookie")
    assert any("zr_client_session=" in cookie for cookie in cookies)
    # Achado baixo da auditoria fina do Portal do Cliente (Fase 13.3,
    # 23/09/2026): o cookie CSRF ficava órfão aqui também.
    assert any("zr_portal_csrf=" in cookie for cookie in cookies)


def test_serializar_parcela_portal_deriva_atrasada_e_descricao_lancamento() -> None:
    # Achados P1/P2 da revisão do Codex na Fase 13.5 (23/09/2026): a
    # parcela persistida fica "aberta" mesmo vencida (o efetivo "atrasada"
    # é derivado, igual app.api.financeiro._serializar) e o cliente
    # precisa saber a qual lançamento/proposta cada parcela pertence.
    parcela_vencida = ParcelaFinanceira(
        id=1, lancamento_id=10, numero=1, vencimento=date(2020, 1, 1), valor=Decimal("500"),
        valor_pago=Decimal("0"), status="aberta", pago_em=None,
    )
    parcela_paga = ParcelaFinanceira(
        id=2, lancamento_id=10, numero=2, vencimento=date(2020, 1, 1), valor=Decimal("500"),
        valor_pago=Decimal("500"), status="paga", pago_em=date(2020, 1, 5),
    )
    parcela_futura = ParcelaFinanceira(
        id=3, lancamento_id=11, numero=1, vencimento=date(2099, 1, 1), valor=Decimal("300"),
        valor_pago=Decimal("0"), status="aberta", pago_em=None,
    )
    descricoes = {10: "Honorários — proposta PROP-1", 11: "GRU de depósito"}

    assert _serializar_parcela_portal(parcela_vencida, descricoes) == {
        "id": 1, "lancamento_id": 10, "descricao_lancamento": "Honorários — proposta PROP-1",
        "numero": 1, "vencimento": date(2020, 1, 1), "valor": Decimal("500"), "valor_pago": Decimal("0"),
        "status": "atrasada", "pago_em": None,
    }
    assert _serializar_parcela_portal(parcela_paga, descricoes)["status"] == "paga"
    assert _serializar_parcela_portal(parcela_futura, descricoes)["status"] == "aberta"
    assert _serializar_parcela_portal(parcela_futura, descricoes)["descricao_lancamento"] == "GRU de depósito"
    assert _serializar_parcela_portal(parcela_vencida, {})["descricao_lancamento"] is None


def test_redefinir_acesso_portal_tem_rate_limit() -> None:
    dados = RecuperacaoRedefinicao(token="token-que-nao-existe-em-lugar-nenhum", nova_senha="Senha-Correta-123")
    for _ in range(5):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(redefinir_acesso_portal(dados, _request(), Response(), FakeSession([FakeResult(scalar=None)])))
        assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(redefinir_acesso_portal(dados, _request(), Response(), FakeSession([FakeResult(scalar=None)])))
    assert exc_info.value.status_code == 429
