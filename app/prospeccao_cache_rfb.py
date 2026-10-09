"""Importação automática do cache nacional de empresas a partir de arquivos
enviados para a VPS (pedido do usuário, 30/09/2026).

A Receita Federal passou a recusar conexões vindas da VPS. Os arquivos do
CNPJ são baixados num computador do escritório (scripts/
enviar-cnpj-rfb-para-vps.bat, agendado no Windows) e enviados para
rfb_cnpj_cache_dir/<período>/. Ao final do envio, o script grava o marcador
ENVIO_COMPLETO. Esta rotina, executada de hora em hora pelo worker, dispara
a importação quando encontra um período completo ainda não importado -- sem
acessar a Receita (o período vai no payload e todos os arquivos já estão no
cache em disco).
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportacaoCnpjRfb
from app.queueing import enfileirar
from app.settings import get_settings

logger = logging.getLogger(__name__)

MARCADOR_ENVIO_COMPLETO = "ENVIO_COMPLETO"
ARQUIVOS_NECESSARIOS: tuple[str, ...] = (
    "Municipios",
    *(f"Empresas{indice}" for indice in range(10)),
    *(f"Estabelecimentos{indice}" for indice in range(10)),
)
MAXIMO_FALHAS_POR_PERIODO = 3
SOLICITANTE_AUTOMATICO = "automático (arquivos enviados)"


PROGRESSO_ESTAGNADO = timedelta(minutes=60)


def importacao_abandonada(execucao: ImportacaoCnpjRfb) -> bool:
    """Achado 4 da auditoria (07/10/2026): considera abandonada tanto a
    execução que nunca registrou etapa (o worker não assumiu) quanto a que
    registrou etapa mas parou de avançar por tempo demais (o worker que a
    executava morreu). Antes, só o primeiro caso era recuperado -- e uma
    importação travada com etapa ficava presa em 'executando' para sempre."""
    agora = datetime.now(UTC)
    if not execucao.etapa_atual:
        return agora - execucao.solicitado_em > timedelta(hours=1)
    referencia = execucao.progresso_em or execucao.solicitado_em
    return agora - referencia > PROGRESSO_ESTAGNADO


# Compatibilidade com o nome interno anterior.
_abandonada = importacao_abandonada


def periodos_enviados_completos(cache_dir: str | None) -> list[str]:
    """Períodos com o marcador de envio completo e todos os arquivos, do mais recente ao mais antigo."""
    if not cache_dir or not Path(cache_dir).is_dir():
        return []
    periodos = [
        pasta.name
        for pasta in Path(cache_dir).iterdir()
        if pasta.is_dir()
        and re.fullmatch(r"\d{4}-\d{2}", pasta.name)
        and (pasta / MARCADOR_ENVIO_COMPLETO).is_file()
        and all((pasta / f"{nome}.zip").is_file() for nome in ARQUIVOS_NECESSARIOS)
    ]
    return sorted(periodos, reverse=True)


async def disparar_importacao_de_arquivos_enviados(session: AsyncSession) -> int | None:
    """Devolve o id da execução disparada, ou None quando não há o que fazer."""
    periodos = periodos_enviados_completos(get_settings().rfb_cnpj_cache_dir)
    if not periodos:
        return None
    periodo = periodos[0]

    em_andamento = await session.scalar(
        select(ImportacaoCnpjRfb).where(ImportacaoCnpjRfb.status == "executando").limit(1)
    )
    if em_andamento is not None and _abandonada(em_andamento):
        # Mesma regra do disparo manual: sem nenhuma etapa 1h depois, o job
        # nunca chegou ao worker -- libera em vez de travar a automação.
        em_andamento.status = "erro"
        em_andamento.erro = "Importação abandonada: sem progresso do worker."
        em_andamento.concluido_em = datetime.now(UTC)
        # Zera o token para cercar o worker antigo (revisão do Codex, PR #172).
        em_andamento.worker_token = None
        await session.flush()
    elif em_andamento is not None:
        return None
    # Já concluído, ou parado de propósito pela tela: não reimporta sozinho.
    encerrado = await session.scalar(
        select(ImportacaoCnpjRfb.id)
        .where(ImportacaoCnpjRfb.periodo == periodo, ImportacaoCnpjRfb.status.in_(("concluido", "cancelado")))
        .limit(1)
    )
    if encerrado is not None:
        return None
    falhas = await session.scalar(
        select(func.count(ImportacaoCnpjRfb.id)).where(
            ImportacaoCnpjRfb.periodo == periodo, ImportacaoCnpjRfb.status == "erro"
        )
    )
    if (falhas or 0) >= MAXIMO_FALHAS_POR_PERIODO:
        return None

    execucao = ImportacaoCnpjRfb(
        status="executando",
        periodo=periodo,
        solicitado_por=SOLICITANTE_AUTOMATICO,
        total_processados=0,
        total_validos=0,
        solicitado_em=datetime.now(UTC),
    )
    session.add(execucao)
    await session.flush()
    await session.commit()  # grava antes de enfileirar (ver disparar_importacao_cnpj_rfb)
    try:
        await enfileirar(
            "prospeccao.importar_cnpj_rfb", {"execucao_id": execucao.id, "periodo": periodo, "limite_linhas": None}
        )
    except Exception:
        execucao.status = "erro"
        execucao.erro = "Não foi possível colocar a importação na fila do worker."
        execucao.concluido_em = datetime.now(UTC)
        await session.commit()
        raise
    logger.info("Importação automática do cache nacional disparada para o período %s.", periodo)
    return execucao.id
