"""ETL dos Dados Abertos do CNPJ (Receita Federal) -> cache_estabelecimentos_rfb.

Fase 2 do Radar de Prospecção (03/09/2026,
docs/arquitetura-radar-prospeccao-2026-09-03.md). A RFB não particiona os
arquivos por UF/CNAE (são ~10 arquivos arbitrários por tipo cobrindo o Brasil
inteiro, vários GB compactados), então isto é um job de lote pesado e
demorado. Duas formas de rodar: standalone ("uv run python -m
app.cli.importar_cnpj_rfb", fora do request-response da aplicação, útil por
cron) ou disparado pela tela do Radar (superadmin), que enfileira o job
prospeccao.importar_cnpj_rfb no worker -- ver app/worker.py -- em vez de rodar
preso à sessão SSH/HTTP de quem clicou (esse acoplamento já causou uma
importação perdida no meio do download em 03/09/2026). A coleta *por
campanha* (rápida, por tenant) é outro código (app/api/prospeccao.py::
coletar_campanha), que só consulta este cache já pronto -- nunca baixa nada
da RFB na hora.

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

Memória: o índice cnpj_basico -> (porte_empresa, razao_social) das ~47M
empresas do Brasil é montado em DISCO (SQLite, dentro do cache do
período), nunca num dict Python inteiro em RAM. Achado de 03/09/2026: a
primeira versão guardava tudo num dict e sozinho consumia ~15,5GB -- quase
toda a RAM da VPS de produção (15GB) -- causando OOM-kill em loop (o
worker reprocessa automaticamente qualquer job "em andamento" ao
reiniciar, então cada kill disparava um novo crash).
"""

import argparse
import asyncio
import base64
import csv
import io
import logging
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from pathlib import Path
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
CallableProgresso = Callable[[str, int, int], Awaitable[None]]


class ImportacaoInterrompida(Exception):
    """Levantada pelo callback de progresso quando a execução foi parada pela tela."""


def _cabecalhos(extra: dict[str, str] | None = None) -> dict[str, str]:
    cabecalhos = {"User-Agent": USER_AGENT, **(extra or {})}
    token = get_settings().rfb_cnpj_share_token
    if token:
        credencial = base64.b64encode(f"{token}:".encode()).decode()
        cabecalhos["Authorization"] = f"Basic {credencial}"
    return cabecalhos


def _caminho_cache(url: str) -> Path | None:
    """Cache em disco por período+arquivo -- ver settings.rfb_cnpj_cache_dir.
    Achado de 03/09/2026: esta VPS reinicia sozinha algumas vezes por dia, o
    que já derrubou duas tentativas de importação no meio do download. Sem
    isso, cada nova tentativa rebaixa vários GB do zero."""
    cache_dir = get_settings().rfb_cnpj_cache_dir
    if not cache_dir:
        return None
    partes = url.rstrip("/").split("/")
    if len(partes) < 2:
        return None
    periodo, nome_arquivo = partes[-2], partes[-1]
    return Path(cache_dir) / periodo / nome_arquivo


def _baixar(url: str) -> bytes:
    caminho = _caminho_cache(url)
    if caminho is not None and caminho.is_file():
        logger.info("Usando arquivo em cache: %s", caminho)
        return caminho.read_bytes()
    requisicao = Request(url, headers=_cabecalhos())
    with urlopen(requisicao, timeout=300) as resposta:  # noqa: S310 - URL vem de settings, não de input do usuário
        conteudo = resposta.read()
    if caminho is not None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)
    return conteudo


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


def _caminho_indice_empresas(periodo: str) -> Path:
    cache_dir = get_settings().rfb_cnpj_cache_dir or tempfile.gettempdir()
    return Path(cache_dir) / periodo / "empresas_indice.sqlite3"


async def carregar_empresas_por_cnpj_basico(base_url: str, periodo: str, progresso: CallableProgresso | None = None) -> Path:
    """Empresas0..9.zip -- monta um índice cnpj_basico -> (porte_empresa,
    razao_social) EM DISCO (SQLite), não em dict Python.

    Achado de 03/09/2026: o dict em memória com as ~47M empresas do Brasil
    inteiro sozinho consumia ~15,5GB de RAM -- praticamente toda a RAM da
    VPS de produção (15GB) -- e o kernel matava o processo (OOM-kill). Como
    o worker reprocessa automaticamente qualquer job "em andamento" ao
    reiniciar, isso virou um loop infinito de crash. Cada arquivo
    Empresas{N}.zip (~1/10 do total) é processado e inserido em lote antes
    de descartar da memória, então o pico de RAM fica limitado ao tamanho
    de UM arquivo, não dos dez somados."""
    caminho = _caminho_indice_empresas(periodo)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.unlink(missing_ok=True)
    conexao = sqlite3.connect(str(caminho))
    try:
        conexao.execute("PRAGMA journal_mode=OFF")
        conexao.execute("PRAGMA synchronous=OFF")
        conexao.execute(
            "CREATE TABLE empresas (cnpj_basico TEXT PRIMARY KEY, porte_empresa TEXT, razao_social TEXT) "
            "WITHOUT ROWID"
        )
        total = 0
        for indice in range(10):
            conteudo = _baixar(f"{base_url}/{periodo}/Empresas{indice}.zip")
            lote = [
                (registro["cnpj_basico"], registro["porte_empresa"], registro["razao_social"])
                for registro in _registros(conteudo, COLUNAS_EMPRESA)
            ]
            conexao.executemany("INSERT OR REPLACE INTO empresas VALUES (?, ?, ?)", lote)
            conexao.commit()
            total += len(lote)
            logger.info("Empresas%s.zip processado (%d cnpj_basico no índice)", indice, total)
            if progresso:
                await progresso(f"Carregando empresas {indice + 1}/10 ({total} no índice)", 0, 0)
    finally:
        conexao.close()
    return caminho


async def importar(
    periodo: str | None = None,
    limite_linhas: int | None = None,
    base_url: str | None = None,
    progresso: CallableProgresso | None = None,
) -> dict:
    """progresso, quando informado, é chamado como
    `await progresso(etapa_atual: str, total_processados: int, total_validos: int)`
    em cada checkpoint significativo -- usado pelo job do worker
    (prospeccao.importar_cnpj_rfb) para persistir progresso visível à tela do
    Radar. A CLI standalone roda sem callback (só o logger)."""
    base_url = base_url or get_settings().rfb_cnpj_base_url
    periodo = periodo or descobrir_periodo_mais_recente(base_url)
    logger.info("Período selecionado: %s (fonte: %s)", periodo, base_url)
    if progresso:
        await progresso(f"Período selecionado: {periodo}", 0, 0)

    municipios = carregar_referencia(base_url, periodo, "Municipios")
    caminho_indice_empresas = await carregar_empresas_por_cnpj_basico(base_url, periodo, progresso)
    conexao_empresas = sqlite3.connect(str(caminho_indice_empresas))
    conexao_empresas.execute("PRAGMA mmap_size=2000000000")
    conexao_empresas.execute("PRAGMA cache_size=-200000")

    total_processados = 0
    total_validos = 0
    lote: list[dict] = []

    try:
        async with session_factory() as session:
            for indice in range(10):
                conteudo = _baixar(f"{base_url}/{periodo}/Estabelecimentos{indice}.zip")
                for estabelecimento in _registros(conteudo, COLUNAS_ESTABELECIMENTO):
                    total_processados += 1
                    linha = conexao_empresas.execute(
                        "SELECT porte_empresa, razao_social FROM empresas WHERE cnpj_basico = ?",
                        (estabelecimento["cnpj_basico"],),
                    ).fetchone()
                    porte_empresa, razao_social = linha if linha else (None, None)
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
                if progresso:
                    await progresso(f"Processando estabelecimentos {indice + 1}/10", total_processados, total_validos)
                if limite_linhas and total_processados >= limite_linhas:
                    break
            if lote:
                await _upsert_lote(session, lote)
                await session.commit()
    finally:
        conexao_empresas.close()

    if not limite_linhas:
        _limpar_cache(periodo)

    return {"periodo": periodo, "processados": total_processados, "validos": total_validos}


def _limpar_cache(periodo: str) -> None:
    """Só chamado após sucesso completo (sem --limite-linhas) -- o cache em
    disco existe como apoio pra retomar uma tentativa interrompida, não como
    armazenamento permanente."""
    cache_dir = get_settings().rfb_cnpj_cache_dir
    if not cache_dir:
        return
    caminho = Path(cache_dir) / periodo
    if caminho.is_dir():
        shutil.rmtree(caminho, ignore_errors=True)
        logger.info("Cache em disco de %s removido após importação bem-sucedida.", periodo)


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
