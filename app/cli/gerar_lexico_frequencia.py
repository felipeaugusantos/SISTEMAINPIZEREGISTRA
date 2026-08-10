"""Gera o léxico de frequência de tokens do acervo de marcas.

Conta em quantas marcas cada token aparece (frequência de documento) usando a mesma
normalização da inferência (app.search.normalizar_texto), garantindo simetria
treino/serviço. O resultado alimenta a feature de distintividade `marca_frequencia_max_norm`.

Uso:
    python -m app.cli.gerar_lexico_frequencia --minimo 200
"""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.database import session_factory
from app.models import Processo, TipoProcesso
from app.search import normalizar_texto
from app.trademarks.learning import _CAMINHO_LEXICO

LOTE = 50_000


async def executar(minimo: int, maximo_tokens: int) -> None:
    contador: Counter[str] = Counter()
    total_marcas = 0
    async with session_factory() as session:
        ultimo_id = 0
        while True:
            linhas = (
                await session.execute(
                    select(Processo.id, Processo.titulo)
                    .where(
                        Processo.tipo == TipoProcesso.MARCA,
                        Processo.titulo.is_not(None),
                        Processo.id > ultimo_id,
                    )
                    .order_by(Processo.id)
                    .limit(LOTE)
                )
            ).all()
            if not linhas:
                break
            for _id, titulo in linhas:
                total_marcas += 1
                for token in set(normalizar_texto(titulo).split()):
                    if len(token) >= 2:
                        contador[token] += 1
            ultimo_id = linhas[-1][0]

    frequencias = {
        token: contagem
        for token, contagem in contador.most_common(maximo_tokens)
        if contagem >= minimo
    }
    caminho = Path(_CAMINHO_LEXICO)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps(frequencias, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    print(
        f"Lexico gerado: {len(frequencias)} tokens (>= {minimo} marcas) "
        f"de {total_marcas:,} marcas -> {caminho}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera o léxico de frequência de tokens.")
    parser.add_argument("--minimo", type=int, default=200, help="Frequência mínima do token.")
    parser.add_argument("--maximo-tokens", type=int, default=20_000)
    args = parser.parse_args()
    asyncio.run(executar(args.minimo, args.maximo_tokens))


if __name__ == "__main__":
    main()
