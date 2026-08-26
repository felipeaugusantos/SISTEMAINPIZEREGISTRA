from collections.abc import Iterable
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ClassificacaoMarca, Movimentacao, Processo, Titular
from app.normalization import normalizar_numero_processo
from app.rpi.types import RegistroRpi, ResultadoImportacao, TitularRpi
from app.trademarks.status import normalizar_despacho


def _chave_movimentacao(registro: RegistroRpi, codigo: str | None, descricao: str) -> str:
    conteudo = "|".join(
        (
            registro.tipo.value,
            str(registro.numero_rpi),
            normalizar_numero_processo(registro.numero),
            codigo or "",
            descricao,
        )
    )
    return sha256(conteudo.encode()).hexdigest()


async def _obter_titular(
    session: AsyncSession,
    titular_rpi: TitularRpi,
    cache: dict[tuple[str, str | None], Titular],
) -> Titular:
    chave = (titular_rpi.nome, titular_rpi.pais)
    if chave in cache:
        return cache[chave]

    consulta = select(Titular).where(
        Titular.nome == titular_rpi.nome,
        Titular.pais == titular_rpi.pais,
    )
    titular = (await session.execute(consulta)).scalar_one_or_none()
    if titular is None:
        titular = Titular(nome=titular_rpi.nome, pais=titular_rpi.pais)
        session.add(titular)

    cache[chave] = titular
    return titular


async def importar_registros(
    session: AsyncSession,
    registros: Iterable[RegistroRpi],
    limite: int | None = None,
) -> ResultadoImportacao:
    resultado = ResultadoImportacao()
    processos: dict[str, Processo] = {}
    titulares: dict[tuple[str, str | None], Titular] = {}

    for registro in registros:
        if limite is not None and resultado.registros_processados >= limite:
            break

        numero_normalizado = normalizar_numero_processo(registro.numero)
        ultimo_movimento = registro.movimentacoes[-1] if registro.movimentacoes else None
        situacao_normalizada = normalizar_despacho(
            ultimo_movimento.codigo if ultimo_movimento else None,
            ultimo_movimento.descricao if ultimo_movimento else registro.situacao,
        )
        processo = processos.get(numero_normalizado)
        if processo is None:
            consulta = (
                select(Processo)
                .where(Processo.numero_normalizado == numero_normalizado)
                .options(selectinload(Processo.titulares))
            )
            processo = (await session.execute(consulta)).scalar_one_or_none()

        if processo is None:
            processo = Processo(
                numero=registro.numero,
                numero_normalizado=numero_normalizado,
                tipo=registro.tipo,
                titulo=registro.titulo,
                data_deposito=registro.data_deposito,
                situacao=registro.situacao,
                situacao_normalizada=situacao_normalizada.codigo,
                relevancia_situacao=situacao_normalizada.relevancia,
                fonte=f"RPI {registro.numero_rpi}",
                apresentacao=registro.apresentacao,
                natureza=registro.natureza,
                elemento_nominativo=registro.elemento_nominativo,
                procurador=registro.procurador,
                imagem_url=registro.imagem_url,
                titulares=[],
            )
            session.add(processo)
            await session.flush()
            resultado.processos_novos += 1
        else:
            processo.titulo = registro.titulo or processo.titulo
            processo.data_deposito = registro.data_deposito or processo.data_deposito
            processo.situacao = registro.situacao or processo.situacao
            processo.situacao_normalizada = situacao_normalizada.codigo
            processo.relevancia_situacao = situacao_normalizada.relevancia
            processo.fonte = f"RPI {registro.numero_rpi}"
            processo.apresentacao = registro.apresentacao or processo.apresentacao
            processo.natureza = registro.natureza or processo.natureza
            processo.elemento_nominativo = registro.elemento_nominativo or processo.elemento_nominativo
            processo.procurador = registro.procurador or processo.procurador
            processo.imagem_url = registro.imagem_url or processo.imagem_url

        processos[numero_normalizado] = processo

        for titular_rpi in registro.titulares:
            titular = await _obter_titular(session, titular_rpi, titulares)
            if titular not in processo.titulares:
                processo.titulares.append(titular)

        for classificacao in registro.classificacoes:
            comando = (
                insert(ClassificacaoMarca)
                .values(
                    processo_id=processo.id,
                    sistema=classificacao.sistema,
                    codigo=classificacao.codigo,
                    edicao=classificacao.edicao,
                    especificacao=classificacao.especificacao,
                    status=classificacao.status,
                )
                .on_conflict_do_update(
                    constraint="uq_classificacoes_marca_processo_sistema_codigo",
                    set_={
                        "edicao": classificacao.edicao,
                        "especificacao": classificacao.especificacao,
                        "status": classificacao.status,
                    },
                )
            )
            await session.execute(comando)

        for movimentacao in registro.movimentacoes:
            comando = (
                insert(Movimentacao)
                .values(
                    processo_id=processo.id,
                    codigo_despacho=movimentacao.codigo,
                    descricao=movimentacao.descricao,
                    data_rpi=registro.data_rpi,
                    numero_rpi=registro.numero_rpi,
                    fonte_arquivo=registro.fonte_arquivo,
                    chave_origem=_chave_movimentacao(registro, movimentacao.codigo, movimentacao.descricao),
                )
                .on_conflict_do_nothing(constraint="uq_movimentacoes_chave_origem")
            )
            execucao = await session.execute(comando)
            resultado.movimentacoes_novas += execucao.rowcount or 0

        resultado.registros_processados += 1

    return resultado
