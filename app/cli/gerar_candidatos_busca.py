"""Gera casos candidatos para o benchmark de busca a partir de despachos oficiais do INPI.

Diferente dos 3 casos escritos à mão que existiam até então, cada caso aqui vem de um
indeferimento por conflito anterior extraído e classificado com alta confiança do
despacho público do INPI (ver app.trademarks.decision_evidence) — a marca citada como
conflitante (`processos_citados`) é a que o exame oficial de fato apontou, não uma
suposição. Continua marcado como "pendente_revisao_especialista": aprovar o dataset é
julgamento jurídico, não algo que este script decide sozinho.
"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from app.database import session_factory
from app.models import ClassificacaoMarca, EvidenciaDecisaoMarca, Processo


async def _gerar(limite: int) -> dict:
    async with session_factory() as session:
        classe = (
            select(
                ClassificacaoMarca.processo_id,
                func.min(ClassificacaoMarca.codigo).label("classe"),
            )
            .where(ClassificacaoMarca.sistema.in_(["NCL", "NICE", "nice"]))
            .group_by(ClassificacaoMarca.processo_id)
            .subquery()
        )
        linhas = (
            await session.execute(
                select(EvidenciaDecisaoMarca, Processo, classe.c.classe)
                .join(Processo, Processo.numero == EvidenciaDecisaoMarca.processo_numero)
                .outerjoin(classe, classe.c.processo_id == Processo.id)
                .where(
                    EvidenciaDecisaoMarca.fundamento_sugerido == "conflito_anterior",
                    EvidenciaDecisaoMarca.confianca >= 0.95,
                    Processo.titulo.is_not(None),
                )
                .order_by(EvidenciaDecisaoMarca.coletado_em.desc())
                .limit(limite)
            )
        ).all()

    casos = []
    ids_vistos: set[str] = set()
    for evidencia, processo, classe_codigo in linhas:
        citados = [str(numero) for numero in (evidencia.processos_citados or []) if numero]
        if not citados:
            continue
        caso_id = f"inpi-conflito-{processo.numero}"
        if caso_id in ids_vistos:
            continue
        ids_vistos.add(caso_id)
        casos.append(
            {
                "id": caso_id,
                "marca": processo.titulo,
                "classe_nice": classe_codigo,
                "tipo_pesquisa": "dupla",
                "processos_esperados": citados,
                "processos_criticos": citados,
                "justificativa": (
                    f"Processo {processo.numero} indeferido por conflito anterior; despacho oficial "
                    f"do INPI cita {', '.join(citados)} como anterioridade(s). "
                    f"Fonte: {evidencia.fonte_url} (hash {evidencia.hash_conteudo[:12]}...)."
                ),
            }
        )

    return {
        "dataset_version": "acervo-inpi-conflitos-oficiais-v1",
        "revisao": {
            "status": "pendente_revisao_especialista",
            "revisado_por": None,
            "papel": None,
            "revisado_em": None,
            "observacoes": (
                f"{len(casos)} casos gerados automaticamente a partir de despachos oficiais do INPI "
                "com indeferimento por conflito anterior (confiança >= 0,95). Cada caso tem link "
                "rastreável ao despacho original em 'justificativa'. Pendente de revisão jurídica "
                "antes de virar baseline aprovada."
            ),
        },
        "casos": casos,
    }


async def executar(args: argparse.Namespace) -> None:
    dataset = await _gerar(args.limite)
    args.saida.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(dataset['casos'])} casos gerados em {args.saida}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera casos candidatos de benchmark de busca a partir de despachos oficiais do INPI."
    )
    parser.add_argument("--limite", type=int, default=200)
    parser.add_argument("--saida", type=Path, default=Path("data/search-benchmark-candidato-inpi.v1.json"))
    args = parser.parse_args()
    asyncio.run(executar(args))


if __name__ == "__main__":
    main()
