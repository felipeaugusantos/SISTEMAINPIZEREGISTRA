from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

BASE_URL = "https://busca.inpi.gov.br"
LOGIN_URL = f"{BASE_URL}/pePI/servlet/LoginController?action=login"
SEARCH_URL = f"{BASE_URL}/pePI/servlet/MarcasServletController"
CLASSIFIER_VERSION = "fundamento-inpi-1.0"


def _tem_desafio_captcha(documento: str) -> bool:
    texto = documento.casefold()
    return any(
        marcador in texto
        for marcador in (
            "g-recaptcha-response",
            "h-captcha-response",
            "confirme que você não é um robô",
            "confirme que voce nao e um robo",
        )
    )


def _normalizar_texto(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        if "Action=detail" in href and "CodPedido=" in href:
            self.links.append(href)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._cell is not None:
            assert self._row is not None
            self._row.append(_normalizar_texto(" ".join(self._cell)))
            self._cell = None
        elif tag.lower() == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


@dataclass(frozen=True)
class OfficialDecision:
    processo_numero: str
    cod_pedido: str
    fonte_url: str
    despacho_texto: str
    numero_rpi: int | None
    fundamento: str | None
    confianca: float
    artigos: list[str]
    processos_citados: list[str]
    hash_conteudo: str


@dataclass(frozen=True)
class DecisionClassification:
    fundamento: str | None
    confianca: float
    artigos: list[str]
    processos_citados: list[str]


def extrair_link_detalhe(documento: str) -> tuple[str, str]:
    parser = _SearchResultParser()
    parser.feed(documento)
    if not parser.links:
        raise ValueError("Processo não localizado na busca pública do INPI")
    href = parser.links[0]
    match = re.search(r"CodPedido=(\d+)", href, flags=re.IGNORECASE)
    if not match:
        raise ValueError("Código interno do pedido não localizado")
    return urljoin(BASE_URL, href), match.group(1)


def extrair_despacho_indeferimento(
    documento: str, numero_rpi: int | None = None
) -> tuple[str, int | None]:
    parser = _TableParser()
    parser.feed(documento)
    candidatos: list[tuple[str, int | None]] = []
    for row in parser.rows:
        texto = " | ".join(cell for cell in row if cell)
        if "indeferimento do pedido" not in texto.casefold():
            continue
        rpi = None
        if row and re.fullmatch(r"\d{3,5}", row[0]):
            rpi = int(row[0])
        complemento = row[-1] if row else ""
        complemento = re.sub(
            r"^Detalhes do despacho:\s*", "", complemento, flags=re.IGNORECASE
        ).strip()
        if complemento:
            candidatos.append((complemento, rpi))
    if not candidatos:
        raise ValueError("Complemento do indeferimento não disponível no detalhe público")
    if numero_rpi is not None:
        correspondente = next((item for item in candidatos if item[1] == numero_rpi), None)
        if correspondente:
            return correspondente
    return candidatos[0]


def classificar_fundamento(texto: str) -> DecisionClassification:
    normalizado = _normalizar_texto(texto)
    minusculo = normalizado.casefold()
    artigos = sorted(
        set(
            re.findall(
                r"(?:art(?:igo)?\.?\s*(?:124|128)(?:\s*,?\s*(?:inciso\s*)?[IVXLCDM]+\b)?)",
                normalizado,
                flags=re.IGNORECASE,
            )
        )
    )
    processos = sorted(set(re.findall(r"\b(?:processo\s*)?(\d{9})\b", normalizado, re.I)))

    conflito = (
        bool(re.search(r"(?:inciso\s*)?xix\b", minusculo))
        or "reproduz ou imita" in minusculo
        or "confusão ou associação" in minusculo
        or "confusao ou associacao" in minusculo
        or "colidência" in minusculo
    )
    distintividade = (
        bool(re.search(r"(?:inciso\s*)?vi\b", minusculo))
        or any(
            termo in minusculo
            for termo in (
                "descritiv",
                "genéric",
                "generico",
                "caráter vulgar",
                "carater vulgar",
                "sinal de caráter genérico",
                "destituído de distintividade",
                "destituido de distintividade",
            )
        )
    )
    if conflito:
        return DecisionClassification("conflito_anterior", 0.99, artigos, processos)
    if distintividade:
        return DecisionClassification("falta_distintividade", 0.98, artigos, processos)
    if (
        "art. 128" in minusculo
        or "artigo 128" in minusculo
        or "atividade licita e efetiva" in minusculo
        or "atividade lícita e efetiva" in minusculo
    ):
        return DecisionClassification("outra_proibicao", 0.99, artigos, processos)
    if "art. 124" in minusculo or "artigo 124" in minusculo:
        return DecisionClassification("outra_proibicao", 0.90, artigos, processos)
    return DecisionClassification(None, 0.0, artigos, processos)


class InpiDecisionClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "ZeRegistra/1.0 (official public decision evidence)",
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
        )
        self._started = False

    async def __aenter__(self) -> InpiDecisionClient:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.client.aclose()

    async def start(self) -> None:
        if self._started:
            return
        response = await self.client.get(LOGIN_URL)
        response.raise_for_status()
        self._started = True

    async def coletar(
        self, processo_numero: str, numero_rpi: int | None = None
    ) -> OfficialDecision:
        await self.start()
        numero = re.sub(r"\D", "", processo_numero)
        response = await self.client.post(
            SEARCH_URL,
            data={
                "NumPedido": numero,
                "NumGRU": "",
                "NumProtocolo": "",
                "NumInscricaoInternacional": "",
                "Action": "searchMarca",
                "tipoPesquisa": "BY_NUM_PROC",
            },
        )
        response.raise_for_status()
        if _tem_desafio_captcha(response.text):
            raise RuntimeError("O INPI solicitou CAPTCHA; a coleta foi interrompida")
        detalhe_url, cod_pedido = extrair_link_detalhe(response.text)
        detalhe = await self.client.get(detalhe_url)
        detalhe.raise_for_status()
        if _tem_desafio_captcha(detalhe.text):
            raise RuntimeError("O INPI solicitou CAPTCHA; a coleta foi interrompida")
        despacho, rpi_encontrada = extrair_despacho_indeferimento(detalhe.text, numero_rpi)
        classificacao = classificar_fundamento(despacho)
        return OfficialDecision(
            processo_numero=processo_numero,
            cod_pedido=cod_pedido,
            fonte_url=detalhe_url,
            despacho_texto=despacho,
            numero_rpi=rpi_encontrada,
            fundamento=classificacao.fundamento,
            confianca=classificacao.confianca,
            artigos=classificacao.artigos,
            processos_citados=classificacao.processos_citados,
            hash_conteudo=hashlib.sha256(despacho.encode("utf-8")).hexdigest(),
        )
