from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from defusedxml.ElementTree import iterparse

from app.models import TipoProcesso
from app.rpi.types import ClassificacaoMarcaRpi, MovimentacaoRpi, RegistroRpi, TitularRpi

FORMATO_DATA_RPI = "%d/%m/%Y"


def _data(valor: str | None) -> date | None:
    if not valor:
        return None
    return datetime.strptime(valor, FORMATO_DATA_RPI).date()


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
            data_deposito=_data(elemento.attrib.get("data-deposito")),
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
            data_deposito=_data(processo.findtext("data-deposito")),
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
