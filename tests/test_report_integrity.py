import inspect
from datetime import UTC, date, datetime

from pypdf import PdfReader

from app.privacy import mascarar_documentos_publicos
from app.relatorios import gerar_pdf_relatorio
from app.schemas import (
    ConclusaoIndicativaResponse,
    EvidenciasBuscaResponse,
    QualidadeBaseResponse,
)
from app.search import buscar_marcas, identificar_criterios
from app.trademarks.relevance import classificar_relevancia, construir_conclusao
from tests.test_relatorio_pdf import _relatorio_exemplo


def test_mascara_cpf_e_cnpj_em_texto_publico() -> None:
    texto = "JOÃO 12602336777 / EMPRESA 12.345.678/0001-90"
    mascarado = mascarar_documentos_publicos(texto)

    assert "12602336777" not in mascarado
    assert "12.345.678/0001-90" not in mascarado
    assert "***.***.***-**" in mascarado
    assert "**.***.***/****-**" in mascarado


def test_relevancia_combina_nome_situacao_e_afinidade() -> None:
    resultado = classificar_relevancia(
        ["Nome idêntico"],
        alto_renome=False,
        afinidade_nivel="alta",
        relevancia_situacao="ativa",
    )

    assert resultado.nivel == "critica"
    assert "Elemento nominativo idêntico" in resultado.justificativas


def test_conclusao_nao_promete_registro_sem_ocorrencias() -> None:
    nivel, titulo, resumo, _ = construir_conclusao([], 0)

    assert nivel == "nenhuma_ocorrencia"
    assert "disponibilidade" in resumo
    assert "pode registrar" not in f"{titulo} {resumo}".lower()


def test_regressao_firmino_nao_tem_limite_de_vinte() -> None:
    limite = inspect.signature(buscar_marcas).parameters["limite"].default

    assert limite >= 200
    assert identificar_criterios("Studio TF Beauty Thaina Firmino", "Firmino")


def test_pdf_registra_evidencias_qualidade_e_conclusao(tmp_path) -> None:
    relatorio = _relatorio_exemplo().model_copy(
        update={
            "gerado_em": datetime(2026, 7, 23, 3, 14, tzinfo=UTC),
            "evidencias_busca": EvidenciasBuscaResponse(
                termo_original="CAVALINHO FEROZ",
                expressao_completa="CAVALINHO FEROZ",
                radicais=["CAVALIN", "FERO"],
                variacoes=["CAVALO", "KAVAL", "PHERO"],
                nomes_identicos=1,
                expressoes_completas=1,
                ocorrencias_por_radical=3,
                criterios_considerados=["nome idêntico", "radicais"],
                versao_algoritmo="busca-marcas-2.0",
            ),
            "qualidade_base": QualidadeBaseResponse(
                status="adequada",
                ultima_rpi=2897,
                data_ultima_rpi=date(2026, 7, 14),
                importada_em=datetime(2026, 7, 16, tzinfo=UTC),
                idade_dias=8,
                total_processos_marca=5_000_000,
                deposito_mais_antigo=date(1990, 1, 1),
                deposito_mais_recente=date(2026, 7, 1),
                ocorrencias_sem_titulo=0,
                ocorrencias_sem_situacao=0,
                ocorrencias_sem_classe=0,
            ),
            "conclusao": ConclusaoIndicativaResponse(
                nivel="atencao_critica",
                titulo="Foram localizadas ocorrências de atenção crítica",
                resumo="Revisão humana necessária.",
                revisao_humana_recomendada=True,
            ),
        }
    )
    caminho = tmp_path / "relatorio.pdf"
    caminho.write_bytes(gerar_pdf_relatorio(relatorio))
    texto = "\n".join(pagina.extract_text() or "" for pagina in PdfReader(caminho).pages)

    assert "Evidências e critérios da pesquisa" in texto
    assert "Qualidade e cobertura da base" in texto
    assert "Conclusão indicativa" in texto
    assert "busca-marcas-2.0" in texto
    assert "23/07/2026 às 00:14" in texto
