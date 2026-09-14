from datetime import UTC, datetime, timedelta

import pytest

from app.api.producao import _resposta_aviso
from app.avisos_versao import confirmar_leitura, lembrar_avisos_criticos_pendentes
from app.models import AlertaSistema, AvisoVersao, AvisoVersaoConfirmacao
from app.permissions import CHAVES_PERMISSAO, PERFIS
from tests.conftest import FakeResult, FakeSession, usuario_teste

# Fase 3 (notificações e confirmação de leitura, 09/09/2026): confirmar
# leitura é sempre passivo -- nunca aciona deploy/rollout, só registra
# usuário, organização, data e (opcionalmente) IP hasheado.


async def _sem_email(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture(autouse=True)
def _mock_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.alertas_plataforma.enviar_alerta_plataforma", _sem_email)


def _aviso(**overrides: object) -> AvisoVersao:
    base = dict(
        id=1,
        organizacao_id=None,
        versao="2026.09.09",
        titulo="Nova versão publicada",
        mensagem="Detalhes da atualização.",
        severidade="critico",
        critico=True,
        ativo=True,
        criado_por_id=1,
        publicado_em=datetime.now(UTC) - timedelta(hours=48),
    )
    base.update(overrides)
    return AvisoVersao(**base)


async def test_confirmar_leitura_cria_registro_quando_nao_existe() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    confirmacao = await confirmar_leitura(
        session, aviso_id=1, usuario_id=7, organizacao_id=2, ip_hash="abc123"
    )
    assert session.adicionados == [confirmacao]
    assert confirmacao.aviso_id == 1
    assert confirmacao.usuario_id == 7
    assert confirmacao.organizacao_id == 2
    assert confirmacao.ip_hash == "abc123"


async def test_confirmar_leitura_e_idempotente() -> None:
    """Uma segunda confirmação do mesmo usuário para o mesmo aviso não
    duplica a linha (uq_aviso_versao_confirmacao) -- só retorna a existente,
    sem inserir de novo."""
    existente = AvisoVersaoConfirmacao(
        id=99, aviso_id=1, organizacao_id=2, usuario_id=7, ip_hash=None, confirmado_em=datetime.now(UTC)
    )
    session = FakeSession([FakeResult(scalar=existente)])
    confirmacao = await confirmar_leitura(
        session, aviso_id=1, usuario_id=7, organizacao_id=2, ip_hash="novo-ip"
    )
    assert confirmacao is existente
    assert session.adicionados == []


async def test_resposta_aviso_calcula_pendentes_e_confirmado_por_mim() -> None:
    aviso = _aviso()
    session = FakeSession(
        [
            FakeResult(scalar=10),  # total_usuarios ativos
            FakeResult(scalar=4),  # total_confirmados
            FakeResult(scalar=1),  # confirmacao do próprio usuário (existe)
        ]
    )
    resposta = await _resposta_aviso(session, usuario_teste(), aviso)
    assert resposta.total_usuarios == 10
    assert resposta.total_confirmados == 4
    assert resposta.pendentes == 6
    assert resposta.confirmado_por_mim is True


async def test_resposta_aviso_pendentes_nunca_fica_negativo() -> None:
    aviso = _aviso()
    session = FakeSession(
        [
            FakeResult(scalar=2),
            FakeResult(scalar=5),  # RLS de outra organização, cenário raro (superadmin)
            FakeResult(scalar=None),
        ]
    )
    resposta = await _resposta_aviso(session, usuario_teste(), aviso)
    assert resposta.pendentes == 0
    assert resposta.confirmado_por_mim is False


async def test_lembrar_avisos_criticos_pendentes_cria_alerta_por_organizacao() -> None:
    aviso = _aviso()
    session = FakeSession(
        [
            FakeResult(itens=[aviso]),  # avisos críticos ativos vencidos
            FakeResult(itens=[5]),  # organizações alvo (aviso de plataforma)
            FakeResult(scalar=3),  # usuários pendentes na org 5
            FakeResult(scalar=None),  # registrar_alerta_plataforma: dedup check (nenhum aberto)
        ]
    )
    await lembrar_avisos_criticos_pendentes(session)
    alertas = [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)]
    assert len(alertas) == 1
    assert alertas[0].codigo == "AVISO_CRITICO_PENDENTE_1"
    assert alertas[0].organizacao_id == 5
    assert alertas[0].detalhes["pendentes"] == 3


async def test_lembrar_avisos_criticos_pendentes_resolve_quando_todos_confirmaram() -> None:
    aviso = _aviso()
    session = FakeSession(
        [
            FakeResult(itens=[aviso]),
            FakeResult(itens=[5]),
            FakeResult(scalar=0),  # ninguém mais pendente
            FakeResult(itens=[]),  # resolver_alerta_plataforma: nenhum alerta aberto pra fechar
        ]
    )
    await lembrar_avisos_criticos_pendentes(session)
    assert [obj for obj in session.adicionados if isinstance(obj, AlertaSistema)] == []


async def test_lembrar_avisos_criticos_pendentes_ignora_aviso_recente() -> None:
    """Um aviso crítico publicado há menos de aviso_critico_lembrete_horas
    não entra na consulta -- a query já filtra por publicado_em, então a
    fila de resultados fica vazia após o primeiro select."""
    session = FakeSession([FakeResult(itens=[])])
    await lembrar_avisos_criticos_pendentes(session)
    assert session.adicionados == []


def test_avisos_view_esta_registrada_e_disponivel_para_administrador() -> None:
    assert "avisos.view" in CHAVES_PERMISSAO
    assert "avisos.view" in PERFIS["administrador"]
    assert "avisos.view" in PERFIS["auditor"]


def test_perfil_operador_nao_ve_avisos() -> None:
    # "operador" só tem dashboard.view -- confirmar leitura continua livre
    # pra qualquer usuário autenticado (endpoint de confirmar não exige
    # avisos.view), mas a auditoria/gestão de avisos é restrita.
    assert "avisos.view" not in PERFIS["operador"]
