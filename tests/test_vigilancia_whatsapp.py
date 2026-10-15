import asyncio
from datetime import UTC, datetime

from app.models import (
    ClientePortal,
    ColidenciaVigilancia,
    HistoricoAlertaVigilancia,
    Lead,
    MensagemSaida,
    Organizacao,
    PreferenciaVigilancia,
)
from app.vigilancia import enfileirar_alerta, montar_texto_whatsapp, preparar_envio_whatsapp
from tests import test_mensageria as base
from tests.conftest import FakeResult, FakeSession
from tests.test_mensageria import _config  # noqa: F401  (fixture que liga o WhatsApp só nos testes)

# O alerta de vigilância aprovado por uma pessoa e destinado ao canal WhatsApp vira mensagem na fila. Só sai para
# telefones com consentimento de vigilância; sem ele fica "bloqueada" e o alerta "bloqueado".

AGORA = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
CHAVE = "vigilancia:5:whatsapp:v1"


def _colidencia() -> ColidenciaVigilancia:
    return ColidenciaVigilancia(
        id=5,
        organizacao_id=1,
        cliente_id=3,
        processo_id=8,
        score_risco=82.0,
        evidencias={"regra": "nome+nice", "marca_monitorada": "ACME", "processo": "912345678"},
        justificativa="Coincidência por nome e classe",
        status="aprovado",
        aprovado_por=1,
        aprovado_em=AGORA,
        falso_positivo=False,
    )


def _alerta(status: str = "pendente") -> HistoricoAlertaVigilancia:
    return HistoricoAlertaVigilancia(
        id=1,
        organizacao_id=1,
        colidencia_id=5,
        cliente_id=3,
        canal="whatsapp",
        status=status,
        idempotency_key=CHAVE,
        justificativa="j",
        detalhes={},
    )


def _objetos(telefone: str = "(11) 99999-8888", **lead_extras):
    cliente = ClientePortal(id=3, organizacao_id=1, lead_id=9, email="cliente@empresa.com.br", ativo=True)
    lead = Lead(id=9, nome="Maria Souza", telefone=telefone, marca="ACME", **lead_extras)
    org = Organizacao(id=1, nome="Escritório Exemplo", slug="exemplo", plano_id=1)
    return [cliente, lead, org]


def _preparar(sessao: FakeSession, alerta: HistoricoAlertaVigilancia) -> None:
    asyncio.run(preparar_envio_whatsapp(sessao, _colidencia(), alerta))


def _mensagens(sessao: FakeSession) -> list[MensagemSaida]:
    return [o for o in sessao.adicionados if isinstance(o, MensagemSaida)]


def test_texto_do_aviso_e_neutro_traz_a_marca_o_processo_e_a_saida() -> None:
    texto = montar_texto_whatsapp(
        nome="Maria Souza", escritorio="Escritório Exemplo", evidencias={"marca_monitorada": "ACME", "processo": "9123"}
    )
    assert texto.startswith("Olá, Maria!") and "Escritório Exemplo" in texto
    assert "ACME" in texto and "9123" in texto and texto.rstrip().endswith("responda SAIR.")
    sem_dados = montar_texto_whatsapp(nome=None, escritorio=None, evidencias={})
    assert sem_dados.startswith("Olá!") and "sua marca" in sem_dados


def test_com_consentimento_o_aviso_entra_na_fila_da_sessao_de_vigilancia() -> None:
    sessao = FakeSession([FakeResult(scalar=None), FakeResult(scalar=base._consentimento())], objetos_get=_objetos())
    alerta = _alerta()
    _preparar(sessao, alerta)
    (mensagem,) = _mensagens(sessao)
    assert mensagem.status == "pendente" and mensagem.finalidade == "vigilancia"
    assert mensagem.telefone == "5511999998888" and mensagem.chave_idempotencia == CHAVE
    assert (mensagem.titular_tipo, mensagem.titular_id) == ("cliente_portal", 3)
    assert "ACME" in mensagem.corpo and "Escritório Exemplo" in mensagem.corpo
    assert alerta.status == "pendente"


def test_sem_consentimento_a_mensagem_e_o_alerta_ficam_bloqueados() -> None:
    sessao = FakeSession([FakeResult(scalar=None), FakeResult(scalar=None)], objetos_get=_objetos())
    alerta = _alerta()
    _preparar(sessao, alerta)
    (mensagem,) = _mensagens(sessao)
    assert mensagem.status == "bloqueada" and mensagem.motivo_bloqueio == "sem_consentimento"
    assert alerta.status == "bloqueado" and alerta.detalhes["whatsapp"] == "sem_consentimento"


def test_cliente_que_pediu_para_sair_nao_recebe() -> None:
    sessao = FakeSession(
        [FakeResult(scalar=None), FakeResult(scalar=base._consentimento("revogado"))], objetos_get=_objetos()
    )
    alerta = _alerta()
    _preparar(sessao, alerta)
    assert _mensagens(sessao)[0].status == "bloqueada" and alerta.status == "bloqueado"


def test_telefone_sem_celular_valido_nao_gera_mensagem() -> None:
    sessao = FakeSession([], objetos_get=_objetos(telefone="(11) 3333-4444"))
    alerta = _alerta()
    _preparar(sessao, alerta)
    assert _mensagens(sessao) == [] and alerta.status == "sem_destino"


def test_cliente_anonimizado_ou_arquivado_nao_gera_mensagem() -> None:
    for extras in ({"anonimizado_em": AGORA}, {"arquivado_em": AGORA}):
        sessao = FakeSession([], objetos_get=_objetos(**extras))
        alerta = _alerta()
        _preparar(sessao, alerta)
        assert _mensagens(sessao) == [] and alerta.status == "sem_destino"


def test_enfileirar_alerta_no_canal_whatsapp_cria_o_alerta_e_a_mensagem() -> None:
    preferencia = PreferenciaVigilancia(organizacao_id=1, cliente_id=3, ativo=True, canais=["whatsapp"])
    sessao = FakeSession(
        [FakeResult(scalar=None), FakeResult(scalar=None), FakeResult(scalar=base._consentimento())],
        objetos_get=_objetos(),
    )
    alerta = asyncio.run(enfileirar_alerta(sessao, _colidencia(), preferencia, "whatsapp"))
    assert alerta.idempotency_key == CHAVE and alerta.canal == "whatsapp"
    assert _mensagens(sessao)[0].chave_idempotencia == CHAVE


def test_canais_que_nao_sao_whatsapp_nao_geram_mensagem() -> None:
    preferencia = PreferenciaVigilancia(organizacao_id=1, cliente_id=3, ativo=True, canais=["portal"])
    sessao = FakeSession([FakeResult(scalar=None)])
    asyncio.run(enfileirar_alerta(sessao, _colidencia(), preferencia, "portal"))
    assert _mensagens(sessao) == []


def test_consentimento_que_chega_depois_libera_a_mensagem_bloqueada() -> None:
    preferencia = PreferenciaVigilancia(organizacao_id=1, cliente_id=3, ativo=True, canais=["whatsapp"])
    bloqueada = MensagemSaida(
        id=1, organizacao_id=1, finalidade="vigilancia", telefone="5511999998888", status="bloqueada",
        motivo_bloqueio="sem_consentimento", chave_idempotencia=CHAVE, corpo="x", tentativas=0,
    )
    alerta = _alerta("bloqueado")
    sessao = FakeSession(
        [FakeResult(scalar=alerta), FakeResult(scalar=bloqueada), FakeResult(scalar=base._consentimento())],
        objetos_get=_objetos(),
    )
    asyncio.run(enfileirar_alerta(sessao, _colidencia(), preferencia, "whatsapp"))
    assert bloqueada.status == "pendente" and bloqueada.motivo_bloqueio is None
    assert alerta.status == "pendente"


def test_envio_da_mensagem_marca_o_alerta_como_enviado_no_historico() -> None:
    mensagem = base._mensagem(chave_idempotencia=CHAVE)
    sessao = base._fila(
        FakeResult(itens=[mensagem]), FakeResult(scalar=base._consentimento()), FakeResult(scalar=0), base._reserva()
    )
    assert base._processar(sessao, base._Provedor())["enviadas"] == 1
    atualizacoes = [c for c in sessao.executados if "UPDATE historico_alertas_vigilancia" in str(c)]
    assert len(atualizacoes) == 1
    assert "enviado" in atualizacoes[0].compile().params.values()


def test_mensagem_que_nao_e_de_alerta_de_vigilancia_nao_mexe_no_historico() -> None:
    mensagem = base._mensagem()  # chave "k", não é de alerta
    sessao = base._fila(
        FakeResult(itens=[mensagem]), FakeResult(scalar=base._consentimento()), FakeResult(scalar=0), base._reserva()
    )
    base._processar(sessao, base._Provedor())
    assert not [c for c in sessao.executados if "historico_alertas_vigilancia" in str(c)]


def test_falha_definitiva_marca_o_alerta_como_falhou() -> None:
    from app.mensageria import ErroEnvioPermanente

    mensagem = base._mensagem(chave_idempotencia=CHAVE)
    sessao = base._fila(
        FakeResult(itens=[mensagem]), FakeResult(scalar=base._consentimento()), FakeResult(scalar=0), base._reserva()
    )
    base._processar(sessao, base._Provedor(erro=ErroEnvioPermanente("número sem WhatsApp")))
    atualizacoes = [c for c in sessao.executados if "UPDATE historico_alertas_vigilancia" in str(c)]
    assert len(atualizacoes) == 1 and "falhou" in atualizacoes[0].compile().params.values()
