from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.relatorios import gerar_pdf_relatorio
from app.trademarks.consolidated import analise_para_exibicao, apresentacao_analise
from tests.test_consolidated_analysis import snapshot


def report_with_findings():
    report = snapshot()
    analysis = report.analise_consolidada
    analysis["matriz"]["regras"] = [
        {
            "criterio": "Distintividade",
            "status": "possivel_impedimento",
            "conclusao": "Termo indicado como descritivo.",
            "evidencia": "Declaração técnica registrada.",
            "referencia": "Referência técnica do critério",
        },
        {
            "criterio": "Produtos e serviços",
            "status": "alerta",
            "conclusao": "Especificação precisa de revisão.",
            "evidencia": "Classe ainda não definida.",
        },
        {"criterio": "Afinidade mercadológica", "status": "alerta", "conclusao": "Afinidade precisa de validação."},
        {"criterio": "Documentos", "status": "alerta", "conclusao": "Documentação precisa de conferência."},
    ]
    analysis["conclusao_preliminar"].update(
        decisao="cenario_desfavoravel",
        motivos=[
            "1 possível(is) impedimento(s) nas regras do INPI",
            "3 ponto(s) de atenção",
            "modelo estatístico indisponível",
        ],
    )
    return report


def test_counts_are_replaced_by_specific_findings_and_statistics_are_separate():
    report = report_with_findings()
    result = apresentacao_analise(report.analise_consolidada)
    assert len(result["impedimentos"]) == 1
    assert result["impedimentos"][0]["criterio"] == "Distintividade"
    assert result["impedimentos"][0]["justificativa"] == "Termo indicado como descritivo."
    assert result["impedimentos"][0]["evidencia"] == "Declaração técnica registrada."
    assert len(result["pontos_atencao"]) == 3
    assert result["fundamentos_tecnicos"] == []
    assert "sem apoio estatístico" in result["mensagem_apoio"]
    assert "não é um impedimento da marca" in result["mensagem_apoio"]
    assert result["situacao"]["rotulo"] == "Desfavorável"


@pytest.mark.parametrize(
    "decision,abstention,label",
    [
        ("cenario_favoravel", False, "Favorável"),
        ("cenario_desfavoravel", False, "Desfavorável"),
        ("cenario_desfavoravel", True, "Desfavorável"),
        ("cenario_intermediario", False, "Inconclusiva - requer revisão"),
        ("dados_insuficientes", True, "Inconclusiva - dados insuficientes"),
        ("cenario_favoravel", True, "Inconclusiva - dados insuficientes"),
        ("unknown", False, "Inconclusiva - dados insuficientes"),
    ],
)
def test_status_does_not_turn_uncertainty_into_favorable(decision, abstention, label):
    result = apresentacao_analise({"conclusao_preliminar": {"decisao": decision, "abstencao": abstention}})
    assert result["situacao"]["rotulo"] == label
    assert result["situacao"]["origem"] == "analise_automatica"


def test_legacy_stored_analysis_gains_details_without_recalculation_or_mutation():
    report = report_with_findings()
    report.analise_consolidada["parecer_humano"] = {"observacoes": "Parecer já registrado"}
    payload = report.model_dump(mode="json")
    before = deepcopy(payload)
    displayed = analise_para_exibicao(
        payload,
        versao=3,
        validado_por="Especialista",
        validado_em=datetime.now(UTC),
    )
    assert displayed["apresentacao"]["impedimentos"][0]["criterio"] == "Distintividade"
    assert displayed["revisao"]["validada"] is True
    assert displayed["conclusao_preliminar"] == before["analise_consolidada"]["conclusao_preliminar"]
    assert payload == before


def test_restricted_statistical_support_stays_restricted():
    result = apresentacao_analise(
        {"estatistica": {"disponivel": False, "mensagem": "Indicador estatístico restrito ao seu perfil."}}
    )
    assert result["mensagem_apoio"] == "Indicador estatístico restrito ao seu perfil."


def test_generic_counts_are_not_silently_discarded_if_evidence_is_missing():
    result = apresentacao_analise(
        {"conclusao_preliminar": {"motivos": ["1 possível(is) impedimento(s) nas regras do INPI"]}}
    )
    assert len(result["fundamentos_tecnicos"]) == 1


@pytest.mark.parametrize(
    "decision,abstention,label",
    [
        ("cenario_desfavoravel", True, "Desfavorável"),
        ("cenario_favoravel", False, "Favorável"),
        ("dados_insuficientes", True, "Inconclusiva - dados insuficientes"),
    ],
)
def test_full_pdf_prints_explicit_status_and_specific_findings(decision, abstention, label):
    report = report_with_findings()
    report.analise_consolidada["conclusao_preliminar"].update(decisao=decision, abstencao=abstention)
    result = apresentacao_analise(report.analise_consolidada)
    document = "\n".join(page.extract_text() for page in PdfReader(BytesIO(gerar_pdf_relatorio(report))).pages)
    assert f"Situação da análise automática: {label}" in document
    assert result["impedimentos"][0]["justificativa"] in document
    assert "Declaração técnica registrada." in document
    for item in result["pontos_atencao"]:
        assert item["criterio"] in document
        assert item["justificativa"] in document
    assert "1 possível(is) impedimento(s)" not in document
    assert "3 ponto(s) de atenção" not in document
    assert "modelo estatístico indisponível" not in document
    assert "Apoio estatístico (informação do sistema)" in document
