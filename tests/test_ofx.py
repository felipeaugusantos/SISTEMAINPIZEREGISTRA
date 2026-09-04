from datetime import date
from decimal import Decimal

import pytest

from app.ofx import OfxInvalidoError, parsear_ofx

# --- Achado FASE7-3 da auditoria (04/09/2026): conciliação bancária --
# parser de OFX sem dependência nova. ---

OFX_EXEMPLO = """
OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260115120000[-3:BRT]
<TRNAMT>500.00
<FITID>202601150001
<MEMO>PAGAMENTO RECEBIDO CLIENTE X
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260116120000[-3:BRT]
<TRNAMT>-45.90
<FITID>202601160002
<MEMO>TARIFA BANCARIA
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def test_parsear_ofx_extrai_transacoes() -> None:
    transacoes = parsear_ofx(OFX_EXEMPLO)
    assert len(transacoes) == 2
    credito, debito = transacoes
    assert credito.fitid == "202601150001"
    assert credito.data == date(2026, 1, 15)
    assert credito.valor == Decimal("500.00")
    assert credito.tipo == "credito"
    assert "PAGAMENTO" in credito.descricao
    assert debito.tipo == "debito"
    assert debito.valor == Decimal("-45.90")


def test_parsear_ofx_sem_transacoes_levanta_erro() -> None:
    with pytest.raises(OfxInvalidoError):
        parsear_ofx("<OFX><BANKMSGSRSV1></BANKMSGSRSV1></OFX>")


def test_parsear_ofx_conteudo_vazio_levanta_erro() -> None:
    with pytest.raises(OfxInvalidoError):
        parsear_ofx("")


def test_parsear_ofx_pula_transacao_incompleta_mas_mantem_as_validas() -> None:
    ofx = """
<OFX>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260115120000
<TRNAMT>100.00
<FITID>abc123
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<MEMO>SEM FITID NEM DATA
</STMTTRN>
</OFX>
"""
    transacoes = parsear_ofx(ofx)
    assert len(transacoes) == 1
    assert transacoes[0].fitid == "abc123"


def test_parsear_ofx_todas_transacoes_incompletas_levanta_erro() -> None:
    ofx = "<OFX><STMTTRN><MEMO>sem nada util</STMTTRN></OFX>"
    with pytest.raises(OfxInvalidoError):
        parsear_ofx(ofx)
