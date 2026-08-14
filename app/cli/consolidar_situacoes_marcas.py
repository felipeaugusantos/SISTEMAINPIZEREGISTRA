import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import session_factory


def _comando(individual: bool) -> text:
    # Um único processo (individual) ou toda a base de marcas. A lógica de
    # classificação é idêntica; muda apenas o escopo do WHERE.
    filtro = "AND p.id = :processo_id" if individual else ""
    return text(
        f"""
    WITH ultimo AS (
        SELECT DISTINCT ON (m.processo_id)
            m.processo_id,
            m.descricao,
            lower(immutable_unaccent(coalesce(m.codigo_despacho, '') || ' ' || m.descricao))
                AS texto,
            EXISTS (
                SELECT 1
                FROM movimentacoes anterior
                WHERE anterior.processo_id = m.processo_id
                  AND lower(immutable_unaccent(coalesce(anterior.descricao, '')))
                      LIKE '%indeferimento do pedido%'
            ) AS teve_indeferimento_pedido
        FROM movimentacoes m
        JOIN processos p ON p.id = m.processo_id
        WHERE p.tipo = 'marca' {filtro}
        ORDER BY m.processo_id, m.data_rpi DESC, m.numero_rpi DESC, m.id DESC
    ),
    consolidado AS (
        SELECT
            processo_id,
            descricao,
            CASE
                WHEN texto LIKE '%considerar pedido inexistente%'
                    OR texto LIKE '%pedido considerado inexistente%' THEN 'inexistente'
                WHEN texto LIKE '%concessao de registro%'
                    OR texto LIKE '%registro de marca concedido%'
                    OR texto LIKE '%registro em vigor%' THEN 'registrada'
                WHEN texto LIKE '%arquiv%' THEN 'arquivada'
                WHEN texto LIKE '%extinc%' OR texto LIKE '%extinto%'
                    OR texto LIKE '%caducidade%' THEN 'extinta'
                WHEN texto LIKE '%cancel%' THEN 'cancelada'
                WHEN texto LIKE '%recurso nao provido%'
                    AND teve_indeferimento_pedido THEN 'indeferida'
                WHEN texto LIKE '%indeferimento do pedido%'
                    OR texto LIKE '%pedido de registro indeferido%' THEN 'indeferida'
                WHEN texto LIKE '%deferimento parcial do pedido%'
                    OR texto LIKE '%pedido parcialmente deferido%' THEN 'deferida_parcial'
                WHEN texto LIKE '%deferimento do pedido%'
                    OR texto LIKE '%pedido de registro deferido%' THEN 'deferida'
                WHEN texto LIKE '%deferimento da peticao%'
                    OR texto LIKE '%indeferimento da peticao%'
                    OR texto LIKE '%peticao deferida%'
                    OR texto LIKE '%peticao indeferida%' THEN 'peticao_decidida'
                WHEN texto LIKE '%exigencia%' THEN 'exigencia'
                WHEN texto LIKE '%oposicao%' THEN 'oposicao'
                WHEN texto LIKE '%recurso%provido%'
                    OR texto LIKE '%recurso%decisao mantida%' THEN 'recurso_decidido'
                WHEN texto LIKE '%recurso%' THEN 'recurso'
                WHEN texto LIKE '%sobrest%' OR texto LIKE '%suspens%' THEN 'suspensa'
                WHEN texto LIKE '%publicacao do pedido%'
                    OR texto LIKE '%pedido de registro para oposicao%' THEN 'publicada'
                WHEN texto LIKE '%exame de merito%' OR texto LIKE '%exame formal%'
                    OR texto LIKE '%em exame%' THEN 'em_exame'
                ELSE 'nao_classificada'
            END AS codigo
        FROM ultimo
    )
    UPDATE processos p
    SET
        situacao = c.descricao,
        situacao_normalizada = c.codigo,
        relevancia_situacao = CASE
            WHEN c.codigo IN (
                'registrada', 'deferida', 'deferida_parcial', 'publicada', 'em_exame',
                'exigencia', 'oposicao', 'recurso', 'recurso_decidido',
                'peticao_decidida', 'suspensa'
            ) THEN 'ativa'
            WHEN c.codigo IN (
                'indeferida', 'arquivada', 'inexistente', 'extinta', 'cancelada'
            ) THEN 'inativa'
            ELSE 'incerta'
        END,
        atualizado_em = now()
    FROM consolidado c
    WHERE p.id = c.processo_id
      AND (
        p.situacao IS DISTINCT FROM c.descricao
        OR p.situacao_normalizada IS DISTINCT FROM c.codigo
      )
    """
)


async def consolidar_situacao(session: AsyncSession, processo_id: int | None = None) -> int:
    """Reconsolida a situação de um processo (se informado) ou de toda a base.

    Não faz commit — a cargo do chamador. Retorna quantas linhas mudaram.
    """
    comando = _comando(processo_id is not None)
    parametros = {"processo_id": processo_id} if processo_id is not None else {}
    resultado = await session.execute(comando, parametros)
    return resultado.rowcount or 0


async def consolidar() -> int:
    async with session_factory() as session:
        quantidade = await consolidar_situacao(session)
        await session.commit()
        return quantidade


def main() -> None:
    quantidade = asyncio.run(consolidar())
    print(json.dumps({"processos_consolidados": quantidade}, ensure_ascii=False))


if __name__ == "__main__":
    main()
