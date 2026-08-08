import asyncio
import json

from sqlalchemy import text

from app.database import session_factory

COMANDO = text(
    """
    WITH ultimo AS (
        SELECT DISTINCT ON (m.processo_id)
            m.processo_id,
            m.descricao,
            lower(immutable_unaccent(coalesce(m.codigo_despacho, '') || ' ' || m.descricao))
                AS texto
        FROM movimentacoes m
        JOIN processos p ON p.id = m.processo_id
        WHERE p.tipo = 'marca'
        ORDER BY m.processo_id, m.data_rpi DESC, m.numero_rpi DESC, m.id DESC
    ),
    consolidado AS (
        SELECT
            processo_id,
            descricao,
            CASE
                WHEN texto LIKE '%indefer%' THEN 'indeferida'
                WHEN texto LIKE '%arquiv%' THEN 'arquivada'
                WHEN texto LIKE '%extinc%' OR texto LIKE '%extinto%'
                    OR texto LIKE '%caducidade%' THEN 'extinta'
                WHEN texto LIKE '%cancel%' THEN 'cancelada'
                WHEN texto LIKE '%deferimento%' OR texto LIKE '%deferido%' THEN 'deferida'
                WHEN texto LIKE '%concessao de registro%'
                    OR texto LIKE '%registro de marca concedido%'
                    OR texto LIKE '%registro em vigor%' THEN 'registrada'
                WHEN texto LIKE '%exigencia%' THEN 'exigencia'
                WHEN texto LIKE '%oposicao%' THEN 'oposicao'
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
                'registrada', 'deferida', 'publicada', 'em_exame',
                'exigencia', 'oposicao', 'recurso'
            ) THEN 'ativa'
            WHEN c.codigo IN ('indeferida', 'arquivada', 'extinta', 'cancelada') THEN 'inativa'
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


async def consolidar() -> int:
    async with session_factory() as session:
        resultado = await session.execute(COMANDO)
        await session.commit()
        return resultado.rowcount or 0


def main() -> None:
    quantidade = asyncio.run(consolidar())
    print(json.dumps({"processos_consolidados": quantidade}, ensure_ascii=False))


if __name__ == "__main__":
    main()
