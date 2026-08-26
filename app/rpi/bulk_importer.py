from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256

import asyncpg

from app.normalization import normalizar_numero_processo
from app.rpi.types import RegistroRpi
from app.trademarks.status import normalizar_despacho


@dataclass(slots=True)
class EstatisticasImportacaoRpi:
    registros: int = 0
    titulares: int = 0
    classes: int = 0
    movimentacoes: int = 0

    def adicionar(self, lote: list[RegistroRpi]) -> None:
        self.registros += len(lote)
        self.titulares += sum(len(registro.titulares) for registro in lote)
        self.classes += sum(len(registro.classificacoes) for registro in lote)
        self.movimentacoes += sum(len(registro.movimentacoes) for registro in lote)


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


def _situacao_registro(registro: RegistroRpi) -> tuple[str, str]:
    ultimo = registro.movimentacoes[-1] if registro.movimentacoes else None
    normalizada = normalizar_despacho(
        ultimo.codigo if ultimo else None,
        ultimo.descricao if ultimo else registro.situacao,
    )
    return normalizada.codigo, normalizada.relevancia


async def importar_rpi_em_lotes(
    database_url: str,
    registros: Iterable[RegistroRpi],
    tamanho_lote: int = 10_000,
    progresso: Callable[[int], None] | None = None,
) -> EstatisticasImportacaoRpi:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conexao = await asyncpg.connect(dsn=dsn)
    estatisticas = EstatisticasImportacaoRpi()

    try:
        await _criar_tabelas_temporarias(conexao)
        lote: list[RegistroRpi] = []
        for registro in registros:
            lote.append(registro)
            if len(lote) < tamanho_lote:
                continue
            await _importar_lote(conexao, lote)
            estatisticas.adicionar(lote)
            lote.clear()
            if progresso:
                progresso(estatisticas.registros)

        if lote:
            await _importar_lote(conexao, lote)
            estatisticas.adicionar(lote)
            if progresso:
                progresso(estatisticas.registros)
    finally:
        await conexao.close()

    return estatisticas


async def _criar_tabelas_temporarias(conexao: asyncpg.Connection) -> None:
    await conexao.execute(
        """
        CREATE TEMP TABLE rpi_processos_lote (
            numero text NOT NULL,
            tipo varchar(10) NOT NULL,
            titulo text,
            data_deposito date,
            situacao text,
            situacao_normalizada varchar(30),
            relevancia_situacao varchar(20),
            numero_rpi integer NOT NULL,
            ordem integer NOT NULL DEFAULT 0,
            apresentacao text,
            natureza text,
            elemento_nominativo text,
            procurador text,
            imagem_url text
        ) ON COMMIT PRESERVE ROWS;
        CREATE TEMP TABLE rpi_titulares_lote (
            numero text NOT NULL,
            nome text NOT NULL,
            pais varchar(2)
        ) ON COMMIT PRESERVE ROWS;
        CREATE TEMP TABLE rpi_movimentacoes_lote (
            numero text NOT NULL,
            codigo text,
            descricao text NOT NULL,
            data_rpi date NOT NULL,
            numero_rpi integer NOT NULL,
            fonte_arquivo text NOT NULL,
            chave_origem varchar(64) NOT NULL
        ) ON COMMIT PRESERVE ROWS;
        CREATE TEMP TABLE rpi_classificacoes_lote (
            numero text NOT NULL,
            sistema varchar(20) NOT NULL,
            codigo varchar(30) NOT NULL,
            edicao varchar(20),
            especificacao text,
            status varchar(255)
        ) ON COMMIT PRESERVE ROWS
        """
    )


async def _importar_lote(conexao: asyncpg.Connection, lote: list[RegistroRpi]) -> None:
    processos = []
    for registro in lote:
        if not registro.numero:
            continue
        situacao_normalizada, relevancia_situacao = _situacao_registro(registro)
        processos.append(
            (
                registro.numero,
                registro.tipo.value,
                registro.titulo,
                registro.data_deposito,
                registro.situacao,
                situacao_normalizada,
                relevancia_situacao,
                registro.numero_rpi,
                registro.ordem,
                registro.apresentacao,
                registro.natureza,
                registro.elemento_nominativo,
                registro.procurador,
                registro.imagem_url,
            )
        )
    titulares = [
        (registro.numero, titular.nome, titular.pais)
        for registro in lote
        for titular in registro.titulares
        if registro.numero and titular.nome
    ]
    movimentacoes = [
        (
            registro.numero,
            movimento.codigo,
            movimento.descricao,
            registro.data_rpi,
            registro.numero_rpi,
            registro.fonte_arquivo,
            _chave_movimentacao(registro, movimento.codigo, movimento.descricao),
        )
        for registro in lote
        for movimento in registro.movimentacoes
        if registro.numero
    ]
    classificacoes = [
        (
            registro.numero,
            classificacao.sistema,
            classificacao.codigo,
            classificacao.edicao,
            classificacao.especificacao,
            classificacao.status,
        )
        for registro in lote
        for classificacao in registro.classificacoes
        if registro.numero and classificacao.codigo
    ]

    async with conexao.transaction():
        if processos:
            await conexao.copy_records_to_table(
                "rpi_processos_lote",
                records=processos,
                columns=(
                    "numero",
                    "tipo",
                    "titulo",
                    "data_deposito",
                    "situacao",
                    "situacao_normalizada",
                    "relevancia_situacao",
                    "numero_rpi",
                    "ordem",
                    "apresentacao",
                    "natureza",
                    "elemento_nominativo",
                    "procurador",
                    "imagem_url",
                ),
            )
            await conexao.execute(
                """
                INSERT INTO processos (
                    numero, numero_normalizado, tipo, titulo,
                    data_deposito, situacao, fonte, apresentacao,
                    natureza, elemento_nominativo, procurador, imagem_url,
                    situacao_normalizada, relevancia_situacao
                )
                SELECT DISTINCT ON (numero_normalizado)
                    numero,
                    numero_normalizado,
                    tipo,
                    titulo,
                    data_deposito,
                    situacao,
                    'RPI ' || numero_rpi,
                    apresentacao,
                    natureza,
                    elemento_nominativo,
                    procurador,
                    imagem_url,
                    situacao_normalizada,
                    relevancia_situacao
                FROM (
                    SELECT *, upper(regexp_replace(numero, '[^A-Za-z0-9]', '', 'g'))
                        AS numero_normalizado
                    FROM rpi_processos_lote
                ) AS origem
                ORDER BY numero_normalizado, numero_rpi DESC, ordem DESC
                ON CONFLICT (numero_normalizado) DO UPDATE SET
                    titulo = coalesce(excluded.titulo, processos.titulo),
                    data_deposito = coalesce(excluded.data_deposito, processos.data_deposito),
                    situacao = coalesce(excluded.situacao, processos.situacao),
                    situacao_normalizada = excluded.situacao_normalizada,
                    relevancia_situacao = excluded.relevancia_situacao,
                    fonte = excluded.fonte,
                    apresentacao = coalesce(excluded.apresentacao, processos.apresentacao),
                    natureza = coalesce(excluded.natureza, processos.natureza),
                    elemento_nominativo = coalesce(
                        excluded.elemento_nominativo, processos.elemento_nominativo
                    ),
                    procurador = coalesce(excluded.procurador, processos.procurador),
                    imagem_url = coalesce(excluded.imagem_url, processos.imagem_url),
                    atualizado_em = now()
                """
            )

        if titulares:
            await conexao.copy_records_to_table(
                "rpi_titulares_lote",
                records=titulares,
                columns=("numero", "nome", "pais"),
            )
            await conexao.execute(
                """
                INSERT INTO titulares (nome, pais)
                SELECT DISTINCT nome, pais FROM rpi_titulares_lote
                ON CONFLICT (nome, pais) DO NOTHING
                """
            )
            await conexao.execute(
                """
                INSERT INTO processo_titulares (processo_id, titular_id)
                SELECT DISTINCT processo.id, titular.id
                FROM rpi_titulares_lote AS origem
                JOIN processos AS processo
                  ON processo.numero_normalizado = upper(
                      regexp_replace(origem.numero, '[^A-Za-z0-9]', '', 'g')
                  )
                JOIN titulares AS titular
                  ON titular.nome = origem.nome
                 AND titular.pais IS NOT DISTINCT FROM origem.pais
                ON CONFLICT DO NOTHING
                """
            )

        if movimentacoes:
            await conexao.copy_records_to_table(
                "rpi_movimentacoes_lote",
                records=movimentacoes,
                columns=(
                    "numero",
                    "codigo",
                    "descricao",
                    "data_rpi",
                    "numero_rpi",
                    "fonte_arquivo",
                    "chave_origem",
                ),
            )
            await conexao.execute(
                """
                INSERT INTO movimentacoes (
                    processo_id, codigo_despacho, descricao, data_rpi,
                    numero_rpi, fonte_arquivo, chave_origem
                )
                SELECT
                    processo.id, origem.codigo, origem.descricao, origem.data_rpi,
                    origem.numero_rpi, origem.fonte_arquivo, origem.chave_origem
                FROM rpi_movimentacoes_lote AS origem
                JOIN processos AS processo
                  ON processo.numero_normalizado = upper(
                      regexp_replace(origem.numero, '[^A-Za-z0-9]', '', 'g')
                  )
                ON CONFLICT (chave_origem) DO NOTHING
                """
            )

        if classificacoes:
            await conexao.copy_records_to_table(
                "rpi_classificacoes_lote",
                records=classificacoes,
                columns=(
                    "numero",
                    "sistema",
                    "codigo",
                    "edicao",
                    "especificacao",
                    "status",
                ),
            )
            await conexao.execute(
                """
                INSERT INTO classificacoes_marca (
                    processo_id, sistema, codigo, edicao, especificacao, status
                )
                SELECT DISTINCT ON (processo.id, origem.sistema, origem.codigo)
                    processo.id,
                    origem.sistema,
                    origem.codigo,
                    origem.edicao,
                    origem.especificacao,
                    origem.status
                FROM rpi_classificacoes_lote AS origem
                JOIN processos AS processo
                  ON processo.numero_normalizado = upper(
                      regexp_replace(origem.numero, '[^A-Za-z0-9]', '', 'g')
                  )
                ORDER BY processo.id, origem.sistema, origem.codigo
                ON CONFLICT (processo_id, sistema, codigo) DO UPDATE SET
                    edicao = coalesce(excluded.edicao, classificacoes_marca.edicao),
                    especificacao = coalesce(
                        excluded.especificacao, classificacoes_marca.especificacao
                    ),
                    status = coalesce(excluded.status, classificacoes_marca.status)
                """
            )

        await conexao.execute(
            "TRUNCATE rpi_processos_lote, rpi_titulares_lote, rpi_movimentacoes_lote, rpi_classificacoes_lote"
        )
