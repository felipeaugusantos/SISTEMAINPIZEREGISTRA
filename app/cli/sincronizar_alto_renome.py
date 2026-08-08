import argparse
import asyncio
import html
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pypdf import PdfReader
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.database import session_factory
from app.models import MarcaAltoRenome
from app.settings import get_settings

USER_AGENT = "INPI-API/0.1 (sincronizacao de dados publicos)"


@dataclass(frozen=True, slots=True)
class ListaAltoRenome:
    processos: tuple[str, ...]
    marcas: tuple[tuple[str, str], ...]
    atualizada_em: datetime | None


def localizar_pdf_oficial(pagina_url: str, conteudo_html: str) -> str:
    links = re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)', conteudo_html, re.I)
    candidatos = [
        html.unescape(urljoin(pagina_url, link))
        for link in links
        if "alto-renome" in link.lower() and "vigencia" in link.lower()
    ]
    if not candidatos:
        raise ValueError("A página oficial não informou a lista de alto renome em PDF")
    return candidatos[0]


def extrair_lista_pdf(conteudo: bytes) -> ListaAltoRenome:
    texto = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(conteudo)).pages)
    return extrair_lista_texto(texto)


def extrair_lista_texto(texto: str) -> ListaAltoRenome:
    processos = tuple(dict.fromkeys(re.findall(r"(?<!\d)\d{9}(?!\d)", texto)))
    if len(processos) < 20:
        raise ValueError("A lista oficial retornou poucos processos e não será aplicada")

    correspondencia = re.search(
        r"(?:última|ultima)\s+atualização:\s*(\d{2}/\d{2}/\d{4})",
        texto,
        re.I,
    )
    atualizada_em = (
        datetime.strptime(correspondencia.group(1), "%d/%m/%Y") if correspondencia else None
    )
    marcas: dict[str, str] = {}
    pendentes: list[str] = []
    for linha_original in texto.splitlines():
        linha = re.sub(r"\s+", " ", linha_original).strip()
        numero = re.search(r"(?<!\d)(\d{9})(?!\d)", linha)
        if not numero:
            if (
                linha
                and not linha.isdigit()
                and "Marca Apresentação" not in linha
                and len(linha) <= 80
            ):
                pendentes.append(linha)
                pendentes = pendentes[-2:]
            continue

        apresentacao = re.search(r"\b(Nominativa|Mista|Figurativa)\b", linha, re.I)
        if apresentacao and apresentacao.group(1).lower() == "nominativa":
            prefixo = linha[: apresentacao.start()].strip()
            marca = prefixo or " ".join(pendentes)
            if marca:
                marcas[numero.group(1)] = marca
        pendentes.clear()

    return ListaAltoRenome(
        processos=processos,
        marcas=tuple(marcas.items()),
        atualizada_em=atualizada_em,
    )


def baixar(url: str) -> bytes:
    requisicao = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(requisicao, timeout=60) as resposta:
        return resposta.read()


async def sincronizar(pagina_url: str) -> tuple[int, str]:
    pagina = baixar(pagina_url).decode("utf-8", errors="replace")
    pdf_url = localizar_pdf_oficial(pagina_url, pagina)
    lista = extrair_lista_pdf(baixar(pdf_url))
    marcas = dict(lista.marcas)

    async with session_factory() as session:
        await session.execute(update(MarcaAltoRenome).values(vigente=False))
        for numero in lista.processos:
            comando = (
                insert(MarcaAltoRenome)
                .values(
                    numero_processo_normalizado=numero,
                    marca=marcas.get(numero),
                    vigente=True,
                    fonte_url=pdf_url,
                    fonte_atualizada_em=(
                        lista.atualizada_em.date() if lista.atualizada_em else None
                    ),
                )
                .on_conflict_do_update(
                    index_elements=[MarcaAltoRenome.numero_processo_normalizado],
                    set_={
                        "vigente": True,
                        "marca": marcas.get(numero),
                        "fonte_url": pdf_url,
                        "fonte_atualizada_em": (
                            lista.atualizada_em.date() if lista.atualizada_em else None
                        ),
                        "sincronizado_em": datetime.now().astimezone(),
                    },
                )
            )
            await session.execute(comando)
        await session.commit()
    return len(lista.processos), pdf_url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sincroniza as marcas de alto renome da página oficial do INPI."
    )
    parser.add_argument("--pagina-url", default=get_settings().alto_renome_page_url)
    argumentos = parser.parse_args()
    quantidade, fonte = asyncio.run(sincronizar(argumentos.pagina_url))
    print(f"{quantidade} registros de alto renome sincronizados de {fonte}")


if __name__ == "__main__":
    main()
