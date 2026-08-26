from app.cli.sincronizar_alto_renome import (
    extrair_lista_texto,
    localizar_pdf_oficial,
)
from app.models import AfinidadeClasse
from app.trademarks.affinity import avaliar_afinidade
from app.trademarks.nice import mapear_atividade
from app.trademarks.status import normalizar_despacho


def test_normaliza_despachos_sem_ocultar_texto_oficial() -> None:
    assert normalizar_despacho("IPAS029", "Deferimento do pedido").codigo == "deferida"
    assert normalizar_despacho("IPAS136", "Exigência de mérito").codigo == "exigencia"
    assert normalizar_despacho(None, "Indeferimento do pedido").relevancia == "inativa"
    assert normalizar_despacho(None, "Texto ainda não mapeado").codigo == "nao_classificada"


def test_nao_confunde_decisao_de_peticao_com_decisao_do_pedido() -> None:
    assert normalizar_despacho(None, "Deferimento da petição").codigo == "peticao_decidida"
    assert normalizar_despacho(None, "Indeferimento da petição").codigo == "peticao_decidida"


def test_classifica_resultados_terminais_e_parciais_do_inpi() -> None:
    assert normalizar_despacho(None, "Concessão de registro").codigo == "registrada"
    assert normalizar_despacho(None, "Deferimento parcial do pedido").codigo == "deferida_parcial"
    assert (
        normalizar_despacho(None, "Decisão de considerar pedido inexistente por falta de pagamento").codigo
        == "inexistente"
    )


def test_mapeia_atividade_leiga_para_classes_candidatas() -> None:
    classes = mapear_atividade("Venda de roupas e acessórios pela internet")
    codigos = {classe.codigo for classe in classes}

    assert {"25", "35"} <= codigos


def test_afinidade_inicial_permanece_pendente_ate_revisao() -> None:
    regra = AfinidadeClasse(
        classe_origem="25",
        classe_destino="35",
        nivel="alta",
        justificativa="Vestuário e varejo compartilham público.",
        versao="inicial-2026",
        status_revisao="pendente",
    )

    resultado = avaliar_afinidade(["25"], ["35"], [regra])

    assert resultado.nivel == "alta"
    assert resultado.revisao == "pendente"


def test_afinidade_aprovada_registra_revisao_humana() -> None:
    regra = AfinidadeClasse(
        classe_origem="09",
        classe_destino="42",
        nivel="alta",
        justificativa="Software e desenvolvimento tecnológico.",
        versao="inicial-2026",
        status_revisao="aprovada",
    )

    assert avaliar_afinidade(["09"], ["42"], [regra]).revisao == "aprovada"


def test_localiza_lista_portuguesa_na_pagina_oficial() -> None:
    pagina = """
    <a href="/inpi/alto-renome/lista-marcas-alto-renome-em-vigencia.pdf">
      Marcas com alto renome reconhecido em português
    </a>
    """
    url = localizar_pdf_oficial("https://www.gov.br/inpi/alto-renome/", pagina)
    assert url == ("https://www.gov.br/inpi/alto-renome/lista-marcas-alto-renome-em-vigencia.pdf")


def test_extrai_processos_e_data_da_lista_oficial() -> None:
    numeros = " ".join(f"{numero:09d}" for numero in range(1, 21))
    lista = extrair_lista_texto(
        "Data da última atualização: 16/06/2026\n"
        "NIKE Nominativa Nike International LTD 000000001 2412 28/03/2017\n"
        f"{numeros}"
    )

    assert len(lista.processos) == 20
    assert lista.atualizada_em is not None
    assert lista.atualizada_em.date().isoformat() == "2026-06-16"
    assert ("000000001", "NIKE") in lista.marcas
