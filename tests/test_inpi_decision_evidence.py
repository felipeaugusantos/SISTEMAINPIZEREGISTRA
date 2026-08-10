from app.trademarks.decision_evidence import (
    classificar_fundamento,
    extrair_despacho_indeferimento,
    extrair_link_detalhe,
)


def test_extracts_public_detail_link() -> None:
    html = """
    <html><a href="/pePI/servlet/MarcasServletController?Action=detail&amp;CodPedido=6639551">
    943324823</a></html>
    """
    url, code = extrair_link_detalhe(html)
    assert code == "6639551"
    assert url.endswith("Action=detail&CodPedido=6639551")


def test_extracts_denial_complement_matching_rpi() -> None:
    html = """
    <table>
      <tr><td>2898</td><td>21/07/2026</td><td>Indeferimento do pedido</td>
          <td></td><td></td><td><b>Detalhes do despacho:</b> outro texto</td></tr>
      <tr><td>2900</td><td>04/08/2026</td><td>Indeferimento do pedido</td>
          <td></td><td></td><td><b>Detalhes do despacho:</b>
          Inciso XIX do Art. 124 da LPI.</td></tr>
    </table>
    """
    text, rpi = extrair_despacho_indeferimento(html, 2900)
    assert text == "Inciso XIX do Art. 124 da LPI."
    assert rpi == 2900


def test_classifies_explicit_conflict() -> None:
    result = classificar_fundamento(
        "A marca reproduz ou imita marca alheia, inciso XIX do Art. 124 da LPI: "
        "Processo 906566738 (LUXEN)."
    )
    assert result.fundamento == "conflito_anterior"
    assert result.confianca == 0.99
    assert result.processos_citados == ["906566738"]


def test_classifies_explicit_lack_of_distinctiveness() -> None:
    result = classificar_fundamento(
        "Sinal de caráter genérico e descritivo, inciso VI do Art. 124 da LPI."
    )
    assert result.fundamento == "falta_distintividade"
    assert result.confianca == 0.98


def test_does_not_guess_without_explicit_ground() -> None:
    result = classificar_fundamento("Pedido indeferido conforme parecer técnico.")
    assert result.fundamento is None
    assert result.confianca == 0.0


def test_classifies_article_128_as_other_prohibition() -> None:
    result = classificar_fundamento(
        "O requerente não exerce atividade lícita e efetiva compatível, "
        "conforme Parágrafo 1º do Art. 128 da LPI."
    )
    assert result.fundamento == "outra_proibicao"
    assert result.confianca == 0.99
