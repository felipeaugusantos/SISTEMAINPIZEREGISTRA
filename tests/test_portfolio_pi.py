from app.api.portfolio_pi import PAPEIS, TIPOS, _slug
from app.models import AtivoPartePI, AtivoPI, AtivoProcessoPI, DocumentoAtivoPI


def test_portfolio_cobre_ativos_e_relacoes_obrigatorias() -> None:
    assert {"marca", "patente", "modelo_utilidade", "desenho_industrial"}.issubset(TIPOS)
    assert {"contrato", "cessao", "licenca", "franquia"}.issubset(TIPOS)
    assert PAPEIS == {"inventor", "procurador", "titular_adicional"}
    assert "organizacao_id" in AtivoPI.__table__.c
    assert "titular_id" in AtivoPI.__table__.c
    assert "processo_id" in AtivoProcessoPI.__table__.c
    assert "hash_documento" in DocumentoAtivoPI.__table__.c
    assert "ativo_id" in AtivoPartePI.__table__.c


def test_nome_de_documento_e_normalizado_para_storage() -> None:
    assert _slug("Procuração/cliente final.pdf") == "Procura_o_cliente_final.pdf"
