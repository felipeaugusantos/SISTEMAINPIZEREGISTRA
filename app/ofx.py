"""Parser de extrato bancário no formato OFX (Open Financial Exchange) --
achado FASE7-3 da auditoria (04/09/2026): conciliação bancária não existia.

OFX (versão 1.x/SGML, que é o que a maioria dos bancos brasileiros exporta
para pessoa jurídica) permite tags sem fechamento explícito -- em vez de
adicionar uma dependência nova ao projeto (nenhuma lib de OFX está em
pyproject.toml), um parser via regex é suficiente para o subconjunto de
tags que interessa para conciliação (STMTTRN: FITID, DTPOSTED, TRNAMT,
TRNTYPE, MEMO/NAME).
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

_BLOCO_TRANSACAO = re.compile(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|\Z)", re.IGNORECASE | re.DOTALL)


class OfxInvalidoError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TransacaoOfx:
    fitid: str
    data: date
    valor: Decimal  # positivo = crédito (entrada), negativo = débito (saída)
    tipo: str  # "credito" | "debito"
    descricao: str


def _valor_tag(bloco: str, tag: str) -> str | None:
    padrao = re.compile(rf"<{tag}>\s*([^\r\n<]*)", re.IGNORECASE)
    encontrado = padrao.search(bloco)
    return encontrado.group(1).strip() if encontrado else None


def _parsear_data(bruto: str) -> date:
    digitos = bruto[:8]
    if len(digitos) != 8 or not digitos.isdigit():
        raise OfxInvalidoError(f"Data OFX inválida: {bruto!r}")
    try:
        return date(int(digitos[0:4]), int(digitos[4:6]), int(digitos[6:8]))
    except ValueError as exc:
        raise OfxInvalidoError(f"Data OFX inválida: {bruto!r}") from exc


def parsear_ofx(conteudo: str) -> list[TransacaoOfx]:
    """Extrai as transações (STMTTRN) de um extrato OFX. Linhas incompletas
    (sem FITID/data/valor) são puladas silenciosamente -- um extrato real
    pode ter transações "SUMMARY" ou similares que não interessam aqui;
    aborta só se NENHUMA transação válida sobrar no fim."""
    blocos = _BLOCO_TRANSACAO.findall(conteudo)
    if not blocos:
        raise OfxInvalidoError("Nenhuma transação (STMTTRN) encontrada no arquivo -- confirme que é um OFX válido.")
    transacoes: list[TransacaoOfx] = []
    for bloco in blocos:
        fitid = _valor_tag(bloco, "FITID")
        dtposted = _valor_tag(bloco, "DTPOSTED")
        trnamt = _valor_tag(bloco, "TRNAMT")
        memo = _valor_tag(bloco, "MEMO") or _valor_tag(bloco, "NAME") or ""
        if not fitid or not dtposted or not trnamt:
            continue
        try:
            valor = Decimal(trnamt.replace(",", "."))
        except InvalidOperation:
            continue
        transacoes.append(
            TransacaoOfx(
                fitid=fitid,
                data=_parsear_data(dtposted),
                valor=valor,
                tipo="credito" if valor > 0 else "debito",
                descricao=memo.strip(),
            )
        )
    if not transacoes:
        raise OfxInvalidoError("Nenhuma transação válida encontrada no arquivo (faltam FITID/data/valor).")
    return transacoes
