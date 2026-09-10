"""Auditoria técnica (10/09/2026) — Hipótese 4: descarte sem motivo.

Correção P1 aplicada em 10/09/2026: atualizar_status_lead agora exige
motivo_perda (válido, dentre MOTIVOS_PERDA) sempre que o status muda para
"descartado" -- antes o campo era opcional e o backend aceitava o descarte
sem nenhum motivo estruturado.

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, StatusLead
from tests.conftest import FakeResult, auth_override, sessao_override, usuario_teste


def _lead_aberto(**overrides: object) -> Lead:
    base = dict(
        id=9,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        status=StatusLead.QUALIFICADO,
        fase="qualificado",
        responsavel_id=3,
        proxima_acao_em=None,
        aceite_marketing=False,
    )
    base.update(overrides)
    lead = Lead(**base)
    # Construção direta (sem flush real) não aplica server_default -- o
    # refetch final de atualizar_status_lead exige criado_em/atualizado_em
    # preenchidos para validar LeadResponse. Não é o achado desta hipótese.
    lead.criado_em = lead.atualizado_em = datetime.now(UTC)
    return lead


def _patch(payload: dict, *, resultados: list[FakeResult] | None = None) -> object:
    lead = _lead_aberto()
    if resultados is None:
        # Ao virar "descartado" com motivo válido, aberta=False ->
        # aplicar_politica_oportunidade nem é chamada; só sobra a query de
        # cadências automáticas (sempre executada em
        # aplicar_cadencias_automaticas) e o refetch final (selectinload
        # responsavel).
        resultados = [FakeResult(scalar=lead), FakeResult(itens=[]), FakeResult(scalar=lead)]
    app.dependency_overrides[get_session] = sessao_override(*resultados)
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return TestClient(app).patch(
        "/v1/admin/leads/9",
        json=payload,
        headers={"X-CSRF-Token": "csrf-teste"},
    )


def test_descartar_sem_motivo_perda_e_rejeitado() -> None:
    """CORRIGIDO (achado H4/P1, 10/09/2026): atualizar_status_lead agora
    exige motivo_perda (não vazio e válido) sempre que o status muda para
    "descartado" -- omitir o campo é rejeitado com 422 antes de qualquer
    outra query, e o lead não é alterado."""
    resposta = _patch({"status": "descartado"}, resultados=[FakeResult(scalar=_lead_aberto())])

    assert resposta.status_code == 422
    assert "motivo" in resposta.json()["detail"].lower()


def test_descartar_com_motivo_perda_valido_e_aceito() -> None:
    resposta = _patch({"status": "descartado", "motivo_perda": "sem_resposta"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "descartado"
    assert corpo["motivo_perda"] == "sem_resposta"


def test_descartar_com_motivo_perda_invalido_e_rejeitado() -> None:
    """CONFIRMADO: valor fora de MOTIVOS_PERDA (app/models.py) é rejeitado
    com 422, mas só quando o campo É enviado -- a validação de conteúdo
    existe; a de presença, não."""
    resposta = _patch({"status": "descartado", "motivo_perda": "invalido"})

    assert resposta.status_code == 422
    assert "inválido" in resposta.json()["detail"].lower()


def test_descartar_com_motivo_outro_sem_detalhe_e_aceito() -> None:
    """CONFIRMADO: motivo_perda="outro" é um valor válido em MOTIVOS_PERDA,
    e motivo_perda_detalhe não tem nenhuma validação condicional exigindo
    texto quando o motivo é "outro" -- o backend aceita "outro" sem
    nenhuma descrição."""
    resposta = _patch({"status": "descartado", "motivo_perda": "outro"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["motivo_perda"] == "outro"
    assert corpo["motivo_perda_detalhe"] is None
