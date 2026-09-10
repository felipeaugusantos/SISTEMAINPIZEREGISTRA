"""Auditoria técnica (10/09/2026) — Hipótese 8: POST
/v1/admin/leads/{lead_id}/contatos exige pesquisa_id -- e um lead "geral"
sem nenhuma pesquisa vinculada?

Isolados e determinísticos: usam FakeSession (tests/conftest.py), sem
tocar banco real. Não substituem nem alteram nenhum teste existente.

Nota de implementação: _lead_do_operador (app/api/leads.py) busca o lead
via session.get(), não session.execute() -- por isso este arquivo usa
FakeSession(resultados, objetos_get=[...]) diretamente em vez do atalho
sessao_override (que só popula a fila de resultados de execute()).
"""

from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import Lead, PesquisaMarca
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste


def _lead(**overrides: object) -> Lead:
    base = dict(
        id=7,
        organizacao_id=1,
        nome="Fulano",
        email="fulano@example.com",
        telefone="11999998888",
        marca="ACME",
        origem="processo",
        empresa_id=None,
    )
    base.update(overrides)
    return Lead(**base)


def _pesquisa(**overrides: object) -> PesquisaMarca:
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        organizacao_id=1,
        lead_id=7,
        marca="ACME",
        tipo_pesquisa="completa",
        empresa_id=None,
    )
    base.update(overrides)
    return PesquisaMarca(**base)


def _override_session(session: FakeSession):
    async def _gen() -> Iterator[FakeSession]:
        yield session

    return _gen


def _registrar(lead_id: int, payload: dict, lead: Lead | None, resultados: list[FakeResult]):
    session = FakeSession(resultados, objetos_get=[lead] if lead is not None else [])
    usuario = usuario_teste()
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = _override_session(session)
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    return TestClient(app).post(
        f"/v1/admin/leads/{lead_id}/contatos",
        json=payload,
        headers={"X-CSRF-Token": "csrf-teste"},
    )


def test_lead_com_pesquisa_registra_contato_normalmente() -> None:
    """Caminho feliz: lead existe, pesquisa existe, pertence a ele e à
    mesma organização -- registra o contato (201)."""
    lead = _lead()
    pesquisa = _pesquisa()
    resposta = _registrar(
        7,
        {"canal": "telefone", "pesquisa_id": pesquisa.id},
        lead,
        [FakeResult(scalar=pesquisa)],
    )

    assert resposta.status_code == 201
    assert resposta.json()["pesquisa_id"] == pesquisa.id


def test_lead_geral_sem_pesquisa_nao_consegue_usar_este_endpoint() -> None:
    """CONFIRMADO: pesquisa_id é obrigatório em ContatoInput (min_length=36,
    max_length=36, sem default) -- omitir o campo é rejeitado na validação
    do próprio Pydantic antes de chegar à lógica de negócio (422). Um lead
    "geral" (captado sem nenhuma pesquisa de marca associada) não tem
    nenhum pesquisa_id válido para informar, então este endpoint específico
    não serve para registrar atendimento desse tipo de lead."""
    resposta = _registrar(7, {"canal": "telefone"}, _lead(), [])

    assert resposta.status_code == 422


def test_pesquisa_id_de_outro_lead_e_rejeitada() -> None:
    """CONFIRMADO: a query de busca da pesquisa filtra
    PesquisaMarca.lead_id == lead_id (o lead da URL) -- uma pesquisa real,
    mas de OUTRO lead, não é encontrada e o endpoint devolve 422 com
    mensagem clara ("nao pertence a este contato"), não um erro genérico
    nem um vínculo indevido."""
    pesquisa_de_outro_lead = _pesquisa(lead_id=999)
    resposta = _registrar(
        7, {"canal": "telefone", "pesquisa_id": pesquisa_de_outro_lead.id}, _lead(), [FakeResult(scalar=None)]
    )

    assert resposta.status_code == 422
    assert "nao pertence" in resposta.json()["detail"].lower()


def test_pesquisa_id_de_outra_organizacao_e_rejeitada() -> None:
    """CONFIRMADO: a mesma query também filtra
    PesquisaMarca.organizacao_id == usuario.organizacao_id -- isolamento
    multi-tenant reforçado neste endpoint especificamente (não é só
    filtro no lead, é filtro repetido na pesquisa também)."""
    resposta = _registrar(
        7,
        {"canal": "telefone", "pesquisa_id": "22222222-2222-2222-2222-222222222222"},
        _lead(),
        [FakeResult(scalar=None)],
    )

    assert resposta.status_code == 422
    assert "nao pertence" in resposta.json()["detail"].lower()


def test_pesquisa_id_invalido_mal_formado_e_rejeitado_pela_validacao() -> None:
    """CONFIRMADO: ContatoInput exige exatamente 36 caracteres -- um id
    curto/mal formado nem chega à consulta ao banco, é rejeitado pela
    validação do schema (422)."""
    resposta = _registrar(7, {"canal": "telefone", "pesquisa_id": "id-invalido"}, _lead(), [])

    assert resposta.status_code == 422
