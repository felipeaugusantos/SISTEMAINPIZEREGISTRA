from datetime import UTC, datetime
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.database import get_session
from app.main import app
from app.models import TipoProcesso
from app.relatorios import gerar_pdf_relatorio, gerar_pdf_resumo_cliente
from app.schemas import (
    AfinidadeClassesResponse,
    ClasseNiceCandidataResponse,
    ClassificacaoMarcaResponse,
    MarcaRelatorioItem,
    MotivoPrognosticoResponse,
    PrognosticoRegistrabilidadeResponse,
    RelatorioMarcaResponse,
    TitularResponse,
)
from app.security import exigir_token_integracao
from tests.conftest import FakeResult, sessao_override


def _relatorio_exemplo(
    *, com_itens: bool = True, com_prognostico: bool = False
) -> RelatorioMarcaResponse:
    itens = []
    if com_itens:
        itens.append(
            MarcaRelatorioItem(
                numero="943906024",
                tipo=TipoProcesso.MARCA,
                titulo="CAVALINHO FEROZ",
                data_deposito=None,
                situacao="Registro concedido",
                situacao_normalizada="registro_vigente",
                relevancia_situacao="alta",
                atualizado_em=datetime.now(UTC),
                titulares=[TitularResponse(nome="ACME LTDA", pais="BR")],
                apresentacao="Mista",
                natureza="Produto",
                classificacoes=[
                    ClassificacaoMarcaResponse(
                        sistema="nice",
                        codigo="25",
                        edicao=None,
                        especificacao=None,
                        status=None,
                    )
                ],
                criterios_encontro=["Nome idêntico"],
                alto_renome=True,
                afinidade_classes=AfinidadeClassesResponse(
                    nivel="alta",
                    rotulo="Alta afinidade",
                    justificativa="x",
                    revisao="pendente",
                    classes_atividade=["25"],
                    classes_processo=["25"],
                ),
            )
        )
    return RelatorioMarcaResponse(
        id="abc",
        marca="CAVALINHO FEROZ",
        atividade="Venda de roupas",
        tipo_pesquisa="completa",
        classe_nice=None,
        criado_em=datetime.now(UTC),
        gerado_em=datetime.now(UTC),
        ultima_rpi=2897,
        classes_atividade=[
            ClasseNiceCandidataResponse(
                codigo="25",
                titulo="Vestuário",
                tipo="produto",
                termos_encontrados=["roupas"],
            )
        ],
        matriz_afinidade_status="pendente_de_validacao",
        alto_renome_atualizado_em=None,
        total=1 if com_itens else 0,
        limite_exibido=1 if com_itens else 0,
        itens=itens,
        prognostico_registrabilidade=(
            PrognosticoRegistrabilidadeResponse(
                veredito="desfavoravel",
                titulo="Risco de indeferimento",
                resumo="A triagem encontrou possíveis impedimentos no exame de mérito.",
                motivos=[
                    MotivoPrognosticoResponse(
                        criterio="Disponibilidade e anterioridades",
                        conclusao="Anterioridades com potencial impeditivo",
                        referencia="Manual 5.11 e art. 124, XIX, da LPI",
                    )
                ],
                pendencias=["Distintividade", "Liceidade"],
                versao_matriz="teste",
                ressalva="Prognóstico indicativo; não constitui garantia de registro.",
            )
            if com_prognostico
            else None
        ),
        risco_pontuacao=69,
        risco_nivel="alto",
    )


class _FakeVersao:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


def test_gera_pdf_valido() -> None:
    pdf = gerar_pdf_relatorio(_relatorio_exemplo())
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
    texto = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)
    # Sem prognóstico, a seção não aparece (a estimativa de ML foi aposentada do relatório).
    assert "Prognóstico de registrabilidade" not in texto


def test_gera_pdf_sem_ocorrencias() -> None:
    pdf = gerar_pdf_relatorio(_relatorio_exemplo(com_itens=False))
    assert pdf[:5] == b"%PDF-"


def test_gera_pdf_com_prognostico_deterministico() -> None:
    pdf = gerar_pdf_relatorio(_relatorio_exemplo(com_prognostico=True))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
    texto = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)
    assert "Prognóstico de registrabilidade" in texto
    assert "Risco de indeferimento" in texto
    assert "Disponibilidade e anterioridades" in texto
    assert "garantia de registro" in texto


def test_resumo_cliente_tem_uma_pagina_e_nao_expoe_ocorrencias() -> None:
    pdf = gerar_pdf_resumo_cliente(_relatorio_exemplo(com_prognostico=True))
    leitor = PdfReader(BytesIO(pdf))
    texto = "\n".join(page.extract_text() or "" for page in leitor.pages)

    assert len(leitor.pages) == 1
    assert "Prognóstico de registrabilidade" in texto
    assert "Análise técnica" in texto
    assert "69 pontos" in texto
    assert "risco alto" in texto
    assert "AnÃ¡lise" not in texto
    assert "Ocorrências encontradas" not in texto
    assert "943906024" not in texto


def test_endpoint_pdf_retorna_documento() -> None:
    versao = _FakeVersao(_relatorio_exemplo().model_dump(mode="json"))
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=versao))
    app.dependency_overrides[exigir_token_integracao] = lambda: None
    try:
        resposta = TestClient(app).get("/v1/pesquisas-marca/abc/relatorio.pdf")
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"
    assert 'filename="resumo-' in resposta.headers["content-disposition"]
    assert resposta.content[:5] == b"%PDF-"
    assert len(PdfReader(BytesIO(resposta.content)).pages) == 1


def test_endpoint_pdf_404_sem_versao() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    app.dependency_overrides[exigir_token_integracao] = lambda: None
    try:
        resposta = TestClient(app).get("/v1/pesquisas-marca/inexistente/relatorio.pdf")
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 404
