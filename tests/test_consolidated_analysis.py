from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.analysis_service import atualizar_snapshot_analise
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import PesquisaMarca, VersaoRelatorioMarca
from app.relatorios import gerar_pdf_relatorio
from app.trademarks.consolidated import analise_para_exibicao, construir_analise_consolidada
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste
from tests.test_production_governance import relatorio


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def snapshot():
    report = relatorio()
    report.risco_pontuacao = 12
    report.risco_nivel = "baixo"
    report.analise_consolidada = construir_analise_consolidada(report.model_dump(mode="json"))
    return report


def test_inactive_statistics_are_not_published():
    data = relatorio().model_dump(mode="json")
    data["estimativa_status"] = "validacao_interna"
    data["estimativa_registrabilidade"] = {
        "modelo_status": "VALIDATION",
        "probabilidade_deferimento": 0.99,
        "cobertura_entrada": 1,
    }
    result = construir_analise_consolidada(data)
    assert result["estatistica"]["disponivel"] is False
    assert result["estatistica"]["estimativa"] is None
    assert result["conclusao_preliminar"]["probabilidade_deferimento"] is None


@pytest.mark.parametrize("coverage,available", [(0.3, False), (0.9, True)])
def test_statistics_require_eligible_model_and_coverage(coverage, available):
    data = relatorio().model_dump(mode="json")
    data["estimativa_status"] = "disponivel"
    data["estimativa_registrabilidade"] = {
        "modelo_status": "ACTIVE",
        "probabilidade_deferimento": 0.8,
        "cobertura_entrada": coverage,
    }
    result = construir_analise_consolidada(data)
    assert result["estatistica"]["disponivel"] is available
    assert (result["conclusao_preliminar"]["probabilidade_deferimento"] is not None) is available


def test_changed_evidence_invalidates_opinion_but_metadata_does_not():
    report = snapshot()
    report.analise_consolidada["parecer_humano"] = {"observacoes": "Parecer original"}
    data = report.model_dump(mode="json")
    data["versao"] = 42
    same = construir_analise_consolidada(data)
    assert same["parecer_humano"]["observacoes"] == "Parecer original"
    changed = construir_analise_consolidada(data, {"descricao_visual": "Novo desenho"})
    assert changed["entrada_hash"] != same["entrada_hash"]
    assert changed["parecer_humano"] is None


def test_legacy_analysis_never_inherits_previous_approval():
    shown = analise_para_exibicao(
        relatorio().model_dump(mode="json"),
        versao=1,
        validado_por="especialista",
        validado_em=datetime.now(UTC),
    )
    assert shown["legado"] is True
    assert shown["revisao"]["validada"] is False


def test_display_does_not_mutate_immutable_payload():
    report = snapshot()
    data = report.model_dump(mode="json")
    shown = analise_para_exibicao(data, versao=7)
    shown["conclusao_preliminar"]["motivos"].append("mutated")
    assert "revisao" not in data["analise_consolidada"]
    assert "mutated" not in data["analise_consolidada"]["conclusao_preliminar"]["motivos"]


def test_pdf_and_screen_share_conclusion_and_private_opinion_stays_internal():
    report = snapshot()
    report.analise_consolidada["parecer_humano"] = {
        "observacoes": "JUSTIFICATIVA INTERNA EXCLUSIVA",
        "nivel": "moderado",
        "avaliador": "Especialista Teste",
        "avaliado_em": datetime.now(UTC).isoformat(),
    }
    shown = analise_para_exibicao(
        report.model_dump(mode="json"),
        versao=1,
        validado_por="Especialista Teste",
        validado_em=datetime.now(UTC),
    )
    report.analise_consolidada = shown
    full = "\n".join(page.extract_text() for page in PdfReader(BytesIO(gerar_pdf_relatorio(report))).pages)
    public = "\n".join(
        page.extract_text()
        for page in PdfReader(
            BytesIO(
                gerar_pdf_relatorio(report, incluir_ocorrencias=False),
            )
        ).pages
    )
    assert shown["titulo"] in full
    assert "JUSTIFICATIVA INTERNA EXCLUSIVA" in full
    assert "VALIDADO POR ESPECIALISTA" in full
    assert "JUSTIFICATIVA INTERNA EXCLUSIVA" not in public
    assert "VALIDADO POR ESPECIALISTA" not in public
    assert "PRELIMINAR" in public
    assert "Triagem determinística de registrabilidade" not in full


async def test_stale_review_cannot_write():
    report = snapshot()
    pesquisa = PesquisaMarca(id=report.id, marca=report.marca, organizacao_id=1)
    version = VersaoRelatorioMarca(pesquisa_id=report.id, numero_versao=3, payload=report.model_dump(mode="json"))
    session = FakeSession([FakeResult(scalar=version)])
    with pytest.raises(ValueError, match="A análise mudou"):
        await atualizar_snapshot_analise(session, pesquisa, parecer={"observacoes": "Novo"}, versao_esperada=2)
    assert session.adicionados == []


async def test_new_opinion_snapshot_preserves_workflow_by_default():
    """atualizar_snapshot_analise() sozinha não deve derrubar um workflow em
    andamento -- refinamentos internos (consolidar, parecer) não são um novo
    resultado de busca do cliente. A responsabilidade de mover o estado para
    IN_REVIEW quando um parecer é registrado é do endpoint (ver
    test_review_uses_authenticated_identity_and_advances_to_review); esta função
    de baixo nível só reseta quando o chamador pede explicitamente
    (resetar_workflow=True), ex.: dados complementares editados após validação."""
    report = snapshot()
    pesquisa = PesquisaMarca(
        id=report.id,
        marca=report.marca,
        organizacao_id=1,
        analysis_state="VALIDATED",
        validated_by="anterior",
        validated_at=datetime.now(UTC),
    )
    version = VersaoRelatorioMarca(pesquisa_id=report.id, numero_versao=1, payload=report.model_dump(mode="json"))

    class Session(FakeSession):
        async def get(self, *_args, **_kwargs):
            return pesquisa

    session = Session([FakeResult(scalar=version), FakeResult(scalar=version)])
    result = await atualizar_snapshot_analise(
        session,
        pesquisa,
        parecer={"observacoes": "Novo parecer", "nivel": "alto"},
        versao_esperada=1,
    )
    assert result.versao == 2
    assert result.analise_consolidada["parecer_humano"]["nivel"] == "alto"
    assert pesquisa.analysis_state == "VALIDATED"
    assert pesquisa.validated_by == "anterior"
    assert pesquisa.validated_at is not None


def test_review_uses_authenticated_identity_and_advances_to_review():
    report = snapshot()
    pesquisa = PesquisaMarca(id=report.id, marca=report.marca, organizacao_id=1, analysis_state="PENDING_REVIEW")
    version = VersaoRelatorioMarca(pesquisa_id=report.id, numero_versao=1, payload=report.model_dump(mode="json"))

    class Session(FakeSession):
        async def get(self, *_args, **_kwargs):
            return pesquisa

    session = Session([FakeResult(scalar=pesquisa), FakeResult(scalar=version), FakeResult(scalar=version)])

    async def override():
        yield session

    user = usuario_teste()
    object.__setattr__(user, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[obter_usuario_atual] = auth_override(user)
    response = TestClient(app).post(
        f"/v1/admin/analises/{report.id}/parecer",
        json={"nivel_humano": "moderado", "observacoes_humanas": "Justificativa técnica", "versao_relatorio": 1},
        headers={"X-CSRF-Token": "csrf-teste"},
    )
    assert response.status_code == 200, response.text
    created = next(item for item in session.adicionados if isinstance(item, VersaoRelatorioMarca))
    assert created.payload["analise_consolidada"]["parecer_humano"]["avaliador"] == user.ator
    assert pesquisa.analysis_state == "IN_REVIEW"


def test_readonly_user_cannot_consolidate_or_review():
    user = usuario_teste("operador", {"leads.view", "validation.view", "risk.view"})
    object.__setattr__(user, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(user)
    for suffix, payload in [
        ("consolidar", {}),
        ("parecer", {"nivel_humano": "baixo", "observacoes_humanas": "Teste", "versao_relatorio": 1}),
    ]:
        response = TestClient(app).post(
            f"/v1/admin/analises/teste/{suffix}",
            json=payload,
            headers={"X-CSRF-Token": "csrf-teste"},
        )
        assert response.status_code == 403


@pytest.mark.parametrize("expected,opinion,status", [(1, False, 409), (2, True, 409), (1, True, 200)])
def test_validation_requires_opinion_and_current_version(expected, opinion, status):
    report = snapshot()
    if opinion:
        report.analise_consolidada["parecer_humano"] = {"observacoes": "Parecer técnico"}
    pesquisa = PesquisaMarca(id=report.id, marca=report.marca, organizacao_id=1, analysis_state="IN_REVIEW")
    version = VersaoRelatorioMarca(pesquisa_id=report.id, numero_versao=1, payload=report.model_dump(mode="json"))
    session = FakeSession([FakeResult(scalar=pesquisa), FakeResult(scalar=version), FakeResult(scalar=None)])

    async def override():
        yield session

    user = usuario_teste()
    object.__setattr__(user, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[obter_usuario_atual] = auth_override(user)
    response = TestClient(app).patch(
        f"/v1/admin/analises/{report.id}/workflow",
        json={"action": "VALIDATE", "notes": "Evidências revisadas", "versao_relatorio": expected},
        headers={"X-CSRF-Token": "csrf-teste"},
    )
    assert response.status_code == status, response.text
    assert (pesquisa.analysis_state == "VALIDATED") is (status == 200)
