"""Mede a velocidade da construção do dataset histórico de ML e protege contra regressão.

Roda `construir_dataset_historico` contra o banco real (não o dataset inteiro por
padrão — um `--limite` menor já é representativo) e reporta rótulos/s e pares/s. Serve
para checar rapidamente, antes de disparar uma reconstrução completa, se uma mudança
recente (nova feature, nova consulta por par) deixou o processo sensivelmente mais
lento — o tipo de regressão que hoje só aparecia horas depois, no fim da execução.
"""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from app.database import session_factory
from app.trademarks.learning import construir_dataset_historico


async def avaliar_velocidade(*, limite: int, candidatos_por_processo: int) -> dict[str, Any]:
    async with session_factory() as session:
        inicio = perf_counter()
        rotulos, pares = await construir_dataset_historico(
            session,
            limite=limite,
            candidatos_por_processo=candidatos_por_processo,
        )
        duracao_s = perf_counter() - inicio
    return {
        "limite": limite,
        "candidatos_por_processo": candidatos_por_processo,
        "rotulos_processados": rotulos,
        "pares_processados": pares,
        "duracao_s": round(duracao_s, 3),
        "ms_por_rotulo": round((duracao_s * 1000) / rotulos, 3) if rotulos else 0.0,
        "ms_por_par": round((duracao_s * 1000) / pares, 3) if pares else 0.0,
    }


def avaliar_regressao_velocidade(atual: dict[str, Any], baseline: dict[str, Any], tolerancia: float) -> list[str]:
    """Compara `ms_por_rotulo` atual contra o baseline; tolerância é fração (0.5 = 50%)."""
    falhas: list[str] = []
    anterior = float(baseline["ms_por_rotulo"])
    corrente = float(atual["ms_por_rotulo"])
    if anterior > 0 and corrente > anterior * (1 + tolerancia):
        falhas.append(
            f"ms_por_rotulo regrediu: baseline={anterior:.2f}ms, atual={corrente:.2f}ms "
            f"(tolerância={tolerancia:.0%})"
        )
    return falhas


async def executar(args: argparse.Namespace) -> None:
    resultado = await avaliar_velocidade(limite=args.limite, candidatos_por_processo=args.candidatos)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))

    if args.salvar_baseline:
        args.salvar_baseline.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Baseline salvo em {args.salvar_baseline}")

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        falhas = avaliar_regressao_velocidade(resultado, baseline, args.tolerancia)
        if falhas:
            for falha in falhas:
                print(f"REGRESSÃO: {falha}")
            raise SystemExit(1)
        print("Sem regressão de velocidade em relação ao baseline.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mede a velocidade da construção do dataset histórico e, opcionalmente, "
        "compara contra um baseline salvo."
    )
    parser.add_argument("--limite", type=int, default=500)
    parser.add_argument("--candidatos", type=int, default=12)
    parser.add_argument("--baseline", type=Path, default=None, help="Arquivo JSON com um resultado anterior.")
    parser.add_argument("--salvar-baseline", type=Path, default=None, help="Onde salvar o resultado desta execução.")
    parser.add_argument("--tolerancia", type=float, default=0.5, help="Regressão máxima aceita (fração, ex: 0.5).")
    args = parser.parse_args()
    asyncio.run(executar(args))


if __name__ == "__main__":
    main()
