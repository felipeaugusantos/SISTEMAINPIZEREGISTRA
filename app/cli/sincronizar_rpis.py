import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

from app.models import TipoProcesso
from app.rpi.bulk_importer import importar_rpi_em_lotes
from app.rpi.integrity import avaliar_importacao, calcular_integridade_arquivo
from app.rpi.locking import adquirir_lock_sincronizacao, liberar_lock_sincronizacao
from app.rpi.parsers import ler_marcas, ler_patentes
from app.rpi.sync import baixar_e_extrair_rpi
from app.settings import get_settings


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Baixa e importa uma sequência de RPIs")
    parser.add_argument("--inicio", required=True, type=int)
    parser.add_argument("--fim", required=True, type=int)
    parser.add_argument(
        "--tipo",
        choices=("marca", "patente", "ambos"),
        default="ambos",
    )
    parser.add_argument("--diretorio", type=Path, default=Path("data/raw/rpis"))
    parser.add_argument("--forcar", action="store_true")
    return parser.parse_args()


async def _ja_importada(database_url: str, numero: int, tipo: TipoProcesso) -> bool:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    try:
        return bool(
            await conexao.fetchval(
                "SELECT EXISTS(SELECT 1 FROM rpi_importacoes WHERE numero_rpi=$1 AND tipo=$2)",
                numero,
                tipo.value,
            )
        )
    finally:
        await conexao.close()


async def _registrar_importacao(
    database_url: str,
    numero: int,
    tipo: TipoProcesso,
    registros: int,
    titulares: int,
    classes: int,
    movimentacoes: int,
    arquivo_sha256: str,
    arquivo_tamanho_bytes: int,
) -> None:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    try:
        mesma_edicao = await conexao.fetchrow(
            "SELECT * FROM rpi_importacoes WHERE numero_rpi=$1 AND tipo=$2",
            numero,
            tipo.value,
        )
        anterior = await conexao.fetchrow(
            """
            SELECT * FROM rpi_importacoes
            WHERE numero_rpi < $1 AND tipo=$2
            ORDER BY numero_rpi DESC LIMIT 1
            """,
            numero,
            tipo.value,
        )
        ultima_edicao = await conexao.fetchval(
            "SELECT max(numero_rpi) FROM rpi_importacoes WHERE numero_rpi < $1 AND tipo=$2",
            numero,
            tipo.value,
        )
        settings = get_settings()
        status_integridade, anomalias = avaliar_importacao(
            numero_rpi=numero,
            registros=registros,
            titulares=titulares,
            classes=classes,
            movimentacoes=movimentacoes,
            arquivo_tamanho_bytes=arquivo_tamanho_bytes,
            arquivo_sha256=arquivo_sha256,
            anterior=dict(anterior) if anterior else None,
            mesma_edicao_anterior=dict(mesma_edicao) if mesma_edicao else None,
            ultima_edicao_importada=ultima_edicao,
            razao_minima_registros=settings.rpi_minimum_record_ratio,
            minimo_referencia_registros=settings.rpi_anomaly_reference_minimum,
            exigir_classes=tipo is TipoProcesso.MARCA,
        )
        await conexao.execute(
            """
            INSERT INTO rpi_importacoes (
                numero_rpi, tipo, registros_processados,
                titulares_processados, classes_processadas, movimentacoes_processadas,
                arquivo_sha256, arquivo_tamanho_bytes, status_integridade, anomalias
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (numero_rpi, tipo) DO UPDATE SET
                registros_processados=excluded.registros_processados,
                titulares_processados=excluded.titulares_processados,
                classes_processadas=excluded.classes_processadas,
                movimentacoes_processadas=excluded.movimentacoes_processadas,
                arquivo_sha256=excluded.arquivo_sha256,
                arquivo_tamanho_bytes=excluded.arquivo_tamanho_bytes,
                status_integridade=excluded.status_integridade,
                anomalias=excluded.anomalias,
                importado_em=now()
            """,
            numero,
            tipo.value,
            registros,
            titulares,
            classes,
            movimentacoes,
            arquivo_sha256,
            arquivo_tamanho_bytes,
            status_integridade,
            json.dumps(anomalias, ensure_ascii=False),
        )
        if anomalias:
            await conexao.execute(
                """
                INSERT INTO alertas_sistema (
                    organizacao_id, severidade, codigo, mensagem, detalhes
                ) VALUES (NULL, $1, 'RPI_IMPORTACAO_ANOMALA', $2, $3::jsonb)
                """,
                "critica" if status_integridade == "erro" else "aviso",
                f"RPI {numero} ({tipo.value}) importada com anomalias de integridade.",
                json.dumps(
                    {"numero_rpi": numero, "tipo": tipo.value, "anomalias": anomalias},
                    ensure_ascii=False,
                ),
            )
    finally:
        await conexao.close()


async def executar() -> None:
    args = argumentos()
    if args.inicio > args.fim:
        raise SystemExit("--inicio deve ser menor ou igual a --fim")

    tipos = (
        (TipoProcesso.MARCA, TipoProcesso.PATENTE)
        if args.tipo == "ambos"
        else (TipoProcesso(args.tipo),)
    )
    database_url = get_settings().database_url
    lock = await adquirir_lock_sincronizacao(database_url)
    if lock is None:
        print("Outra sincronização de RPIs já está em execução", file=sys.stderr, flush=True)
        return

    for numero in range(args.inicio, args.fim + 1):
        for tipo in tipos:
            if not args.forcar and await _ja_importada(database_url, numero, tipo):
                print(f"RPI {numero} ({tipo.value}) já importada", file=sys.stderr)
                continue

            xml = baixar_e_extrair_rpi(numero, tipo, args.diretorio, print)
            arquivo_sha256, arquivo_tamanho_bytes = calcular_integridade_arquivo(xml)
            leitor = ler_marcas if tipo is TipoProcesso.MARCA else ler_patentes
            estatisticas = await importar_rpi_em_lotes(database_url, leitor(xml))
            await _registrar_importacao(
                database_url,
                numero,
                tipo,
                estatisticas.registros,
                estatisticas.titulares,
                estatisticas.classes,
                estatisticas.movimentacoes,
                arquivo_sha256,
                arquivo_tamanho_bytes,
            )
            print(
                f"RPI {numero} ({tipo.value}): {estatisticas.registros:,} registros",
                flush=True,
            )

    await liberar_lock_sincronizacao(lock)


if __name__ == "__main__":
    asyncio.run(executar())
