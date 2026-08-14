from datetime import UTC, datetime

import pytest

from app.api.producao import _resumo
from app.models import EventoAuditoria
from tests.conftest import FakeResult, FakeSession, usuario_teste


@pytest.mark.asyncio
async def test_auditoria_retorna_pagina_de_dez_e_total_completo() -> None:
    eventos = [
        EventoAuditoria(
            id=indice,
            organizacao_id=1,
            ator="admin@teste.local",
            acao="ATUALIZAR",
            recurso=f"processo:{indice}",
            sucesso=True,
            status_http=200,
            detalhes={},
            criado_em=datetime.now(UTC),
        )
        for indice in range(10)
    ]
    session = FakeSession(
        [
            FakeResult(itens=[(100, 2, 30.0, 75.0, 120)]),
            FakeResult(itens=[(10, 2)]),
            FakeResult(itens=[(8, 5)]),
            FakeResult(scalar=27),
            FakeResult(itens=eventos),
        ]
    )

    resultado = await _resumo(
        session,
        usuario_teste(),
        limite_auditoria=10,
        deslocamento_auditoria=10,
    )

    assert resultado.auditoria_total == 27
    assert resultado.auditoria_limite == 10
    assert resultado.auditoria_deslocamento == 10
    assert len(resultado.auditoria) == 10
