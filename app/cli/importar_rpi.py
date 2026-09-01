import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text

from app.database import session_factory
from app.models import TipoProcesso
from app.rpi.importer import importar_registros
from app.rpi.parsers import ler_marcas, ler_patentes

_SQL_VINCULAR_PRE_CADASTROS_NOVOS = """
    INSERT INTO processos_monitorados (
        organizacao_id, processo_id, empresa_id, responsavel_id, status,
        origem, observacoes, vinculado_por, criado_em, atualizado_em,
        etapa_kanban, ordem_kanban, etapa_atualizada_em, prioridade
    )
    SELECT pc.organizacao_id, proc.id, pc.empresa_id, pc.responsavel_id, 'ativo',
           'pre_cadastro', pc.observacoes, pc.criado_por, now(), now(),
           'triagem', 0, now(), 'media'
    FROM pre_cadastros_processo AS pc
    JOIN processos AS proc
      ON proc.numero_normalizado = pc.numero_normalizado AND proc.tipo = 'marca'
    WHERE pc.status = 'aguardando'
    ON CONFLICT (organizacao_id, processo_id) DO NOTHING
"""
_SQL_MARCAR_PRE_CADASTROS_VINCULADOS = """
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


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa uma edição XML da RPI")
    parser.add_argument("--arquivo", required=True, type=Path)
    parser.add_argument("--tipo", required=True, choices=[tipo.value for tipo in TipoProcesso])
    parser.add_argument("--limite", type=int)
    return parser.parse_args()


async def executar() -> None:
    args = argumentos()
    tipo = TipoProcesso(args.tipo)
    leitor = ler_marcas if tipo is TipoProcesso.MARCA else ler_patentes

    async with session_factory() as session:
        resultado = await importar_registros(session, leitor(args.arquivo), args.limite)
        # Escopo cruza organizacoes (varias podem ter pre-cadastrado o mesmo
        # numero) -- precisa do bypass de RLS igual outros jobs de sistema.
        await session.execute(text("SET app.superadmin = 'true'"))
        await session.execute(text(_SQL_VINCULAR_PRE_CADASTROS_NOVOS))
        await session.execute(text(_SQL_MARCAR_PRE_CADASTROS_VINCULADOS))
        await session.commit()

    print(json.dumps(asdict(resultado), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(executar())
