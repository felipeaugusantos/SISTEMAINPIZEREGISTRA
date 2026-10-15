import asyncio
from datetime import UTC, datetime, timedelta

from app.cadencia_email import pausar_envios_pendentes_do_lead
from app.cadencia_whatsapp import agendar_whatsapp_do_passo, montar_texto_cadencia, motivo_sem_envio
from app.models import Cadencia, CadenciaPasso, Lead, MensagemSaida
from tests import test_mensageria as base
from tests.conftest import FakeResult, FakeSession
from tests.test_mensageria import _config  # noqa: F401  (fixture que liga o WhatsApp só nos testes)

# Passo de cadência com canal WhatsApp vira mensagem na fila de PROSPECÇÃO: só sai se o telefone não estiver
# suprimido, em dia útil e horário comercial; lead que responde, sai ou é descadastrado para de receber.

AGORA = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
DESCRICAO = "Vimos que sua marca ainda não foi registrada no INPI."


def _lead(**extras) -> Lead:
    lead = Lead(id=9, organizacao_id=1, nome="Carlos Silva", telefone="(11) 99999-8888", marca="ACME")
    for campo, valor in extras.items():
        setattr(lead, campo, valor)
    return lead


def _passo(dia: int = 2, descricao: str | None = DESCRICAO) -> CadenciaPasso:
    return CadenciaPasso(
        id=21, organizacao_id=1, cadencia_id=3, dia=dia, canal="whatsapp", titulo="Primeiro contato", descricao=descricao
    )


def _agendar(sessao: FakeSession, lead: Lead, passo: CadenciaPasso) -> str | None:
    cadencia = Cadencia(id=3, organizacao_id=1, nome="Prospecção marcas")
    return asyncio.run(agendar_whatsapp_do_passo(sessao, lead, cadencia, passo, AGORA))


def _mensagens(sessao: FakeSession) -> list[MensagemSaida]:
    return [o for o in sessao.adicionados if isinstance(o, MensagemSaida)]


def test_texto_usa_a_descricao_do_passo_com_a_saudacao_e_cai_no_titulo_se_vazia() -> None:
    assert montar_texto_cadencia(nome="Carlos Silva", passo=_passo()).startswith("Olá, Carlos!\n\nVimos que")
    assert montar_texto_cadencia(nome=None, passo=_passo(descricao=None)) == "Olá!\n\nPrimeiro contato"


def test_passo_de_whatsapp_entra_na_fila_de_prospeccao_no_dia_do_passo() -> None:
    sessao = FakeSession([])
    assert _agendar(sessao, _lead(), _passo(dia=2)) is None
    (mensagem,) = _mensagens(sessao)
    assert mensagem.finalidade == "prospeccao" and mensagem.status == "pendente"
    assert mensagem.telefone == "5511999998888"
    assert (mensagem.titular_tipo, mensagem.titular_id) == ("lead", 9)
    assert mensagem.chave_idempotencia == "cadencia:3:9:21:whatsapp"
    assert mensagem.agendada_para == AGORA + timedelta(days=2)
    assert mensagem.corpo.endswith("responda SAIR.")  # a prospecção sempre ensina a sair


def test_aplicar_de_novo_nao_duplica_a_mensagem() -> None:
    existente = MensagemSaida(id=1, organizacao_id=1, status="pendente", chave_idempotencia="cadencia:3:9:21:whatsapp")
    sessao = FakeSession([FakeResult(scalar=existente)])
    assert _agendar(sessao, _lead(), _passo()) is None
    assert _mensagens(sessao) == []


def test_telefone_suprimido_fica_bloqueado_mesmo_na_cadencia() -> None:
    sessao = FakeSession([FakeResult(scalar=None), FakeResult(scalar=base._consentimento("revogado", "prospeccao"))])
    _agendar(sessao, _lead(), _passo())
    assert _mensagens(sessao)[0].status == "bloqueada"


def test_lead_que_nao_pode_receber_nao_gera_mensagem() -> None:
    casos = {
        "lead_descadastrado": _lead(cadencia_opt_out_em=AGORA),
        "lead_indisponivel": _lead(arquivado_em=AGORA),
        "telefone_sem_celular_valido": _lead(telefone="(11) 3333-4444"),
    }
    for motivo, lead in casos.items():
        sessao = FakeSession([])
        assert motivo_sem_envio(lead) == motivo and _agendar(sessao, lead, _passo()) == motivo
        assert _mensagens(sessao) == []
    assert motivo_sem_envio(_lead(anonimizado_em=AGORA)) == "lead_indisponivel"


def test_lead_que_responde_ou_sai_cancela_os_passos_de_whatsapp_pendentes() -> None:
    sessao = FakeSession([])
    asyncio.run(pausar_envios_pendentes_do_lead(sessao, 1, 9))
    cancelamentos = [c for c in sessao.executados if "UPDATE mensagens_saida" in str(c)]
    assert len(cancelamentos) == 1
    params = cancelamentos[0].compile().params.values()
    assert "cancelada" in params and "prospeccao" in params and 9 in params and "lead" in params


def test_no_envio_o_lead_e_conferido_de_novo_e_o_descadastrado_e_cancelado() -> None:
    mensagem = base._mensagem("prospeccao", titular_tipo="lead", titular_id=9)
    sessao = FakeSession(
        [FakeResult(), FakeResult(itens=[]), FakeResult(itens=[mensagem]), FakeResult(scalar=None)],
        objetos_get=[_lead(cadencia_opt_out_em=AGORA)],
    )
    provedor = base._Provedor()
    resultado = base._processar(sessao, provedor)
    assert resultado["bloqueadas"] == 1 and provedor.chamadas == []
    assert mensagem.status == "cancelada" and mensagem.motivo_bloqueio == "lead_indisponivel"


def test_no_envio_lead_normal_segue_o_fluxo_da_prospeccao() -> None:
    mensagem = base._mensagem("prospeccao", titular_tipo="lead", titular_id=9)
    sessao = FakeSession(
        [
            FakeResult(),
            FakeResult(itens=[]),
            FakeResult(itens=[mensagem]),
            FakeResult(scalar=None),  # sem supressão
            FakeResult(scalar=0),  # enviadas hoje
            FakeResult(scalar=0),  # falhas recentes
            FakeResult(scalar=None),  # último envio
            base._reserva(),
        ],
        objetos_get=[_lead()],
    )
    provedor = base._Provedor()
    assert base._processar(sessao, provedor)["enviadas"] == 1
    assert provedor.chamadas[0][0] == "prospeccao"
