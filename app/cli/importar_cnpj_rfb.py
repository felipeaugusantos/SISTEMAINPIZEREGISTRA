"""ETL dos Dados Abertos do CNPJ (Receita Federal) -> cache_estabelecimentos_rfb.

Fase 2 do Radar de Prospecção (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md). NÃO roda pelo worker
Redis: a RFB não particiona os arquivos por UF/CNAE (são ~10 arquivos
arbitrários por tipo cobrindo o Brasil inteiro, vários GB compactados), então
isto é um job de lote pesado e demorado -- roda por cron/execução manual
("uv run python -m app.cli.importar_cnpj_rfb"), fora do request-response da
aplicação. A coleta *por campanha* (rápida, por tenant) é outro código
(app/api/prospeccao.py::coletar_campanha), que só consulta este cache já
pronto -- nunca baixa nada da RFB na hora.

Origem: dadosabertos.rfb.gov.br não responde a partir da rede desta VPS
(timeout de TCP, confirmado em 03/09/2026 -- outros hosts gov.br respondem
normalmente). A rota que funciona de fato é o compartilhamento público via
WebDAV (Nextcloud/SERPRO+, mesmo layout de arquivos, autenticado por um
token de compartilhamento como usuário HTTP Basic e senha vazia) --
configurável em app.settings (rfb_cnpj_base_url/rfb_cnpj_share_token), já
que um link de compartilhamento pode rotacionar sem aviso.

Uso:
    uv run python -m app.cli.importar_cnpj_rfb [--periodo AAAA-MM] [--limite-linhas N]

Sem --periodo, descobre o mês mais recente publicado automaticamente.
--limite-linhas é só para teste manual em ambiente de homologação (evita
baixar o Brasil inteiro ao validar a integração pela primeira vez).
"""

import argparse
import asyncio
import base64
import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from datetime import datetime
from urllib.request import Request, urlopen

from sqlalchemy.dialects.postgresql import insert

from app.database import session_factory
from app.models import CacheEstabelecimentoRFB
from app.rfb_cnpj import (
    COLUNAS_EMPRESA,
    COLUNAS_ESTABELECIMENTO,
    COLUNAS_REFERENCIA,
    montar_registro_cache,
)
from app.settings import get_settings

logger = logging.getLogger("ze_registra.importar_cnpj_rfb")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())

USER_AGENT = "INPI-API/0.1 (radar de prospeccao -- dados abertos CNPJ)"
TAMANHO_LOTE_UPSERT = 2000
_NS_DAV = {"d": "DAV:"}


def _cabecalhos(extra: dict[str, str] | None = None) -> dict[str, str]:
    cabecalhos = {"User-Agent": USER_AGENT, **(extra or {})}
    token = get_settings().rfb_cnpj_share_token
    if token:
        credencial = base64.b64encode(f"{token}:".encode()).decode()
        cabecalhos["Authorization"] = f"Basic {credencial}"
    return cabecalhos


def _baixar(url: str) -> bytes:
    requisicao = Request(url, headers=_cabecalhos())
    with urlopen(requisicao, timeout=300) as resposta:  # noqa: S310 - URL vem de settings, não de input do usuário
        return resposta.read()


def descobrir_periodo_mais_recente(base_url: str) -> str:
    """Lista o diretório via WebDAV PROPFIND (Depth: 1) e devolve a pasta
    AAAA-MM mais recente entre as filhas do compartilhamento."""
    requisicao = Request(f"{base_url}/", method="PROPFIND", headers=_cabecalhos({"Depth": "1"}))
    with urlopen(requisicao, timeout=60) as resposta:  # noqa: S310 - URL vem de settings, não de input do usuário
        corpo = resposta.read()
    raiz = ET.fromstring(corpo)  # noqa: S314 - resposta WebDAV do host configurado, não input arbitrário
    periodos = set()
    for item in raiz.findall("d:response", _NS_DAV):
        href = item.findtext("d:href", default="", namespaces=_NS_DAV)
        nome = href.rstrip("/").rsplit("/", 1)[-1]
        if re.fullmatch(r"\d{4}-\d{2}", nome):
            periodos.add(nome)
    if not periodos:
        raise RuntimeError("Não foi possível descobrir o período mais recente no compartilhamento configurado")
    return sorted(periodos)[-1]


def _linhas_csv_do_zip(conteudo_zip: bytes) -> Iterator[list[str]]:
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as arquivo:
        (nome_interno,) = arquivo.namelist()
        with arquivo.open(nome_interno) as bruto:
            texto = io.TextIOWrapper(bruto, encoding="latin-1", newline="")
            yield from csv.reader(texto, delimiter=";")


def _registros(conteudo_zip: bytes, colunas: tuple[str, ...]) -> Iterator[dict[str, str]]:
    for linha in _linhas_csv_do_zip(conteudo_zip):
        if len(linha) < len(colunas):
            continue
        yield dict(zip(colunas, linha, strict=False))


def carregar_referencia(base_url: str, periodo: str, prefixo_arquivo: str) -> dict[str, str]:
    """Municipios.zip/Cnaes.zip etc: um único arquivo pequeno (código -> descrição)."""
    conteudo = _baixar(f"{base_url}/{periodo}/{prefixo_arquivo}.zip")
    return {reg["codigo"].strip(): reg["descricao"].strip() for reg in _registros(conteudo, COLUNAS_REFERENCIA)}


def carregar_empresas_por_cnpj_basico(base_url: str, periodo: str) -> dict[str, tuple[str, str]]:
    """Empresas0..9.zip -- só guarda cnpj_basico -> (porte_empresa, razao_social),
    para não segurar capital social/natureza jurídica/etc. de ~50M empresas em memória."""
    empresas: dict[str, tuple[str, str]] = {}
    for indice in range(10):
        conteudo = _baixar(f"{base_url}/{periodo}/Empresas{indice}.zip")
        for registro in _registros(conteudo, COLUNAS_EMPRESA):
            empresas[registro["cnpj_basico"]] = (registro["porte_empresa"], registro["razao_social"])
        logger.info("Empresas%s.zip processado (%d cnpj_basico acumulados)", indice, len(empresas))
    return empresas


async def importar(periodo: str | None = None, limite_linhas: int | None = None, base_url: str | None = None) -> dict:
    base_url = base_url or get_settings().rfb_cnpj_base_url
    periodo = periodo or descobrir_periodo_mais_recente(base_url)
    logger.info("Período selecionado: %s (fonte: %s)", periodo, base_url)

    municipios = carregar_referencia(base_url, periodo, "Municipios")
    empresas = carregar_empresas_por_cnpj_basico(base_url, periodo)

    total_processados = 0
    total_validos = 0
    lote: list[dict] = []

    async with session_factory() as session:
        for indice in range(10):
            conteudo = _baixar(f"{base_url}/{periodo}/Estabelecimentos{indice}.zip")
            for estabelecimento in _registros(conteudo, COLUNAS_ESTABELECIMENTO):
                total_processados += 1
                porte_empresa, razao_social = empresas.get(estabelecimento["cnpj_basico"], (None, None))
                if not razao_social:
                    continue
                registro = montar_registro_cache(
                    estabelecimento,
                    porte_empresa=porte_empresa,
                    razao_social=razao_social,
                    municipios=municipios,
                )
                if registro is not None:
                    lote.append(registro)
                    total_validos += 1
                if len(lote) >= TAMANHO_LOTE_UPSERT:
                    await _upsert_lote(session, lote)
                    await session.commit()
                    lote = []
                if limite_linhas and total_processados >= limite_linhas:
                    break
            logger.info("Estabelecimentos%s.zip processado (%d válidos até agora)", indice, total_validos)
            if limite_linhas and total_processados >= limite_linhas:
                break
        if lote:
            await _upsert_lote(session, lote)
            await session.commit()

    return {"periodo": periodo, "processados": total_processados, "validos": total_validos}


async def _upsert_lote(session, lote: list[dict]) -> None:
    comando = insert(CacheEstabelecimentoRFB).values(lote)
    colunas_atualizaveis = {
        coluna.name: getattr(comando.excluded, coluna.name)
        for coluna in CacheEstabelecimentoRFB.__table__.columns
        if coluna.name != "cnpj"
    }
    comando = comando.on_conflict_do_update(index_elements=["cnpj"], set_=colunas_atualizaveis)
    await session.execute(comando)


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa os Dados Abertos do CNPJ (RFB) para o cache do Radar.")
    parser.add_argument("--periodo", default=None, help="AAAA-MM. Sem isso, descobre o mês mais recente.")
    parser.add_argument("--limite-linhas", type=int, default=None, help="Só para teste manual em homologação.")
    parser.add_argument("--base-url", default=None, help="Sobrescreve app.settings.rfb_cnpj_base_url.")
    argumentos = parser.parse_args()
    inicio = datetime.now()
    resultado = asyncio.run(importar(argumentos.periodo, argumentos.limite_linhas, argumentos.base_url))
    duracao = (datetime.now() - inicio).total_seconds()
    print(
        f"Período {resultado['periodo']}: {resultado['validos']}/{resultado['processados']} "
        f"estabelecimentos válidos em {duracao:.0f}s"
    )


if __name__ == "__main__":
    main()
