from io import BytesIO

from PIL import Image

from app.trademarks.benchmark import avaliar_gate_regressao
from app.trademarks.visual import assinatura_visual, similaridade_visual
from app.trademarks.visual_ranking import calcular_score_visual


def _png(cor: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), color=cor).save(buffer, format="PNG")
    return buffer.getvalue()


def test_score_visual_explicavel_e_versionado() -> None:
    score = calcular_score_visual(0.9, 0.5, 1.0)
    assert score.total > 0
    assert score.versao == "visual-ranking-1.0"
    assert {item["fator"] for item in score.fatores} == {"similaridade_visual", "ocr", "v viena"}


def test_gate_bloqueia_queda_de_recall() -> None:
    gate = avaliar_gate_regressao(
        {"recall_at_5": 0.4, "recall_at_10": 0.8, "recall_at_20": 1.0, "mrr": 0.7},
        {"recall_at_5": 0.5, "recall_at_10": 0.8, "recall_at_20": 1.0, "mrr": 0.7},
    )
    assert gate["bloqueado"] is True


# --- Achado da Fase 14.5 (auditoria fina da busca figurativa, 23/09/2026):
# app.trademarks.visual (o hash perceptual usado pelo upload de imagem)
# nunca tinha nenhum teste unitário, apesar de ser o núcleo do endpoint
# /v1/admin/figurativa/validar-imagem. ---


def test_assinatura_visual_e_deterministica_para_a_mesma_imagem() -> None:
    conteudo = _png((10, 20, 30))
    assert assinatura_visual(conteudo) == assinatura_visual(conteudo)


def test_assinatura_visual_tem_256_bits_binarios() -> None:
    assinatura = assinatura_visual(_png((200, 50, 90)))
    assert len(assinatura) == 256
    assert set(assinatura) <= {0, 1}


def test_similaridade_visual_e_maxima_para_a_mesma_assinatura() -> None:
    assinatura = assinatura_visual(_png((10, 20, 30)))
    assert similaridade_visual(assinatura, assinatura) == 1.0


def test_similaridade_visual_zero_quando_tamanhos_diferem() -> None:
    assert similaridade_visual((1, 0, 1), (1, 0)) == 0.0


def test_similaridade_visual_zero_quando_vazia() -> None:
    assert similaridade_visual((), ()) == 0.0
