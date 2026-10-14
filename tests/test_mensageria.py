import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.mensageria as mensageria
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import ConsentimentoCanal, EventoAuditoria, MensagemSaida
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste

# Base do WhatsApp: consentimento, lista de supressão e fila. O gateway ainda não existe; o provedor
# usado aqui é um dublê que só registra as chamadas.

QUARTA_MEIO_DIA = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # 12:00 em São Paulo, dia útil
SABADO_MEIO_DIA = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
TELEFONE = "5511999998888"


class _Provedor:
    def __init__(self, erro: Exception | None = None) -> None:
        self.chamadas: list[tuple[str, str, str]] = []
        self.erro = erro

    async def enviar_texto(self, sessao: str, telefone: str, texto: str) -> str:
        if self.erro:
            raise self.erro
        self.chamadas.append((sessao, telefone, texto))
        return "msg-1"


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    configuracao = SimpleNamespace(
        whatsapp_enabled=True,
        whatsapp_limite_diario_vigilancia=200,
        whatsapp_limite_diario_prospeccao=20,
        whatsapp_horario_inicio=9,
        whatsapp_horario_fim=18,
        whatsapp_intervalo_min_segundos=60,
        whatsapp_intervalo_max_segundos=180,
        whatsapp_max_tentativas=3,
        whatsapp_pausa_falhas_por_hora=3,
    )
    monkeypatch.setattr(mensageria, "get_settings", lambda: configuracao)
    yield
    app.dependency_overrides.clear()


def _consentimento(status: str = "concedido", finalidade: str = "vigilancia") -> ConsentimentoCanal:
    return ConsentimentoCanal(
        id=1, organizacao_id=1, canal="whatsapp", finalidade=finalidade, telefone=TELEFONE, status=status, evidencia={}
    )


def _mensagem(finalidade: str = "vigilancia", **extras) -> MensagemSaida:
    mensagem = MensagemSaida(
        id=7,
        organizacao_id=1,
        canal="whatsapp",
        finalidade=finalidade,
        telefone=TELEFONE,
        corpo="Olá",
        status="pendente",
        tentativas=0,
        agendada_para=QUARTA_MEIO_DIA - timedelta(minutes=1),
        chave_idempotencia="k",
    )
    for campo, valor in extras.items():
        setattr(mensagem, campo, valor)
    return mensagem


# --- telefone e pedido de saída ---


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("(11) 99999-8888", "5511999998888"),
        ("+55 11 99999-8888", "5511999998888"),
        ("011999998888", "5511999998888"),
        ("55999998888", "5555999998888"),  # DDD 55 (RS) não pode ser confundido com o DDI
        ("1133334444", None),  # fixo não recebe WhatsApp
        ("11 89999-8888", None),
        ("05999998888", None),
        ("123", None),
        ("", None),
        (None, None),
    ],
)
def test_normalizar_telefone(entrada, esperado) -> None:
    assert mensageria.normalizar_telefone(entrada) == esperado


@pytest.mark.parametrize("texto", ["SAIR", " sair! ", "Parar", "pare", "Cancelar", "STOP", "não quero receber"])
def test_pedidos_de_saida_sao_reconhecidos(texto: str) -> None:
    assert mensageria.eh_pedido_de_saida(texto)


@pytest.mark.parametrize("texto", ["", "quero sair para almoçar", "obrigado", "sim", None])
def test_conversa_comum_nao_e_pedido_de_saida(texto) -> None:
    assert not mensageria.eh_pedido_de_saida(texto)


# --- consentimento ---


def _registrar(sessao: FakeSession, **extras):
    dados = dict(
        organizacao_id=1,
        finalidade="vigilancia",
        telefone="(11) 99999-8888",
        origem="portal",
        registrado_por="op@teste.local",
        evidencia={"texto": "Aceito receber avisos", "em": "2026-10-14"},
    )
    dados.update(extras)
    return asyncio.run(mensageria.registrar_consentimento(sessao, **dados))


def test_registrar_consentimento_grava_evidencia_e_normaliza_o_telefone() -> None:
    sessao = FakeSession([])
    registro = _registrar(sessao)
    assert registro.telefone == TELEFONE and registro.status == "concedido"
    assert registro.evidencia["texto"] == "Aceito receber avisos"
    assert registro.concedido_em is not None and registro.revogado_em is None
    assert registro in sessao.adicionados


def test_consentimento_sem_evidencia_e_recusado() -> None:
    with pytest.raises(ValueError, match="evidência"):
        _registrar(FakeSession([]), evidencia={})


def test_vigilancia_nao_aceita_legitimo_interesse() -> None:
    with pytest.raises(ValueError, match="consentimento"):
        _registrar(FakeSession([]), base_legal="legitimo_interesse")


def test_prospeccao_por_legitimo_interesse_nao_exige_evidencia() -> None:
    registro = _registrar(FakeSession([]), finalidade="prospeccao", base_legal="legitimo_interesse", evidencia={})
    assert registro.base_legal == "legitimo_interesse"


def test_quem_pediu_para_sair_nao_volta_por_decisao_interna() -> None:
    sessao = FakeSession([FakeResult(scalar=_consentimento("revogado", "prospeccao"))])
    with pytest.raises(ValueError, match="supressão"):
        _registrar(sessao, finalidade="prospeccao", base_legal="legitimo_interesse", evidencia={})


def test_novo_consentimento_do_proprio_titular_reativa_o_numero_suprimido() -> None:
    suprimido = _consentimento("revogado")
    registro = _registrar(FakeSession([FakeResult(scalar=suprimido)]))
    assert registro is suprimido and registro.status == "concedido" and registro.revogado_em is None


def test_telefone_invalido_e_recusado() -> None:
    with pytest.raises(ValueError, match="Telefone"):
        _registrar(FakeSession([]), telefone="1133334444")


def test_revogar_cria_o_registro_de_supressao_e_cancela_as_pendentes() -> None:
    sessao = FakeSession([])
    registro = asyncio.run(
        mensageria.revogar_consentimento(
            sessao,
            organizacao_id=1,
            finalidade="prospeccao",
            telefone=TELEFONE,
            origem="atendimento",
            registrado_por="op@teste.local",
        )
    )
    assert registro.status == "revogado" and registro.revogado_em is not None
    assert any("UPDATE mensagens_saida" in str(c) for c in sessao.executados)


def test_resposta_sair_revoga_todas_as_finalidades() -> None:
    sessao = FakeSession([])
    assert asyncio.run(
        mensageria.processar_pedido_de_saida(sessao, organizacao_id=1, telefone=TELEFONE, texto="SAIR")
    )
    revogados = [o for o in sessao.adicionados if isinstance(o, ConsentimentoCanal)]
    assert sorted(r.finalidade for r in revogados) == ["andamento", "prospeccao", "vigilancia"]
    assert all(r.status == "revogado" for r in revogados)
    assert not asyncio.run(
        mensageria.processar_pedido_de_saida(FakeSession([]), organizacao_id=1, telefone=TELEFONE, texto="obrigado")
    )


# --- fila: enfileirar ---


def _enfileirar(sessao: FakeSession, **extras):
    dados = dict(
        organizacao_id=1, finalidade="vigilancia", telefone=TELEFONE, corpo="Novo andamento", chave_idempotencia="a1"
    )
    dados.update(extras)
    return asyncio.run(mensageria.enfileirar_mensagem(sessao, **dados))


def test_vigilancia_sem_consentimento_fica_bloqueada_e_registrada() -> None:
    # 1ª consulta: chave de idempotência (nada); 2ª: consentimento (nada)
    mensagem = _enfileirar(FakeSession([]))
    assert mensagem.status == "bloqueada" and mensagem.motivo_bloqueio == "sem_consentimento"


def test_vigilancia_com_consentimento_entra_na_fila() -> None:
    mensagem = _enfileirar(FakeSession([FakeResult(scalar=None), FakeResult(scalar=_consentimento())]))
    assert mensagem.status == "pendente" and mensagem.motivo_bloqueio is None


def test_consentimento_revogado_bloqueia_mesmo_na_prospeccao() -> None:
    sessao = FakeSession([FakeResult(scalar=None), FakeResult(scalar=_consentimento("revogado", "prospeccao"))])
    mensagem = _enfileirar(sessao, finalidade="prospeccao")
    assert mensagem.status == "bloqueada" and mensagem.motivo_bloqueio == "consentimento_revogado"


def test_prospeccao_sempre_leva_a_instrucao_para_sair() -> None:
    mensagem = _enfileirar(FakeSession([]), finalidade="prospeccao", corpo="Olá, somos o escritório X.")
    assert mensagem.status == "pendente"
    assert mensagem.corpo.endswith(mensageria.AVISO_SAIDA)
    de_novo = _enfileirar(FakeSession([]), finalidade="prospeccao", corpo="Responda SAIR para parar.")
    assert de_novo.corpo == "Responda SAIR para parar."


def test_mesma_chave_devolve_a_mensagem_ja_existente() -> None:
    existente = _mensagem()
    sessao = FakeSession([FakeResult(scalar=existente)])
    assert _enfileirar(sessao) is existente
    assert sessao.adicionados == []


@pytest.mark.parametrize("extras", [{"telefone": "123"}, {"corpo": "  "}, {"finalidade": "outra"}])
def test_enfileirar_recusa_dados_invalidos(extras) -> None:
    with pytest.raises(ValueError):
        _enfileirar(FakeSession([]), **extras)


# --- fila: processar ---


def _processar(sessao: FakeSession, provedor, agora=QUARTA_MEIO_DIA):
    return asyncio.run(mensageria.processar_fila(sessao, provedor, agora=agora, sortear=lambda a, b: a))


def test_desligada_ou_sem_provedor_nada_sai(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _processar(FakeSession([]), None)["motivo"] == "sem_provedor"
    ligado = mensageria.get_settings()
    monkeypatch.setattr(mensageria, "get_settings", lambda: SimpleNamespace(**{**vars(ligado), "whatsapp_enabled": False}))
    provedor = _Provedor()
    assert _processar(FakeSession([]), provedor)["motivo"] == "desligado"
    assert provedor.chamadas == []


def test_vigilancia_com_consentimento_e_enviada_pela_sessao_de_vigilancia() -> None:
    mensagem = _mensagem()
    sessao = FakeSession(
        [FakeResult(itens=[mensagem]), FakeResult(scalar=_consentimento()), FakeResult(scalar=0)]
    )
    provedor = _Provedor()
    assert _processar(sessao, provedor)["enviadas"] == 1
    assert provedor.chamadas == [("vigilancia", TELEFONE, "Olá")]
    assert mensagem.status == "enviada" and mensagem.enviada_em == QUARTA_MEIO_DIA
    assert mensagem.provedor_mensagem_id == "msg-1" and mensagem.tentativas == 1


def test_consentimento_revogado_depois_de_enfileirar_bloqueia_no_envio() -> None:
    mensagem = _mensagem()
    sessao = FakeSession([FakeResult(itens=[mensagem]), FakeResult(scalar=_consentimento("revogado"))])
    provedor = _Provedor()
    assert _processar(sessao, provedor)["bloqueadas"] == 1
    assert mensagem.status == "bloqueada" and provedor.chamadas == []


def test_limite_diario_segura_o_que_passou_do_teto() -> None:
    mensagem = _mensagem()
    sessao = FakeSession([FakeResult(itens=[mensagem]), FakeResult(scalar=_consentimento()), FakeResult(scalar=200)])
    provedor = _Provedor()
    assert _processar(sessao, provedor)["enviadas"] == 0
    assert mensagem.status == "pendente" and provedor.chamadas == []


def _fila_prospeccao(mensagem, *, enviadas=0, falhas=0, ultimo=None) -> FakeSession:
    return FakeSession(
        [
            FakeResult(itens=[mensagem]),
            FakeResult(scalar=None),  # sem registro de supressão
            FakeResult(scalar=enviadas),
            FakeResult(scalar=falhas),
            FakeResult(scalar=ultimo),
        ]
    )


def test_prospeccao_envia_em_dia_util_pela_sessao_de_prospeccao() -> None:
    mensagem = _mensagem("prospeccao")
    provedor = _Provedor()
    assert _processar(_fila_prospeccao(mensagem), provedor)["enviadas"] == 1
    assert provedor.chamadas[0][0] == "prospeccao"


def test_prospeccao_nao_sai_no_fim_de_semana() -> None:
    mensagem = _mensagem("prospeccao", agendada_para=SABADO_MEIO_DIA - timedelta(minutes=1))
    provedor = _Provedor()
    sessao = FakeSession([FakeResult(itens=[mensagem]), FakeResult(scalar=None), FakeResult(scalar=0)])
    assert _processar(sessao, provedor, agora=SABADO_MEIO_DIA)["enviadas"] == 0
    assert provedor.chamadas == []


def test_prospeccao_pausa_sozinha_quando_as_falhas_aumentam() -> None:
    mensagem = _mensagem("prospeccao")
    provedor = _Provedor()
    assert _processar(_fila_prospeccao(mensagem, falhas=3), provedor)["enviadas"] == 0
    assert provedor.chamadas == []


def test_prospeccao_respeita_a_espera_entre_mensagens() -> None:
    mensagem = _mensagem("prospeccao")
    provedor = _Provedor()
    recente = QUARTA_MEIO_DIA - timedelta(seconds=30)
    assert _processar(_fila_prospeccao(mensagem, ultimo=recente), provedor)["enviadas"] == 0
    antigo = QUARTA_MEIO_DIA - timedelta(minutes=10)
    assert _processar(_fila_prospeccao(_mensagem("prospeccao"), ultimo=antigo), provedor)["enviadas"] == 1


def test_falha_de_envio_reagenda_e_na_terceira_vez_desiste() -> None:
    mensagem = _mensagem()
    for esperado_status, esperado_tentativas in (("pendente", 1), ("pendente", 2), ("falhou", 3)):
        sessao = FakeSession(
            [FakeResult(itens=[mensagem]), FakeResult(scalar=_consentimento()), FakeResult(scalar=0)]
        )
        resultado = _processar(sessao, _Provedor(erro=RuntimeError("gateway fora do ar")))
        assert resultado["falhas"] == 1
        assert (mensagem.status, mensagem.tentativas) == (esperado_status, esperado_tentativas)
        mensagem.agendada_para = QUARTA_MEIO_DIA - timedelta(minutes=1)
    assert mensagem.ultimo_erro == "gateway fora do ar"


# --- API de administração ---


def _cliente(sessao: FakeSession, usuario=None) -> TestClient:
    async def _dep():
        yield sessao

    app.dependency_overrides[get_session] = _dep
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        replace(usuario or usuario_teste(), csrf_hash=hash_token("tok"))
    )
    return TestClient(app, raise_server_exceptions=False, headers={"X-CSRF-Token": "tok"})


def test_api_registra_consentimento_e_audita() -> None:
    sessao = FakeSession([])
    resposta = _cliente(sessao).post(
        "/v1/admin/mensageria/consentimentos",
        json={
            "finalidade": "vigilancia",
            "telefone": "(11) 99999-8888",
            "origem": "portal",
            "evidencia": {"texto": "Aceito"},
        },
    )
    assert resposta.status_code == 201
    assert resposta.json()["telefone"] == TELEFONE and resposta.json()["status"] == "concedido"
    assert sessao.commits == 1
    assert any(isinstance(o, EventoAuditoria) and o.acao == "CONSENT_REGISTRAR" for o in sessao.adicionados)


def test_api_recusa_telefone_invalido_e_sem_evidencia() -> None:
    cliente = _cliente(FakeSession([]))
    base = {"finalidade": "vigilancia", "origem": "portal", "evidencia": {"texto": "Aceito"}}
    assert cliente.post("/v1/admin/mensageria/consentimentos", json={**base, "telefone": "1133334444"}).status_code == 422
    sem_evidencia = {**base, "telefone": "11999998888", "evidencia": {}}
    assert cliente.post("/v1/admin/mensageria/consentimentos", json=sem_evidencia).status_code == 422


def test_api_mascara_o_telefone_para_quem_nao_ve_contatos() -> None:
    operador = usuario_teste(perfil="operador", permissoes={"crm.view"})
    sessao = FakeSession([FakeResult(itens=[_consentimento()])])
    resposta = _cliente(sessao, operador).get("/v1/admin/mensageria/consentimentos")
    assert resposta.status_code == 200
    assert resposta.json()[0]["telefone"] == "••••••8888"


def test_api_so_cancela_mensagem_pendente() -> None:
    enviada = _mensagem(status="enviada")
    resposta = _cliente(FakeSession([FakeResult(scalar=enviada)])).post("/v1/admin/mensageria/fila/7/cancelar")
    assert resposta.status_code == 409
    pendente = _mensagem()
    resposta = _cliente(FakeSession([FakeResult(scalar=pendente)])).post("/v1/admin/mensageria/fila/7/cancelar")
    assert resposta.status_code == 200 and pendente.status == "cancelada"
    assert _cliente(FakeSession([])).post("/v1/admin/mensageria/fila/7/cancelar").status_code == 404


def test_api_exige_permissao_de_gestao_para_alterar() -> None:
    so_leitura = usuario_teste(perfil="operador", permissoes={"crm.view"})
    resposta = _cliente(FakeSession([]), so_leitura).post(
        "/v1/admin/mensageria/consentimentos/revogar", json={"finalidade": "prospeccao", "telefone": TELEFONE}
    )
    assert resposta.status_code == 403
