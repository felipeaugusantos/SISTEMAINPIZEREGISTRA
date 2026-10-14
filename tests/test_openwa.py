import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.openwa_webhook as modulo_webhook
import app.mensageria as mensageria
import app.openwa as openwa
from app.cli.openwa_configurar import EVENTOS, configurar
from app.database import get_session
from app.main import app
from app.mensageria import ErroEnvioPermanente
from app.models import AlertaSistema, ConsentimentoCanal, MensagemSaida
from tests.conftest import FakeResult, FakeSession

# Integração com o gateway OpenWA: cliente de envio, assinatura e receptor de webhook, comando de configuração
# e o arquivo de composição do gateway. Nenhum teste fala com um OpenWA de verdade.

SEGREDO = "segredo-do-webhook-com-mais-de-16-caracteres"
TELEFONE = "5511999998888"
AGORA = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
RAIZ = Path(".")


@pytest.fixture(autouse=True)
def _limpar_overrides() -> None:
    yield
    app.dependency_overrides.clear()


# --- assinatura ---


def _assinar(corpo: bytes, segredo: str = SEGREDO) -> str:
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


def test_assinatura_valida_confere_os_bytes_exatos_do_corpo() -> None:
    corpo = b'{"event":"message.received"}'
    assert openwa.assinatura_valida(corpo, _assinar(corpo), SEGREDO)
    assert not openwa.assinatura_valida(corpo + b" ", _assinar(corpo), SEGREDO)  # corpo alterado
    assert not openwa.assinatura_valida(corpo, _assinar(corpo, "outro-segredo-qualquer-123"), SEGREDO)
    assert not openwa.assinatura_valida(corpo, "sha256=" + "0" * 64, SEGREDO)
    assert not openwa.assinatura_valida(corpo, None, SEGREDO)
    assert not openwa.assinatura_valida(corpo, "", SEGREDO)
    assert not openwa.assinatura_valida(corpo, _assinar(corpo), "")  # sem segredo configurado nada vale


# --- cliente de envio ---


def _gateway(respostas: dict, chamadas: list[httpx.Request]) -> httpx.MockTransport:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        chamadas.append(requisicao)
        chave = (requisicao.method, requisicao.url.path)
        resposta = respostas[chave]
        if isinstance(resposta, list):  # várias respostas em sequência para o mesmo endereço
            resposta = resposta.pop(0)
        return resposta

    return httpx.MockTransport(tratar)


def _provedor(respostas: dict, chamadas: list[httpx.Request]) -> openwa.ProvedorOpenWA:
    return openwa.ProvedorOpenWA(
        "http://openwa:2785",
        "chave-do-gateway",
        {"vigilancia": "vigilancia", "prospeccao": "prospeccao"},
        transport=_gateway(respostas, chamadas),
    )


SESSOES = httpx.Response(200, json=[{"id": "uuid-1", "name": "vigilancia"}, {"id": "uuid-2", "name": "prospeccao"}])
ENVIO = ("POST", "/api/sessions/uuid-1/messages/send-text")


def _enviar(provedor: openwa.ProvedorOpenWA, sessao: str = "vigilancia") -> str:
    return asyncio.run(provedor.enviar_texto(sessao, TELEFONE, "Olá", "chave-1"))


def test_envio_usa_o_id_da_sessao_a_chave_do_gateway_e_a_idempotencia() -> None:
    chamadas: list[httpx.Request] = []
    provedor = _provedor(
        {("GET", "/api/sessions"): SESSOES, ENVIO: httpx.Response(201, json={"messageId": "m-1", "timestamp": 1})},
        chamadas,
    )
    assert _enviar(provedor) == "m-1"
    envio = chamadas[-1]
    assert envio.headers["X-API-Key"] == "chave-do-gateway"
    assert envio.headers["Idempotency-Key"] == "chave-1"
    assert json.loads(envio.content) == {"chatId": f"{TELEFONE}@c.us", "text": "Olá"}


def test_o_id_da_sessao_fica_em_cache_entre_envios() -> None:
    chamadas: list[httpx.Request] = []
    provedor = _provedor(
        {("GET", "/api/sessions"): SESSOES, ENVIO: httpx.Response(201, json={"messageId": "m-1", "timestamp": 1})},
        chamadas,
    )
    _enviar(provedor)
    _enviar(provedor)
    assert [c.url.path for c in chamadas].count("/api/sessions") == 1


def test_sessao_recriada_no_gateway_renova_o_id_e_tenta_de_novo() -> None:
    chamadas: list[httpx.Request] = []
    novas = httpx.Response(200, json=[{"id": "uuid-9", "name": "vigilancia"}])
    provedor = _provedor(
        {
            ("GET", "/api/sessions"): [SESSOES, novas],
            ENVIO: httpx.Response(404, json={"message": "Session not found"}),
            ("POST", "/api/sessions/uuid-9/messages/send-text"): httpx.Response(
                201, json={"messageId": "m-9", "timestamp": 1}
            ),
        },
        chamadas,
    )
    assert _enviar(provedor) == "m-9"


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_recusa_definitiva_do_gateway_nao_e_repetida(status: int) -> None:
    provedor = _provedor(
        {("GET", "/api/sessions"): SESSOES, ENVIO: httpx.Response(status, json={"message": "recusado"})}, []
    )
    with pytest.raises(ErroEnvioPermanente, match=str(status)):
        _enviar(provedor)


@pytest.mark.parametrize("status", [409, 500, 503])
def test_sessao_sem_conexao_ou_gateway_fora_e_temporario(status: int) -> None:
    provedor = _provedor(
        {("GET", "/api/sessions"): SESSOES, ENVIO: httpx.Response(status, json={"message": "tente depois"})}, []
    )
    with pytest.raises(RuntimeError) as erro:
        _enviar(provedor)
    assert not isinstance(erro.value, ErroEnvioPermanente)


def test_sessao_inexistente_no_gateway_e_definitiva() -> None:
    provedor = _provedor({("GET", "/api/sessions"): httpx.Response(200, json=[])}, [])
    with pytest.raises(ErroEnvioPermanente, match="openwa_configurar"):
        _enviar(provedor)


def test_provedor_so_existe_com_url_e_chave(monkeypatch: pytest.MonkeyPatch) -> None:
    base = dict(
        openwa_sessao_vigilancia="vigilancia", openwa_sessao_prospeccao="prospeccao", openwa_timeout_segundos=15
    )
    monkeypatch.setattr(openwa, "get_settings", lambda: SimpleNamespace(openwa_base_url="", openwa_api_key="x", **base))
    assert openwa.criar_provedor() is None
    monkeypatch.setattr(
        openwa, "get_settings", lambda: SimpleNamespace(openwa_base_url="http://openwa:2785", openwa_api_key="", **base)
    )
    assert openwa.criar_provedor() is None
    monkeypatch.setattr(
        openwa, "get_settings", lambda: SimpleNamespace(openwa_base_url="http://openwa:2785", openwa_api_key="k", **base)
    )
    assert isinstance(openwa.criar_provedor(), openwa.ProvedorOpenWA)


# --- fila: erro definitivo ---


def test_erro_definitivo_do_provedor_derruba_a_mensagem_sem_repetir(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import test_mensageria as base

    monkeypatch.setattr(
        mensageria,
        "get_settings",
        lambda: SimpleNamespace(
            whatsapp_enabled=True,
            whatsapp_limite_diario_vigilancia=200,
            whatsapp_limite_diario_prospeccao=20,
            whatsapp_horario_inicio=9,
            whatsapp_horario_fim=18,
            whatsapp_intervalo_min_segundos=60,
            whatsapp_intervalo_max_segundos=180,
            whatsapp_max_tentativas=3,
            whatsapp_pausa_falhas_por_hora=3,
        ),
    )
    mensagem = base._mensagem()
    sessao = base._fila(
        FakeResult(itens=[mensagem]), FakeResult(scalar=base._consentimento()), FakeResult(scalar=0), base._reserva()
    )
    resultado = base._processar(sessao, base._Provedor(erro=ErroEnvioPermanente("número sem WhatsApp")))
    assert resultado["falhas"] == 1
    assert mensagem.status == "falhou" and mensagem.tentativas == 1  # não esperou as 3 tentativas


# --- eventos do gateway ---


def _evento(sessao: FakeSession, evento: str, dados: dict, logica: str | None = None) -> str:
    return asyncio.run(
        mensageria.processar_evento_gateway(
            sessao, organizacao_id=1, evento=evento, dados=dados, sessao_logica=logica, agora=AGORA
        )
    )


def test_resposta_sair_revoga_o_telefone_de_quem_escreveu() -> None:
    sessao = FakeSession([])
    dados = {"from": f"{TELEFONE}@c.us", "body": "SAIR", "isGroup": False, "fromMe": False}
    assert _evento(sessao, "message.received", dados) == "saida"
    revogados = [o for o in sessao.adicionados if isinstance(o, ConsentimentoCanal)]
    assert len(revogados) == 3 and all(r.status == "revogado" and r.telefone == TELEFONE for r in revogados)


def test_remetente_com_id_privado_usa_o_telefone_resolvido() -> None:
    sessao = FakeSession([])
    dados = {"from": "123456@lid", "senderPhone": TELEFONE, "body": "pare", "isGroup": False, "fromMe": False}
    assert _evento(sessao, "message.received", dados) == "saida"
    sem_telefone = {"from": "123456@lid", "body": "pare", "isGroup": False, "fromMe": False}
    assert _evento(FakeSession([]), "message.received", sem_telefone) == "ignorado"


@pytest.mark.parametrize(
    "dados",
    [
        {"from": f"{TELEFONE}@c.us", "body": "SAIR", "isGroup": True},
        {"from": f"{TELEFONE}@c.us", "body": "SAIR", "fromMe": True},
        {"from": f"{TELEFONE}@c.us", "body": "obrigado, até logo"},
        {"from": "120363@g.us", "body": "sair"},
    ],
)
def test_so_pedido_de_saida_de_conversa_individual_revoga(dados: dict) -> None:
    sessao = FakeSession([])
    assert _evento(sessao, "message.received", dados) == "ignorado"
    assert sessao.adicionados == []


def test_recibos_de_entrega_e_leitura_atualizam_a_mensagem() -> None:
    mensagem = MensagemSaida(id=7, status="enviada", provedor_mensagem_id="m-1")
    assert _evento(FakeSession([FakeResult(scalar=mensagem)]), "message.ack", {"messageId": "m-1", "status": "delivered"}) == "recibo"
    assert mensagem.entregue_em == AGORA and mensagem.lida_em is None
    posterior = AGORA + timedelta(minutes=3)
    asyncio.run(
        mensageria.processar_evento_gateway(
            FakeSession([FakeResult(scalar=mensagem)]),
            organizacao_id=1,
            evento="message.ack",
            dados={"messageId": "m-1", "status": "read"},
            agora=posterior,
        )
    )
    assert mensagem.entregue_em == AGORA and mensagem.lida_em == posterior  # a 1ª entrega não é sobrescrita


def test_falha_de_entrega_no_whatsapp_marca_a_mensagem() -> None:
    mensagem = MensagemSaida(id=7, status="enviada", provedor_mensagem_id="m-1")
    assert _evento(FakeSession([FakeResult(scalar=mensagem)]), "message.failed", {"messageId": "m-1"}) == "recibo"
    assert mensagem.status == "falhou" and mensagem.ultima_falha_em == AGORA


def test_recibo_de_mensagem_desconhecida_ou_estado_intermediario_e_ignorado() -> None:
    assert _evento(FakeSession([FakeResult(scalar=None)]), "message.ack", {"messageId": "?", "status": "read"}) == "ignorado"
    assert _evento(FakeSession([]), "message.ack", {"messageId": "m-1", "status": "sent"}) == "ignorado"


def test_restricao_do_numero_alerta_e_segura_as_pendentes_da_sessao() -> None:
    sessao = FakeSession([])
    assert _evento(sessao, "session.restriction", {"active": True, "kind": "send", "code": "X"}, "prospeccao") == "alerta"
    alerta = next(o for o in sessao.adicionados if isinstance(o, AlertaSistema))
    assert alerta.severidade == "critico" and alerta.codigo == "WHATSAPP_RESTRICAO"
    segurar = [c for c in sessao.executados if "UPDATE mensagens_saida" in str(c)]
    assert len(segurar) == 1
    assert AGORA + mensageria.PAUSA_POR_RESTRICAO in segurar[0].compile().params.values()


def test_alerta_repetido_em_poucos_minutos_nao_e_duplicado() -> None:
    sessao = FakeSession([FakeResult(scalar=1)])  # já existe um alerta igual na janela
    assert _evento(sessao, "session.disconnected", {"reason": "conflict"}, "vigilancia") == "alerta"
    assert not [o for o in sessao.adicionados if isinstance(o, AlertaSistema)]


# --- webhook HTTP ---


def _postar(corpo: dict | bytes, *, assinar: bool = True, sessao: FakeSession | None = None, segredo: str = SEGREDO):
    bruto = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
    fake = sessao or FakeSession([])

    async def _dep():
        yield fake

    app.dependency_overrides[get_session] = _dep
    cabecalhos = {"Content-Type": "application/json", "X-Zr-Sessao": "vigilancia"}
    if assinar:
        cabecalhos["X-OpenWA-Signature"] = _assinar(bruto)
    original = modulo_webhook.get_settings
    modulo_webhook.get_settings = lambda: SimpleNamespace(openwa_webhook_secret=segredo, default_organization_id=1)
    try:
        resposta = TestClient(app, raise_server_exceptions=False).post(
            "/v1/webhooks/openwa", content=bruto, headers=cabecalhos
        )
    finally:
        modulo_webhook.get_settings = original
    return resposta, fake


def test_webhook_sem_segredo_configurado_recusa_tudo() -> None:
    resposta, _ = _postar({"event": "message.received"}, segredo="")
    assert resposta.status_code == 503


def test_webhook_sem_assinatura_ou_com_assinatura_errada_e_recusado() -> None:
    resposta, sessao = _postar({"event": "message.received", "data": {}}, assinar=False)
    assert resposta.status_code == 401 and sessao.commits == 0
    bruto = json.dumps({"event": "message.received"}).encode()

    async def _dep():
        yield FakeSession([])

    app.dependency_overrides[get_session] = _dep
    original = modulo_webhook.get_settings
    modulo_webhook.get_settings = lambda: SimpleNamespace(openwa_webhook_secret=SEGREDO, default_organization_id=1)
    try:
        errada = TestClient(app, raise_server_exceptions=False).post(
            "/v1/webhooks/openwa", content=bruto, headers={"X-OpenWA-Signature": _assinar(bruto, "outro-segredo-123456")}
        )
    finally:
        modulo_webhook.get_settings = original
    assert errada.status_code == 401


def test_webhook_assinado_aplica_o_pedido_de_saida_e_confirma() -> None:
    carga = {
        "event": "message.received",
        "sessionId": "uuid-1",
        "data": {"from": f"{TELEFONE}@c.us", "body": "SAIR", "isGroup": False, "fromMe": False},
    }
    resposta, sessao = _postar(carga)
    assert resposta.status_code == 200 and resposta.json() == {"ok": True, "acao": "saida"}
    assert sessao.commits == 1
    assert len([o for o in sessao.adicionados if isinstance(o, ConsentimentoCanal)]) == 3


def test_webhook_com_json_invalido_ou_nao_objeto_da_400() -> None:
    assert _postar(b"isto nao e json")[0].status_code == 400
    assert _postar(b"[1, 2]")[0].status_code == 400


def test_webhook_de_evento_desconhecido_e_aceito_sem_efeito() -> None:
    resposta, sessao = _postar({"event": "group.update", "data": {"groupId": "1@g.us"}})
    assert resposta.status_code == 200 and resposta.json()["acao"] == "ignorado"
    assert sessao.adicionados == []


# --- comando de configuração ---


def _gateway_de_configuracao(existentes: list[dict], webhooks: list[dict]) -> tuple[httpx.AsyncClient, list]:
    registro: list[tuple[str, str, object]] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content else None
        registro.append((req.method, req.url.path, corpo))
        if (req.method, req.url.path) == ("GET", "/api/sessions"):
            return httpx.Response(200, json=existentes)
        if (req.method, req.url.path) == ("POST", "/api/sessions"):
            return httpx.Response(201, json={"id": f"id-{corpo['name']}", "name": corpo["name"], "status": "created"})
        if req.method == "GET" and req.url.path.endswith("/webhooks"):
            return httpx.Response(200, json=webhooks)
        return httpx.Response(201, json={})

    cliente = httpx.AsyncClient(base_url="http://openwa:2785", transport=httpx.MockTransport(tratar))
    return cliente, registro


def test_configurar_cria_sessoes_inicia_e_registra_webhooks_com_o_cabecalho_da_sessao() -> None:
    cliente, registro = _gateway_de_configuracao([], [])
    relato = asyncio.run(configurar(cliente, {"vigilancia": "vigilancia", "prospeccao": "prospeccao"}, SEGREDO))
    assert any("criada" in linha for linha in relato) and any("QR" in linha for linha in relato)
    webhooks = [c for c in registro if c[0] == "POST" and c[1].endswith("/webhooks")]
    assert len(webhooks) == 2
    for _, _, corpo in webhooks:
        assert corpo["secret"] == SEGREDO and corpo["events"] == EVENTOS
        assert corpo["url"] == "http://api:8000/v1/webhooks/openwa"
    assert [c[2]["headers"] for c in webhooks] == [{"X-Zr-Sessao": "vigilancia"}, {"X-Zr-Sessao": "prospeccao"}]


def test_configurar_e_idempotente() -> None:
    existentes = [
        {"id": "a", "name": "vigilancia", "status": "ready"},
        {"id": "b", "name": "prospeccao", "status": "ready"},
    ]
    cliente, registro = _gateway_de_configuracao(existentes, [{"url": "http://api:8000/v1/webhooks/openwa"}])
    relato = asyncio.run(configurar(cliente, {"vigilancia": "vigilancia", "prospeccao": "prospeccao"}, SEGREDO))
    assert not [c for c in registro if c[0] == "POST"]
    assert all("já" in linha for linha in relato)


def test_eventos_do_webhook_cobrem_o_que_o_painel_trata() -> None:
    for evento in ("message.received", "message.ack", "message.failed", "session.restriction", "session.disconnected"):
        assert evento in EVENTOS


# --- arquivo de composição do gateway ---


def test_composicao_do_gateway_e_restrita_e_separada_do_deploy() -> None:
    bruto = (RAIZ / "compose.openwa.yaml").read_text(encoding="utf-8")
    texto = "\n".join(linha for linha in bruto.splitlines() if not linha.lstrip().startswith("#"))
    assert "ghcr.io/rmyndharis/openwa:v" in texto and ":latest" not in texto  # versão fixada
    assert "docker.sock" not in texto  # sem acesso ao Docker do servidor
    assert '"127.0.0.1:2785:2785"' in texto  # porta só no próprio servidor
    assert "cap_drop:" in texto and "- ALL" in texto and "read_only: true" in texto
    assert "no-new-privileges:true" in texto
    assert "OPENWA_API_KEY:?" in texto  # sem chave mestra o gateway não sobe aberto
    # O deploy normal não conhece o gateway: ele nunca sobe nem derruba o serviço.
    assert "\n  openwa:" not in (RAIZ / "compose.yaml").read_text(encoding="utf-8")
    assert "compose.openwa" not in (RAIZ / "docker" / "deploy.sh").read_text(encoding="utf-8")
    assert "compose.openwa" not in (RAIZ / "docker" / "rollback.sh").read_text(encoding="utf-8")
