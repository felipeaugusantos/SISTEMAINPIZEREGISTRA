from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Movimentacao, Processo, RpiImportacao, TipoProcesso

_COBERTURA_CACHE: tuple[float, tuple[int, object, object]] | None = None
_COBERTURA_TTL_SEGUNDOS = 600


async def avaliar_qualidade_base(
    session: AsyncSession,
    processos_encontrados: list[Processo],
) -> dict:
    ultima_importacao = (
        await session.execute(
            select(RpiImportacao)
            .where(RpiImportacao.tipo == TipoProcesso.MARCA.value)
            .order_by(RpiImportacao.numero_rpi.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    ultima_data_rpi = (
        await session.execute(
            select(func.max(Movimentacao.data_rpi)).join(Processo).where(
                Processo.tipo == TipoProcesso.MARCA
            )
        )
    ).scalar_one_or_none()
    global _COBERTURA_CACHE
    agora = monotonic()
    if _COBERTURA_CACHE and agora - _COBERTURA_CACHE[0] < _COBERTURA_TTL_SEGUNDOS:
        cobertura = _COBERTURA_CACHE[1]
    else:
        cobertura = (
            await session.execute(
                select(
                    func.count(Processo.id),
                    func.min(Processo.data_deposito),
                    func.max(Processo.data_deposito),
                ).where(Processo.tipo == TipoProcesso.MARCA)
            )
        ).one()
        _COBERTURA_CACHE = (agora, cobertura)

    sem_titulo = sum(not (processo.titulo or "").strip() for processo in processos_encontrados)
    sem_situacao = sum(
        not processo.situacao and not processo.situacao_normalizada
        for processo in processos_encontrados
    )
    sem_classes = sum(not processo.classificacoes for processo in processos_encontrados)
    avisos: list[str] = []

    hoje = datetime.now(UTC).date()
    idade_dias = (hoje - ultima_data_rpi).days if ultima_data_rpi else None
    if ultima_importacao is None or ultima_data_rpi is None:
        status = "bloqueada"
        avisos.append("Não foi possível comprovar uma importação da Seção V — Marcas.")
    elif idade_dias is not None and idade_dias > 14:
        status = "bloqueada"
        avisos.append(f"A última RPI disponível na base tem {idade_dias} dias.")
    else:
        status = "adequada"

    if sem_titulo:
        avisos.append(f"{sem_titulo} ocorrência(s) exibida(s) estão sem elemento nominativo.")
    if sem_situacao:
        avisos.append(f"{sem_situacao} ocorrência(s) exibida(s) estão sem situação consolidada.")
    if sem_classes:
        avisos.append(f"{sem_classes} ocorrência(s) exibida(s) estão sem classificação.")
    if avisos and status == "adequada":
        status = "atencao"

    return {
        "status": status,
        "ultima_rpi": ultima_importacao.numero_rpi if ultima_importacao else None,
        "data_ultima_rpi": ultima_data_rpi,
        "importada_em": ultima_importacao.importado_em if ultima_importacao else None,
        "idade_dias": idade_dias,
        "total_processos_marca": int(cobertura[0] or 0),
        "deposito_mais_antigo": cobertura[1],
        "deposito_mais_recente": cobertura[2],
        "ocorrencias_sem_titulo": sem_titulo,
        "ocorrencias_sem_situacao": sem_situacao,
        "ocorrencias_sem_classe": sem_classes,
        "avisos": avisos,
    }
