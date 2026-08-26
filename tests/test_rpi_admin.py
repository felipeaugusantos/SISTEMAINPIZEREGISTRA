from datetime import UTC, datetime, timedelta

from app.api.rpi_admin import (
    _aparencia_status,
    _execucao_response,
    pode_ver_execucoes_recentes,
    solicitar_sincronizacao,
    tentar_novamente,
)
from app.models import RpiSyncExecucao
from tests.conftest import FakeSession, usuario_teste


def test_cores_do_monitoramento_refletem_saude_e_atraso() -> None:
    assert _aparencia_status("atualizado", 0, True) == ("Base atualizada", "verde")
    assert _aparencia_status("importando", 0, True)[1] == "amarelo"
    assert _aparencia_status("atualizado", 1, True)[1] == "amarelo"
    assert _aparencia_status("atualizado", 2, True)[1] == "vermelho"
    assert _aparencia_status("falhou", 0, True)[1] == "vermelho"
    assert _aparencia_status("atualizado", 0, False)[1] == "cinza"


def test_execucao_calcula_progresso_e_duracao() -> None:
    inicio = datetime.now(UTC) - timedelta(seconds=90)
    execucao = RpiSyncExecucao(
        id=10,
        origem="automatica",
        status="importando",
        solicitado_por="rpi-sync",
        edicoes_total=4,
        edicoes_processadas=2,
        registros_processados=80_000,
        titulares_processados=12_000,
        classes_processadas=30_000,
        movimentacoes_processadas=82_000,
        solicitado_em=inicio,
        iniciado_em=inicio,
    )

    resposta = _execucao_response(execucao)

    assert resposta.progresso_percentual == 50.0
    assert resposta.duracao_segundos is not None
    assert resposta.duracao_segundos >= 90


def test_execucoes_recentes_sao_exclusivas_de_tech_e_administrador() -> None:
    assert pode_ver_execucoes_recentes(usuario_teste(perfil="tech"))
    assert pode_ver_execucoes_recentes(usuario_teste(perfil="administrador"))
    assert not pode_ver_execucoes_recentes(usuario_teste(perfil="ceo"))


async def test_solicitacao_manual_entra_na_fila() -> None:
    session = FakeSession()

    from tests.conftest import usuario_teste

    resposta = await solicitar_sincronizacao(session, usuario_teste())

    assert resposta.status == "solicitada"
    assert session.adicionados[0].origem == "manual"
    assert session.adicionados[0].solicitado_por == "admin@teste.local"


async def test_falha_pode_ser_colocada_novamente_na_fila() -> None:
    anterior = RpiSyncExecucao(id=44, origem="automatica", status="falhou")

    class SessionComFalha(FakeSession):
        async def get(self, *_args, **_kwargs):
            return anterior

    session = SessionComFalha()

    from tests.conftest import usuario_teste

    resposta = await tentar_novamente(44, session, usuario_teste())

    assert resposta.status == "solicitada"
    assert session.adicionados[0].origem == "reprocessamento"
    assert session.adicionados[0].execucao_anterior_id == 44
