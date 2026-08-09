from app.api.admin import obter_resumo
from tests.conftest import FakeResult, FakeSession


async def test_resumo_centraliza_metricas_sem_carregar_listas() -> None:
    session = FakeSession(
        [
            FakeResult(
                itens=[
                    (
                        120,
                        9,
                        103,
                        2897,
                        201,
                        19,
                        64,
                        7,
                        3,
                    )
                ]
            )
        ]
    )

    resumo = await obter_resumo(session, "admin")

    assert resumo.leads_total == 120
    assert resumo.leads_novos == 9
    assert resumo.pesquisas_total == 103
    assert resumo.ultima_rpi == 2897
    assert resumo.alto_renome_vigentes == 201
    assert resumo.afinidades_pendentes == 19
    assert resumo.riscos_elevados == 7
    assert resumo.riscos_pendentes_revisao == 3
