import asyncio
import smtplib
from datetime import UTC, datetime, timedelta

from app.cadencia_email import (
    dentro_do_horario_comercial,
    montar_envio_pendente,
    pausar_envios_pendentes_do_lead,
    processar_descadastro_cadencia,
    processar_envios_cadencia_pendentes,
    registrar_abertura,
)
from app.models import CadenciaPasso, EnvioCadenciaEmail, Lead
from tests.conftest import FakeResult, FakeSession

# --- Fase 9 do plano Leads/CRM (03/09/2026): cadências reais por e-mail
# (achados L5/L6). Sem WhatsApp nesta fase. ---


def _passo(**kwargs: object) -> CadenciaPasso:
    base: dict = {"id": 1, "organizacao_id": 1, "cadencia_id": 1, "ordem": 0, "dia": 3, "canal": "email", "titulo": "Follow-up"}
    base.update(kwargs)
    return CadenciaPasso(**base)


def _envio(**kwargs: object) -> EnvioCadenciaEmail:
    base: dict = {
        "id": 1,
        "organizacao_id": 1,
        "lead_id": 9,
        "cadencia_id": 1,
        "passo_id": 1,
        "agendado_para": datetime.now(UTC) - timedelta(hours=1),
        "status": "pendente",
        "tentativas": 0,
    }
    base.update(kwargs)
    return EnvioCadenciaEmail(**base)


def _lead(**kwargs: object) -> Lead:
    base: dict = {"id": 9, "organizacao_id": 1, "nome": "Fulano", "email": "fulano@example.com", "telefone": "11999998888", "marca": "ACME"}
    base.update(kwargs)
    return Lead(**base)


# --- horário comercial ---


def test_horario_comercial_dentro_da_janela_em_dia_util() -> None:
    quarta_11h_utc = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)  # 08:00 em America/Sao_Paulo (UTC-3)
    assert dentro_do_horario_comercial(quarta_11h_utc, 8, 19) is True


def test_horario_comercial_fora_da_janela_a_noite() -> None:
    quarta_22h05_utc = datetime(2026, 9, 2, 22, 5, tzinfo=UTC)  # 19:05 em America/Sao_Paulo
    assert dentro_do_horario_comercial(quarta_22h05_utc, 8, 19) is False


def test_horario_comercial_fora_no_fim_de_semana() -> None:
    sabado_11h_utc = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)  # sábado, 08:00 em America/Sao_Paulo
    assert dentro_do_horario_comercial(sabado_11h_utc, 8, 19) is False


# --- agendamento ---


def test_montar_envio_pendente_agenda_pelo_dia_do_passo_sem_token() -> None:
    passo = _passo(dia=5)
    agora = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    valores = montar_envio_pendente(organizacao_id=1, lead_id=9, cadencia_id=3, passo=passo, agora=agora)
    assert valores["agendado_para"] == agora + timedelta(days=5)
    assert valores["passo_id"] == passo.id
    assert "rastreio_token_hash" not in valores


# --- envio ---


async def _envio_ok(*_a: object, **_k: object) -> None:
    return None


async def _envio_falha(*_a: object, **_k: object) -> None:
    raise RuntimeError("SMTP indisponível")


def _sem_horario(momento: datetime, inicio: int, fim: int) -> bool:
    return True


def _isolar_horario_e_envio(envio_fn):
    import app.cadencia_email as modulo

    original_horario = modulo.dentro_do_horario_comercial
    original_envio = modulo.enviar_passo_cadencia
    modulo.dentro_do_horario_comercial = _sem_horario
    modulo.enviar_passo_cadencia = envio_fn
    return original_horario, original_envio


def _restaurar(original_horario, original_envio) -> None:
    import app.cadencia_email as modulo

    modulo.dentro_do_horario_comercial = original_horario
    modulo.enviar_passo_cadencia = original_envio


def test_processar_envios_marca_enviado_e_gera_token_no_sucesso() -> None:
    originais = _isolar_horario_e_envio(_envio_ok)
    try:
        envio = _envio()
        session = FakeSession([FakeResult(itens=[envio])], objetos_get=[_lead()])
        envio.passo = _passo()
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        _restaurar(*originais)

    assert resultado == {"enviados": 1, "falhas": 0, "por_organizacao": {1: {"enviados": 1, "falhas": 0}}}
    assert envio.status == "enviado"
    assert envio.enviado_em is not None
    assert envio.rastreio_token_hash is not None


def test_processar_envios_marca_falhou_quando_lead_nao_existe() -> None:
    originais = _isolar_horario_e_envio(_envio_ok)
    try:
        envio = _envio()
        envio.passo = _passo()
        session = FakeSession([FakeResult(itens=[envio])], objetos_get=[None])
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        _restaurar(*originais)

    assert resultado == {"enviados": 0, "falhas": 1, "por_organizacao": {1: {"enviados": 0, "falhas": 1}}}
    assert envio.status == "falhou"


def test_processar_envios_incrementa_tentativas_ate_falhar_de_vez() -> None:
    originais = _isolar_horario_e_envio(_envio_falha)
    try:
        envio = _envio(tentativas=2)  # settings.cadencia_email_max_tentativas default = 3
        envio.passo = _passo()
        session = FakeSession([FakeResult(itens=[envio])], objetos_get=[_lead()])
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        _restaurar(*originais)

    assert resultado == {"enviados": 0, "falhas": 1, "por_organizacao": {1: {"enviados": 0, "falhas": 1}}}
    assert envio.tentativas == 3
    assert envio.status == "falhou"
    assert "SMTP indisponível" in envio.ultimo_erro


def test_processar_envios_pula_lead_descadastrado_da_cadencia() -> None:
    # Achado da auditoria completa do CRM (06/09/2026): lead que clicou no
    # link de descadastro não pode continuar recebendo a sequência.
    originais = _isolar_horario_e_envio(_envio_ok)
    try:
        envio = _envio()
        envio.passo = _passo()
        lead = _lead(cadencia_opt_out_em=datetime.now(UTC) - timedelta(days=1))
        session = FakeSession([FakeResult(itens=[envio])], objetos_get=[lead])
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        _restaurar(*originais)

    assert resultado == {"enviados": 0, "falhas": 0, "por_organizacao": {}}
    assert envio.status == "pausado"


def test_processar_envios_separa_contagem_por_organizacao() -> None:
    # Achado 17.4: o worker grava um AlertaSistema por organização a partir
    # desta quebra -- antes só havia o total agregado de todos os tenants.
    originais = _isolar_horario_e_envio(_envio_ok)
    try:
        envio_a = _envio(id=1, organizacao_id=1)
        envio_a.passo = _passo()
        envio_b = _envio(id=2, organizacao_id=2)
        envio_b.passo = _passo(organizacao_id=2)
        session = FakeSession(
            [FakeResult(itens=[envio_a, envio_b])],
            objetos_get=[_lead(), None],
        )
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        _restaurar(*originais)

    assert resultado["por_organizacao"] == {
        1: {"enviados": 1, "falhas": 0},
        2: {"enviados": 0, "falhas": 1},
    }


def test_processar_envios_fora_do_horario_nao_toca_nada() -> None:
    import app.cadencia_email as modulo

    original = modulo.dentro_do_horario_comercial
    modulo.dentro_do_horario_comercial = lambda *_a, **_k: False
    try:
        session = FakeSession()
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        modulo.dentro_do_horario_comercial = original

    assert resultado["enviados"] == 0
    assert resultado["motivo"] == "fora_do_horario_comercial"


# --- abertura (pixel de rastreio) ---


def test_registrar_abertura_marca_a_primeira_vez() -> None:
    envio = _envio(aberto_em=None)
    session = FakeSession([FakeResult(scalar=envio)])
    asyncio.run(registrar_abertura(session, "token-bruto"))
    assert envio.aberto_em is not None


def test_registrar_abertura_nao_sobrescreve_a_segunda_vez() -> None:
    primeira_abertura = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    envio = _envio(aberto_em=primeira_abertura)
    session = FakeSession([FakeResult(scalar=envio)])
    asyncio.run(registrar_abertura(session, "token-bruto"))
    assert envio.aberto_em == primeira_abertura


def test_registrar_abertura_token_inexistente_nao_quebra() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    asyncio.run(registrar_abertura(session, "token-que-nao-existe"))  # não deve levantar


# --- descadastro (opt-out) ---


def test_processar_descadastro_marca_lead_e_pausa_envios_pendentes() -> None:
    envio = _envio()
    lead = _lead(cadencia_opt_out_em=None)
    session = FakeSession([FakeResult(scalar=envio), FakeResult(rowcount=2)], objetos_get=[lead])

    resultado = asyncio.run(processar_descadastro_cadencia(session, "token-bruto"))

    assert resultado is True
    assert lead.cadencia_opt_out_em is not None


def test_processar_descadastro_token_inexistente_retorna_false() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    resultado = asyncio.run(processar_descadastro_cadencia(session, "token-invalido"))

    assert resultado is False


def test_processar_descadastro_e_idempotente() -> None:
    marca_original = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    envio = _envio()
    lead = _lead(cadencia_opt_out_em=marca_original)
    session = FakeSession([FakeResult(scalar=envio), FakeResult(rowcount=0)], objetos_get=[lead])

    resultado = asyncio.run(processar_descadastro_cadencia(session, "token-bruto"))

    assert resultado is True
    assert lead.cadencia_opt_out_em == marca_original


# --- pausa ao responder ---


def test_pausar_envios_pendentes_do_lead_devolve_quantidade_afetada() -> None:
    session = FakeSession([FakeResult(rowcount=2)])
    total = asyncio.run(pausar_envios_pendentes_do_lead(session, organizacao_id=1, lead_id=9))
    assert total == 2


async def _envio_cota_esgotada(*_args: object, **_kwargs: object) -> None:
    raise smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded")


def test_cota_diaria_adia_sem_gastar_tentativa_nem_marcar_falha() -> None:
    # Revisão do Codex no PR #164: a janela de 24h da cota pode durar mais que
    # as tentativas horárias; o envio fica pendente e o lote para.
    originais = _isolar_horario_e_envio(_envio_cota_esgotada)
    try:
        primeiro = _envio(id=1, tentativas=2)
        primeiro.passo = _passo()
        segundo = _envio(id=2, tentativas=0)
        segundo.passo = _passo()
        session = FakeSession([FakeResult(itens=[primeiro, segundo])], objetos_get=[_lead()])
        resultado = asyncio.run(processar_envios_cadencia_pendentes(session))
    finally:
        _restaurar(*originais)

    assert resultado["falhas"] == 0
    assert primeiro.status == "pendente"
    assert primeiro.tentativas == 2
    assert "Cota diária" in primeiro.ultimo_erro
    assert segundo.status == "pendente"
    assert segundo.tentativas == 0
