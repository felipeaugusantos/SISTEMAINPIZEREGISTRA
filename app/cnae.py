"""Descrição oficial de cada código CNAE (Classificação Nacional de
Atividades Econômicas), pra exibição -- não é usada em nenhuma regra de
negócio nem gravada no banco.

Achado do usuário (20/09/2026): a tela de Prospecção mostra o código do
CNAE (ex.: "4711302"), mas a equipe de atendimento não sabe de cabeça o
que cada número significa. `app/referencias/cnae.json` é a tabela oficial
de subclasses do CNAE 2.0 (extraída de https://github.com/thefintz/cnae,
que por sua vez publica os dados abertos do IBGE/Receita Federal),
reduzida a só código + descrição (a fonte original inclui hierarquia e
notas completas, sem uso aqui).

Cuidado: NÃO renomear "referencias" para "data" -- o .dockerignore tem uma
regra `data` (sem barra) que exclui qualquer pasta com esse nome em
qualquer profundidade, o que apagaria este arquivo silenciosamente do
build de produção (achado nesta mesma tarefa, 20/09/2026).
"""

import json
from functools import lru_cache
from pathlib import Path

_ARQUIVO_CNAE = Path(__file__).parent / "referencias" / "cnae.json"


@lru_cache(maxsize=1)
def _tabela_cnae() -> dict[str, str]:
    try:
        bruto = json.loads(_ARQUIVO_CNAE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {item["codigo"]: item["descricao"] for item in bruto}


def descricao_cnae(codigo: str | None) -> str | None:
    """Devolve a descrição oficial do código informado, ou None se o
    código não for reconhecido (formato inesperado, código descontinuado
    etc.) -- nunca levanta exceção, é só um texto auxiliar de exibição."""
    if not codigo:
        return None
    normalizado = "".join(c for c in codigo if c.isdigit())
    return _tabela_cnae().get(normalizado)
