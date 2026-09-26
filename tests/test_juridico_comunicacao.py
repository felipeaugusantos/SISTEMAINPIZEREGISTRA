import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import app.juridico_comunicacao as comunicacao
from app.models import SaidaEmailJuridico
from tests.conftest import FakeResult, FakeSession


def _saida(**kwargs: object) -> SaidaEmailJuridico:
    base = {
        "id": 10,
        "organizacao_id": 1,
        "chave": "juridico:1:2:vencido:3",
        "tipo": "alerta_prazo",
        "destinatario": "juridico@example.test",
        "assunto": "Prazo vencido",
        "mensagem": "Confira o prazo.",
        "status": "pendente",
        "tentativas": 0,
        "disponivel_em": datetime.now(UTC) - timedelta(minutes=1),
    }
    base.update(kwargs)
    return SaidaEmailJuridico(**base)


def test_processar_saida_registra_provedor_e_envio(monkeypatch: pytest.MonkeyPatch) -> None:
    saida = _saida()
    session = FakeSession([FakeResult(itens=[saida])])

    async def _enviar(*_args: object, **_kwargs: object) -> str:
        return "secundario"

    monkeypatch.setattr(comunicacao, "enviar_comunicacao_juridica_rastreada", _enviar)
    resultado = asyncio.run(comunicacao.processar_saidas_email_juridico(session))

    assert resultado == {"processados": 1, "enviados": 1, "reagendados": 0, "falhas": 0}
    assert saida.status == "enviado"
    assert saida.provedor == "secundario"
    assert saida.enviado_em is not None
    assert session.commits == 2  # reivindicação + resultado


def test_processar_saida_reagenda_sem_gravar_detalhe_sensivel(monkeypatch: pytest.MonkeyPatch) -> None:
    saida = _saida(tentativas=1)
    session = FakeSession([FakeResult(itens=[saida])])

    async def _falhar(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("senha-super-secreta")

    monkeypatch.setattr(comunicacao, "enviar_comunicacao_juridica_rastreada", _falhar)
    resultado = asyncio.run(comunicacao.processar_saidas_email_juridico(session))

    assert resultado["reagendados"] == 1
    assert saida.status == "pendente"
    assert saida.ultimo_erro == "RuntimeError"
    assert "senha" not in saida.ultimo_erro


def test_processar_saida_falha_definitivamente_na_quinta_tentativa(monkeypatch: pytest.MonkeyPatch) -> None:
    saida = _saida(tentativas=4)
    session = FakeSession([FakeResult(itens=[saida])])

    async def _falhar(*_args: object, **_kwargs: object) -> str:
        raise TimeoutError

    monkeypatch.setattr(comunicacao, "enviar_comunicacao_juridica_rastreada", _falhar)
    resultado = asyncio.run(comunicacao.processar_saidas_email_juridico(session))

    assert resultado["falhas"] == 1
    assert saida.status == "falha"
    assert saida.tentativas == 5
