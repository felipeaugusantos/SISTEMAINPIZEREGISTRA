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

        await _vincular_pre_cadastros_pendentes(conexao)
    finally:
        await conexao.close()

    return estatisticas


async def _vincular_pre_cadastros_pendentes(conexao: asyncpg.Connection) -> None:
    """Vincula automaticamente processos pre-cadastrados (numero informado antes
    da RPI publicar) assim que o numero correspondente aparece em `processos`.

    Roda em toda sincronizacao da RPI, entao tambem cobre pre-cadastros cujo
    processo ja tinha sido publicado antes desta execucao especifica.
    """
    async with conexao.transaction():
        # Escopo cruza organizacoes (varias podem ter pre-cadastrado o mesmo
        # numero) -- precisa do bypass de RLS igual outros jobs de sistema.
        await conexao.execute("SET LOCAL app.superadmin = 'true'")
        await conexao.execute(
            """
            INSERT INTO processos_monitorados (
                organizacao_id, processo_id, empresa_id, responsavel_id, lead_id, status,
                origem, observacoes, vinculado_por, criado_em, atualizado_em,
                etapa_kanban, ordem_kanban, etapa_atualizada_em, prioridade
            )
            SELECT pc.organizacao_id, proc.id, pc.empresa_id, pc.responsavel_id,
                   (
                       SELECT l.id FROM leads AS l
                       WHERE l.organizacao_id = pc.organizacao_id
                         AND l.arquivado_em IS NULL
                         AND l.processo_numero IS NOT NULL
                         AND upper(regexp_replace(l.processo_numero, '[^A-Za-z0-9]', '', 'g')) = proc.numero_normalizado
                       LIMIT 1
                   ),
                   'ativo',
                   'pre_cadastro', pc.observacoes, pc.criado_por, now(), now(),
                   'triagem', 0, now(), 'media'
            FROM pre_cadastros_processo AS pc
            JOIN processos AS proc
              ON proc.numero_normalizado = pc.numero_normalizado AND proc.tipo = 'marca'
            WHERE pc.status = 'aguardando'
            ON CONFLICT (organizacao_id, processo_id) DO NOTHING
            """
        )
        await conexao.execute(
            """
            UPDATE pre_cadastros_processo AS pc
            SET status = 'vinculado',
                vinculado_em = now(),
                processo_monitorado_id = pm.id,
                titular_divergente = NOT EXISTS (
                    SELECT 1 FROM processo_titulares AS pt
                    JOIN titulares AS t ON t.id = pt.titular_id
                    WHERE pt.processo_id = pm.processo_id
                      AND (
                            unaccent(lower(t.nome)) = unaccent(lower(pc.titular))
                         OR unaccent(lower(t.nome)) LIKE '%' || unaccent(lower(pc.titular)) || '%'
                         OR unaccent(lower(pc.titular)) LIKE '%' || unaccent(lower(t.nome)) || '%'
                      )
                )
            FROM processos_monitorados AS pm
            JOIN processos AS proc ON proc.id = pm.processo_id
            WHERE pc.status = 'aguardando'
              AND pc.organizacao_id = pm.organizacao_id
              AND proc.numero_normalizado = pc.numero_normalizado
            """
        )


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
            # A constraint uq_titulares_nome_pais não existe hoje em produção
            # (índice corrompido derrubado num incidente de restore em
            # 16/09/2026, reconciliação dos 13.836 grupos duplicados ainda
            # pendente de revisão humana) -- sem ela, o Postgres rejeita
            # "ON CONFLICT (nome, pais)" com InvalidColumnReferenceError.
            # NOT EXISTS não depende de constraint nenhuma; a importação da
            # RPI roda uma execução por vez, então não há corrida real aqui.
            await conexao.execute(
                """
                INSERT INTO titulares (nome, pais)
                SELECT DISTINCT origem.nome, origem.pais
                FROM rpi_titulares_lote AS origem
                WHERE NOT EXISTS (
                    SELECT 1 FROM titulares AS existente
                    WHERE existente.nome = origem.nome
                      AND existente.pais IS NOT DISTINCT FROM origem.pais
                )
                """
            )
            # Achado do Codex (PR #108): com os 13.836 grupos duplicados que
            # ainda existem em titulares, um JOIN direto por (nome, pais)
            # bate em TODAS as linhas duplicadas do grupo, associando o
            # processo a cada duplicata. O LATERAL abaixo escolhe sempre o
            # id canônico (o mais antigo) do grupo, então cada processo fica
            # ligado a um titular só, mesmo enquanto a duplicata existir.
            await conexao.execute(
                """
                INSERT INTO processo_titulares (processo_id, titular_id)
                SELECT DISTINCT processo.id, titular.id
                FROM rpi_titulares_lote AS origem
                JOIN processos AS processo
                  ON processo.numero_normalizado = upper(
                      regexp_replace(origem.numero, '[^A-Za-z0-9]', '', 'g')
                  )
                JOIN LATERAL (
                    SELECT t.id
                    FROM titulares AS t
                    WHERE t.nome = origem.nome
                      AND t.pais IS NOT DISTINCT FROM origem.pais
                    ORDER BY t.id
                    LIMIT 1
                ) AS titular ON true
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
