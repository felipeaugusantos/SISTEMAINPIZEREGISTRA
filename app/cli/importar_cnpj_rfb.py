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

Uso:
    uv run python -m app.cli.importar_cnpj_rfb [--periodo AAAA-MM] [--limite-linhas N]

Sem --periodo, descobre o mês mais recente publicado automaticamente.
--limite-linhas é só para teste manual em ambiente de homologação (evita
baixar o Brasil inteiro ao validar a integração pela primeira vez).
"""

import argparse
import asyncio
import csv
import io
import logging
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from urllib.request import Request, urlopen

from sqlalchemy.dialects.postgresql import insert

from app.database import session_factory
from app.models import CacheEstabelecimentoRFB
from app.rfb_cnpj import (
    BASE_URL_RFB,
    COLUNAS_EMPRESA,
    COLUNAS_ESTABELECIMENTO,
    COLUNAS_REFERENCIA,
    montar_registro_cache,
)

logger = logging.getLogger("ze_registra.importar_cnpj_rfb")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())

USER_AGENT = "INPI-API/0.1 (radar de prospeccao -- dados abertos CNPJ)"
TAMANHO_LOTE_UPSERT = 2000


def _baixar(url: str) -> bytes:
    requisicao = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(requisicao, timeout=300) as resposta:  # noqa: S310 - URL fixa da RFB, não vem de input do usuário
        return resposta.read()


def descobrir_periodo_mais_recente() -> str:
    """A RFB publica um índice HTML com as pastas AAAA-MM disponíveis."""
    html = _baixar(f"{BASE_URL_RFB}/").decode("utf-8", errors="replace")
    periodos = sorted(set(re.findall(r'href="(\d{4}-\d{2})/"', html)))
    if not periodos:
        raise RuntimeError("Não foi possível descobrir o período mais recente nos dados abertos da RFB")
    return periodos[-1]


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


def carregar_referencia(periodo: str, prefixo_arquivo: str) -> dict[str, str]:
    """Municipios.zip/Cnaes.zip etc: um único arquivo pequeno (código -> descrição)."""
    conteudo = _baixar(f"{BASE_URL_RFB}/{periodo}/{prefixo_arquivo}.zip")
    return {reg["codigo"].strip(): reg["descricao"].strip() for reg in _registros(conteudo, COLUNAS_REFERENCIA)}


def carregar_empresas_por_cnpj_basico(periodo: str) -> dict[str, tuple[str, str]]:
    """Empresas0..9.zip -- só guarda cnpj_basico -> (porte_empresa, razao_social),
    para não segurar capital social/natureza jurídica/etc. de ~50M empresas em memória."""
    empresas: dict[str, tuple[str, str]] = {}
    for indice in range(10):
        conteudo = _baixar(f"{BASE_URL_RFB}/{periodo}/Empresas{indice}.zip")
        for registro in _registros(conteudo, COLUNAS_EMPRESA):
            empresas[registro["cnpj_basico"]] = (registro["porte_empresa"], registro["razao_social"])
        logger.info("Empresas%s.zip processado (%d cnpj_basico acumulados)", indice, len(empresas))
    return empresas


async def importar(periodo: str | None = None, limite_linhas: int | None = None) -> dict:
    periodo = periodo or descobrir_periodo_mais_recente()
    logger.info("Período selecionado: %s", periodo)

    municipios = carregar_referencia(periodo, "Municipios")
    empresas = carregar_empresas_por_cnpj_basico(periodo)

    total_processados = 0
    total_validos = 0
    lote: list[dict] = []

    async with session_factory() as session:
        for indice in range(10):
            conteudo = _baixar(f"{BASE_URL_RFB}/{periodo}/Estabelecimentos{indice}.zip")
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
    argumentos = parser.parse_args()
    inicio = datetime.now()
    resultado = asyncio.run(importar(argumentos.periodo, argumentos.limite_linhas))
    duracao = (datetime.now() - inicio).total_seconds()
    print(
        f"Período {resultado['periodo']}: {resultado['validos']}/{resultado['processados']} "
        f"estabelecimentos válidos em {duracao:.0f}s"
    )


if __name__ == "__main__":
    main()
