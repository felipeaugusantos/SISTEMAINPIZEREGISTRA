import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg

from app.request_context import novo_request_id
from app.rpi.latest import consultar_ultima_rpi
from app.settings import get_settings

POLL_PADRAO_SEGUNDOS = 10
RPI_INICIAL_PADRAO = 2900

# Agenda semanal da sincronizacao automatica: terca-feira as 10h (horario de
# Brasilia), com novas tentativas as 12h e 15h no mesmo dia caso a execucao
# anterior tenha falhado. Apos a tentativa das 15h (ou em caso de sucesso), a
# proxima verificacao volta para a terca-feira seguinte as 10h.
FUSO_AGENDA_RPI = ZoneInfo("America/Sao_Paulo")
DIA_SEMANA_AGENDA_RPI = 1  # segunda=0, terca=1, ... (datetime.weekday())
HORARIOS_AGENDA_RPI = (10, 12, 15)


def _proxima_terca_no_horario(referencia_local: datetime, hora: int) -> datetime:
    dias_ate_terca = (DIA_SEMANA_AGENDA_RPI - referencia_local.weekday()) % 7
    candidato = (referencia_local + timedelta(days=dias_ate_terca)).replace(
        hour=hora, minute=0, second=0, microsecond=0
    )
    if candidato <= referencia_local:
        candidato += timedelta(days=7)
    return candidato


def calcular_proxima_verificacao(agora_utc: datetime, sucesso: bool) -> datetime:
    """Calcula a proxima verificacao segundo a agenda semanal de terca-feira.

    Em caso de falha ocorrida numa terca-feira, agenda a proxima tentativa
    para o proximo horario do dia (12h ou 15h); esgotados os horarios do dia
    (ou em caso de sucesso, ou de falha fora de terca-feira), agenda para a
    terca-feira seguinte as 10h.
    """
    agora_local = agora_utc.astimezone(FUSO_AGENDA_RPI)
    if not sucesso and agora_local.weekday() == DIA_SEMANA_AGENDA_RPI:
        for hora in HORARIOS_AGENDA_RPI[1:]:
            candidato_local = agora_local.replace(hour=hora, minute=0, second=0, microsecond=0)
            if candidato_local > agora_local:
                return candidato_local.astimezone(UTC)
    return _proxima_terca_no_horario(agora_local, HORARIOS_AGENDA_RPI[0]).astimezone(UTC)


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mantém a Seção V — Marcas atualizada com as RPIs oficiais")
    parser.add_argument("--uma-vez", action="store_true")
    parser.add_argument("--diretorio", type=Path, default=Path("data/raw/rpis"))
    return parser.parse_args()


def _inteiro_ambiente(nome: str, padrao: int, minimo: int) -> int:
    valor = int(os.getenv(nome, str(padrao)))
    if valor < minimo:
        raise ValueError(f"{nome} deve ser maior ou igual a {minimo}")
    return valor


def intervalo_pendente(
    importadas: set[int],
    inicio_minimo: int,
    ultima_oficial: int,
) -> tuple[int, int] | None:
    pendentes = [numero for numero in range(inicio_minimo, ultima_oficial + 1) if numero not in importadas]
    if not pendentes:
        return None
    return min(pendentes), max(pendentes)


def _dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _garantir_estado(conexao: asyncpg.Connection) -> None:
    await conexao.execute(
        """
        INSERT INTO rpi_sync_estado (id, status, falhas_consecutivas, heartbeat_em)
        VALUES (1, 'iniciando', 0, now())
        ON CONFLICT (id) DO UPDATE SET heartbeat_em=excluded.heartbeat_em
        """
    )


async def _reivindicar_execucao(
    database_url: str,
    forcar_automatica: bool,
) -> tuple[int, str] | None:
    conexao = await asyncpg.connect(dsn=_dsn(database_url))
    try:
        async with conexao.transaction():
            await _garantir_estado(conexao)
            solicitada = await conexao.fetchrow(
                """
                SELECT id, origem FROM rpi_sync_execucoes
                WHERE status='solicitada'
                ORDER BY solicitado_em
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            if solicitada is not None:
                await conexao.execute(
                    """
                    UPDATE rpi_sync_execucoes
                    SET status='verificando', iniciado_em=now(), heartbeat_em=now()
                    WHERE id=$1
                    """,
                    solicitada["id"],
                )
                await conexao.execute(
                    """
                    UPDATE rpi_sync_estado
                    SET status='verificando', execucao_atual_id=$1, heartbeat_em=now(),
                        atualizado_em=now()
                    WHERE id=1
                    """,
                    solicitada["id"],
                )
                return solicitada["id"], solicitada["origem"]

            estado = await conexao.fetchrow("SELECT proxima_verificacao_em FROM rpi_sync_estado WHERE id=1 FOR UPDATE")
            proxima = estado["proxima_verificacao_em"] if estado else None
            agora = datetime.now(UTC)
            if not forcar_automatica and proxima is not None and proxima > agora:
                await conexao.execute("UPDATE rpi_sync_estado SET heartbeat_em=now(), atualizado_em=now() WHERE id=1")
                return None

            execucao_id = await conexao.fetchval(
                """
                INSERT INTO rpi_sync_execucoes (
                    origem, status, solicitado_por, request_id, iniciado_em, heartbeat_em
                )
                VALUES ('automatica', 'verificando', 'rpi-sync', $1, now(), now())
                RETURNING id
                """,
                novo_request_id(),
            )
            await conexao.execute(
                """
                UPDATE rpi_sync_estado
                SET status='verificando', execucao_atual_id=$1, heartbeat_em=now(),
                    atualizado_em=now()
                WHERE id=1
                """,
                execucao_id,
            )
            return execucao_id, "automatica"
    finally:
        await conexao.close()


async def _heartbeat(database_url: str, execucao_id: int | None = None) -> None:
    conexao = await asyncpg.connect(dsn=_dsn(database_url))
    try:
        await _garantir_estado(conexao)
        await conexao.execute("UPDATE rpi_sync_estado SET heartbeat_em=now(), atualizado_em=now() WHERE id=1")
        if execucao_id is not None:
            await conexao.execute(
                "UPDATE rpi_sync_execucoes SET heartbeat_em=now() WHERE id=$1",
                execucao_id,
            )
    finally:
        await conexao.close()


async def _executar_importador(
    database_url: str,
    execucao_id: int,
    numero_rpi: int,
    diretorio: Path,
) -> None:
    processo = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cli.sincronizar_rpis",
        "--inicio",
        str(numero_rpi),
        "--fim",
        str(numero_rpi),
        "--tipo",
        "marca",
        "--diretorio",
        str(diretorio),
    )
    while True:
        try:
            codigo = await asyncio.wait_for(processo.wait(), timeout=5)
            break
        except TimeoutError:
            await _heartbeat(database_url, execucao_id)
    if codigo != 0:
        raise RuntimeError(f"Importador da RPI {numero_rpi} encerrou com código {codigo}")


async def _processar_execucao(
    database_url: str,
    execucao_id: int,
    diretorio: Path,
    inicio_minimo: int,
) -> None:
    try:
        ultima_oficial = await asyncio.to_thread(consultar_ultima_rpi)
        conexao = await asyncpg.connect(dsn=_dsn(database_url))
        try:
            registros = await conexao.fetch("SELECT numero_rpi FROM rpi_importacoes WHERE tipo='marca'")
            importadas = {registro["numero_rpi"] for registro in registros}
            ultima_local = max(importadas) if importadas else None
            intervalo = intervalo_pendente(importadas, inicio_minimo, ultima_oficial)
            inicio = intervalo[0] if intervalo else None
            fim = intervalo[1] if intervalo else None
            pendentes = (
                [numero for numero in range(inicio, fim + 1) if numero not in importadas]
                if inicio is not None and fim is not None
                else []
            )
            await conexao.execute(
                """
                UPDATE rpi_sync_execucoes
                SET ultima_rpi_oficial=$2, ultima_rpi_local_antes=$3,
                    rpi_inicio=$4, rpi_fim=$5, edicoes_total=$6,
                    status=CASE WHEN $6 > 0 THEN 'importando' ELSE 'sem_atualizacoes' END,
                    heartbeat_em=now()
                WHERE id=$1
                """,
                execucao_id,
                ultima_oficial,
                ultima_local,
                inicio,
                fim,
                len(pendentes),
            )

            if not pendentes:
                proxima = calcular_proxima_verificacao(datetime.now(UTC), sucesso=True)
                await conexao.execute(
                    """
                    UPDATE rpi_sync_execucoes
                    SET ultima_rpi_local_depois=$2, mensagem='Base já estava atualizada',
                        finalizado_em=now(), heartbeat_em=now()
                    WHERE id=$1
                    """,
                    execucao_id,
                    ultima_local,
                )
                await conexao.execute(
                    """
                    UPDATE rpi_sync_estado
                    SET status='atualizado', execucao_atual_id=NULL,
                        ultima_rpi_oficial=$1, ultima_rpi_local=$2,
                        ultima_verificacao_em=now(), proxima_verificacao_em=$3,
                        heartbeat_em=now(), falhas_consecutivas=0, ultimo_erro=NULL,
                        atualizado_em=now()
                    WHERE id=1
                    """,
                    ultima_oficial,
                    ultima_local,
                    proxima,
                )
                print(
                    f"Base de marcas atualizada: RPI {ultima_local}; última oficial: RPI {ultima_oficial}",
                    flush=True,
                )
                return

            await conexao.execute(
                """
                UPDATE rpi_sync_estado
                SET status='importando', ultima_rpi_oficial=$1, ultima_rpi_local=$2,
                    heartbeat_em=now(), atualizado_em=now()
                WHERE id=1
                """,
                ultima_oficial,
                ultima_local,
            )
        finally:
            await conexao.close()

        for numero in pendentes:
            conexao = await asyncpg.connect(dsn=_dsn(database_url))
            try:
                await conexao.execute(
                    """
                    UPDATE rpi_sync_execucoes
                    SET rpi_atual=$2, heartbeat_em=now(),
                        mensagem=concat('Importando RPI ', $2::int)
                    WHERE id=$1
                    """,
                    execucao_id,
                    numero,
                )
            finally:
                await conexao.close()

            await _executar_importador(database_url, execucao_id, numero, diretorio)

            conexao = await asyncpg.connect(dsn=_dsn(database_url))
            try:
                estatisticas = await conexao.fetchrow(
                    """
                    SELECT registros_processados, titulares_processados,
                           classes_processadas, movimentacoes_processadas
                    FROM rpi_importacoes
                    WHERE numero_rpi=$1 AND tipo='marca'
                    """,
                    numero,
                )
                if estatisticas is None:
                    raise RuntimeError(f"RPI {numero} não foi registrada após a importação")
                await conexao.execute(
                    """
                    UPDATE rpi_sync_execucoes
                    SET edicoes_processadas=edicoes_processadas + 1,
                        registros_processados=registros_processados + $2,
                        titulares_processados=titulares_processados + $3,
                        classes_processadas=classes_processadas + $4,
                        movimentacoes_processadas=movimentacoes_processadas + $5,
                        heartbeat_em=now()
                    WHERE id=$1
                    """,
                    execucao_id,
                    estatisticas["registros_processados"],
                    estatisticas["titulares_processados"],
                    estatisticas["classes_processadas"],
                    estatisticas["movimentacoes_processadas"],
                )
            finally:
                await conexao.close()

        conexao = await asyncpg.connect(dsn=_dsn(database_url))
        try:
            ultima_local_depois = await conexao.fetchval(
                "SELECT max(numero_rpi) FROM rpi_importacoes WHERE tipo='marca'"
            )
            proxima = calcular_proxima_verificacao(datetime.now(UTC), sucesso=True)
            await conexao.execute(
                """
                UPDATE rpi_sync_execucoes
                SET status='concluida', ultima_rpi_local_depois=$2,
                    mensagem='Sincronização concluída', finalizado_em=now(),
                    heartbeat_em=now()
                WHERE id=$1
                """,
                execucao_id,
                ultima_local_depois,
            )
            await conexao.execute(
                """
                UPDATE rpi_sync_estado
                SET status='atualizado', execucao_atual_id=NULL,
                    ultima_rpi_oficial=$1, ultima_rpi_local=$2,
                    ultima_verificacao_em=now(), proxima_verificacao_em=$3,
                    heartbeat_em=now(), falhas_consecutivas=0, ultimo_erro=NULL,
                    atualizado_em=now()
                WHERE id=1
                """,
                ultima_oficial,
                ultima_local_depois,
                proxima,
            )
        finally:
            await conexao.close()
    except Exception as erro:
        mensagem = str(erro)[:4000]
        conexao = await asyncpg.connect(dsn=_dsn(database_url))
        try:
            proxima = calcular_proxima_verificacao(datetime.now(UTC), sucesso=False)
            await conexao.execute(
                """
                UPDATE rpi_sync_execucoes
                SET status='falhou', erro=$2, mensagem='Falha na sincronização',
                    finalizado_em=now(), heartbeat_em=now()
                WHERE id=$1
                """,
                execucao_id,
                mensagem,
            )
            await conexao.execute(
                """
                UPDATE rpi_sync_estado
                SET status='falhou', execucao_atual_id=NULL, ultimo_erro=$1,
                    falhas_consecutivas=falhas_consecutivas + 1,
                    ultima_verificacao_em=now(), proxima_verificacao_em=$2,
                    heartbeat_em=now(), atualizado_em=now()
                WHERE id=1
                """,
                mensagem,
                proxima,
            )
        finally:
            await conexao.close()
        print(f"Falha na sincronização automática: {mensagem}", file=sys.stderr, flush=True)


async def executar() -> None:
    args = argumentos()
    settings = get_settings()
    poll = _inteiro_ambiente("RPI_SYNC_POLL_SECONDS", POLL_PADRAO_SEGUNDOS, 5)
    inicio_minimo = _inteiro_ambiente("RPI_SYNC_START_NUMBER", RPI_INICIAL_PADRAO, 2404)

    while True:
        execucao = await _reivindicar_execucao(
            settings.database_url,
            forcar_automatica=args.uma_vez,
        )
        if execucao is not None:
            execucao_id, _origem = execucao
            await _processar_execucao(
                settings.database_url,
                execucao_id,
                args.diretorio,
                inicio_minimo,
            )
        else:
            await _heartbeat(settings.database_url)
        if args.uma_vez:
            return
        await asyncio.sleep(poll)


if __name__ == "__main__":
    asyncio.run(executar())
