import logging
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from defusedxml.ElementTree import iterparse

from app.models import TipoProcesso
from app.rpi.types import ClassificacaoMarcaRpi, MovimentacaoRpi, RegistroRpi, TitularRpi

FORMATO_DATA_RPI = "%d/%m/%Y"

logger = logging.getLogger(__name__)


def _data(valor: str | None) -> date | None:
    if not valor:
        return None
    return datetime.strptime(valor, FORMATO_DATA_RPI).date()


def _deposito_sanitizado(data_deposito: date | None, data_rpi: date, numero: str) -> date | None:
    """Descarta data de depósito posterior à data da própria RPI.

    Um depósito é sempre anterior à revista que o publica, então
    data_deposito > data_rpi só pode ser erro de digitação na fonte
    (caso real: cobertura da base exibindo depósito em 2030). Sem esta
    validação o valor contaminava func.max(data_deposito) em
    app.trademarks.quality e a "Cobertura de depósitos" de todos os
    relatórios. O registro é mantido; só a data inválida é descartada.
    """
    if data_deposito is not None and data_deposito > data_rpi:
        logger.warning(
            "Data de depósito futura descartada no processo %s: %s (RPI de %s)",
            numero,
            data_deposito.isoformat(),
            data_rpi.isoformat(),
        )
        return None
    return data_deposito


def _texto(valor: str | None) -> str | None:
    if valor is None:
        return None
    valor = valor.strip()
    return valor or None


def ler_marcas(caminho: Path) -> Iterator[RegistroRpi]:
    contexto = iterparse(caminho, events=("start", "end"))
    _, raiz = next(contexto)
    numero_rpi = int(raiz.attrib["numero"])
    data_rpi = _data(raiz.attrib.get("data"))
    if data_rpi is None:
        raise ValueError("A RPI de marcas não contém data válida")

    for evento, elemento in contexto:
        if evento != "end" or elemento.tag != "processo":
            continue

        despachos = tuple(
            MovimentacaoRpi(
                codigo=_texto(despacho.attrib.get("codigo")),
                descricao=_texto(despacho.attrib.get("nome")) or "Despacho sem descrição",
            )
            for despacho in elemento.findall("./despachos/despacho")
        )
        titulares = tuple(
            TitularRpi(
                nome=titular.attrib["nome-razao-social"].strip(),
                pais=_texto(titular.attrib.get("pais")),
            )
            for titular in elemento.findall("./titulares/titular")
            if titular.attrib.get("nome-razao-social", "").strip()
        )
        situacao = despachos[-1].descricao if despachos else None
        marca = elemento.find("./marca")
        nome_marca = _texto(elemento.findtext("./marca/nome"))
        classes_vienna = tuple(
            ClassificacaoMarcaRpi(
                sistema="vienna",
                codigo=classe.attrib["codigo"].strip(),
                edicao=_texto(classe.attrib.get("edicao")),
            )
            for classe in elemento.findall("./classes-vienna/classe-vienna")
            if classe.attrib.get("codigo", "").strip()
        )
        classes_nice = tuple(
            ClassificacaoMarcaRpi(
                sistema="nice",
                codigo=classe.attrib["codigo"].strip(),
                especificacao=_texto(classe.findtext("especificacao")),
                status=_texto(classe.findtext("status")),
            )
            for classe in elemento.findall("./lista-classe-nice/classe-nice")
            if classe.attrib.get("codigo", "").strip()
        )

        yield RegistroRpi(
            numero=elemento.attrib["numero"].strip(),
            tipo=TipoProcesso.MARCA,
            titulo=nome_marca,
            data_deposito=_deposito_sanitizado(
                _data(elemento.attrib.get("data-deposito")), data_rpi, elemento.attrib["numero"].strip()
            ),
            situacao=situacao,
            numero_rpi=numero_rpi,
            data_rpi=data_rpi,
            fonte_arquivo=caminho.name,
            titulares=titulares,
            movimentacoes=despachos,
            apresentacao=_texto(marca.attrib.get("apresentacao")) if marca is not None else None,
            natureza=_texto(marca.attrib.get("natureza")) if marca is not None else None,
            elemento_nominativo=nome_marca,
            procurador=_texto(elemento.findtext("procurador")),
            classificacoes=classes_vienna + classes_nice,
        )
        elemento.clear()
        raiz.clear()


def ler_patentes(caminho: Path) -> Iterator[RegistroRpi]:
    contexto = iterparse(caminho, events=("start", "end"))
    _, raiz = next(contexto)
    numero_rpi = int(raiz.attrib["numero"])
    data_rpi = _data(raiz.attrib.get("dataPublicacao"))
    if data_rpi is None:
        raise ValueError("A RPI de patentes não contém data válida")

    ordem = 0
    for evento, elemento in contexto:
        if evento != "end" or elemento.tag != "despacho":
            continue

        processo = elemento.find("processo-patente")
        if processo is None:
            elemento.clear()
            continue

        titulo_despacho = _texto(elemento.findtext("titulo")) or "Despacho sem descrição"
        comentario = _texto(elemento.findtext("comentario"))
        descricao = f"{titulo_despacho}: {comentario}" if comentario else titulo_despacho
        codigo = _texto(elemento.findtext("codigo"))
        titulares = tuple(
            TitularRpi(
                nome=nome,
                pais=_texto(titular.findtext("./endereco/pais/sigla")),
            )
            for titular in processo.findall("./titular-lista/titular")
            if (nome := _texto(titular.findtext("nome-completo"))) is not None
        )

        yield RegistroRpi(
            numero=(processo.findtext("numero") or "").strip(),
            tipo=TipoProcesso.PATENTE,
            titulo=_texto(processo.findtext("titulo")),
            data_deposito=_deposito_sanitizado(
                _data(processo.findtext("data-deposito")), data_rpi, (processo.findtext("numero") or "").strip()
            ),
            situacao=titulo_despacho,
            numero_rpi=numero_rpi,
            data_rpi=data_rpi,
            fonte_arquivo=caminho.name,
            titulares=titulares,
            movimentacoes=(MovimentacaoRpi(codigo=codigo, descricao=descricao),),
            ordem=ordem,
        )
        ordem += 1
        elemento.clear()
        raiz.clear()
