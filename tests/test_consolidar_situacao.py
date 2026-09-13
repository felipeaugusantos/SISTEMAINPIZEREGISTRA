import asyncio

from app.cli.consolidar_situacoes_marcas import _comando, consolidar_situacao


def test_comando_individual_filtra_por_processo() -> None:
    sql = str(_comando("AND p.id = :processo_id"))
    assert "AND p.id = :processo_id" in sql


def test_comando_lote_filtra_por_lista_de_processos() -> None:
    sql = str(_comando("AND p.id = ANY(:processo_ids)"))
    assert "AND p.id = ANY(:processo_ids)" in sql


def test_comando_global_sem_filtro_de_processo() -> None:
    sql = str(_comando(""))
    assert ":processo_id" not in sql
    assert ":processo_ids" not in sql
    # mantém o escopo de marca em ambos os modos
    assert "p.tipo = 'marca'" in sql


class _SessionFalsa:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.chamadas: list[tuple[object, dict]] = []

    async def execute(self, comando: object, parametros: dict) -> "_SessionFalsa":
        self.chamadas.append((comando, parametros))
        return self


def test_consolidar_situacao_prioriza_processo_individual_sobre_lote() -> None:
    session = _SessionFalsa(rowcount=3)

    resultado = asyncio.run(consolidar_situacao(session, processo_id=42, processo_ids=[1, 2, 3]))

    assert resultado == 3
    _comando_usado, parametros = session.chamadas[0]
    assert parametros == {"processo_id": 42}


def test_consolidar_situacao_usa_lote_quando_sem_processo_individual() -> None:
    session = _SessionFalsa(rowcount=2)

    resultado = asyncio.run(consolidar_situacao(session, processo_ids=[10, 11]))

    assert resultado == 2
    _comando_usado, parametros = session.chamadas[0]
    assert parametros == {"processo_ids": [10, 11]}


def test_consolidar_situacao_sem_argumentos_atualiza_toda_a_base() -> None:
    session = _SessionFalsa(rowcount=0)

    asyncio.run(consolidar_situacao(session))

    _comando_usado, parametros = session.chamadas[0]
    assert parametros == {}
