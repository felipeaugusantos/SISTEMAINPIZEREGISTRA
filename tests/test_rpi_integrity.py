from datetime import UTC, datetime, timedelta

from app.models import TipoProcesso
from app.rpi.bulk_importer import _chave_movimentacao
from app.rpi.health import avaliar_saude_rpi
from app.rpi.integrity import (
    avaliar_importacao,
    calcular_integridade_arquivo,
    sanitizar_referencias_xml,
    validar_arquivo_rpi,
)
from app.rpi.types import MovimentacaoRpi, RegistroRpi


def _avaliar(**mudancas):
    dados = {
        "numero_rpi": 2901,
        "registros": 40_000,
        "titulares": 39_000,
        "classes": 70_000,
        "movimentacoes": 40_100,
        "arquivo_tamanho_bytes": 2_000_000,
        "arquivo_sha256": "a" * 64,
    }
    dados.update(mudancas)
    return avaliar_importacao(**dados)


def test_checksum_e_tamanho_do_arquivo_sao_reproduziveis(tmp_path) -> None:
    arquivo = tmp_path / "rpi.xml"
    arquivo.write_bytes(b"<revista><processo/></revista>")

    primeiro = calcular_integridade_arquivo(arquivo)
    segundo = calcular_integridade_arquivo(arquivo)

    assert primeiro == segundo
    assert primeiro[1] == arquivo.stat().st_size
    assert len(primeiro[0]) == 64


def test_xml_rpi_corrompido_e_rejeitado_antes_da_importacao(tmp_path) -> None:
    arquivo = tmp_path / "RM2901.xml"
    arquivo.write_bytes(b'<revista numero="2901" data="11/08/2026"><processo>')

    try:
        validar_arquivo_rpi(arquivo, TipoProcesso.MARCA)
    except ValueError as erro:
        assert "incompleto" in str(erro)
    else:
        raise AssertionError("XML corrompido deveria ser rejeitado")


def test_xml_rpi_sem_registros_e_rejeitado(tmp_path) -> None:
    arquivo = tmp_path / "RM2901.xml"
    arquivo.write_text('<revista numero="2901" data="11/08/2026"/>', encoding="utf-8")

    try:
        validar_arquivo_rpi(arquivo, TipoProcesso.MARCA)
    except ValueError as erro:
        assert "não contém registros" in str(erro)
    else:
        raise AssertionError("RPI sem registros deveria ser rejeitada")


def test_referencia_numerica_xml_invalida_eh_removida_sem_perder_o_registro(tmp_path) -> None:
    arquivo = tmp_path / "RM2902.xml"
    arquivo.write_text(
        '<revista numero="2902" data="18/08/2026"><processo><marca>ABC &#x13; DEF</marca></processo></revista>',
        encoding="utf-8",
    )
    assert sanitizar_referencias_xml(arquivo) == 1
    assert validar_arquivo_rpi(arquivo, TipoProcesso.MARCA) == 1
    assert "&#x13;" not in arquivo.read_text(encoding="utf-8")


def test_rpi_vazia_ou_parcial_e_erro_de_integridade() -> None:
    status, anomalias = _avaliar(registros=0, titulares=0, classes=0, movimentacoes=0)

    assert status == "erro"
    assert "RPI_SEM_REGISTROS" in {item["codigo"] for item in anomalias}


def test_queda_abrupta_gera_atencao_sem_bloquear_importacao() -> None:
    status, anomalias = _avaliar(
        registros=15_000,
        anterior={"registros_processados": 40_000},
        razao_minima_registros=0.5,
        minimo_referencia_registros=1_000,
    )

    assert status == "atencao"
    assert "QUEDA_ABRUPTA_REGISTROS" in {item["codigo"] for item in anomalias}


def test_edicao_pulada_e_detectada() -> None:
    status, anomalias = _avaliar(numero_rpi=2903, ultima_edicao_importada=2901)

    assert status == "atencao"
    assert "EDICAO_PULADA" in {item["codigo"] for item in anomalias}


def test_mesmo_arquivo_com_mesmo_resultado_e_idempotente() -> None:
    anterior = {
        "arquivo_sha256": "a" * 64,
        "registros_processados": 40_000,
        "titulares_processados": 39_000,
        "classes_processadas": 70_000,
        "movimentacoes_processadas": 40_100,
    }

    status, anomalias = _avaliar(mesma_edicao_anterior=anterior)

    assert status == "ok"
    assert anomalias == []


def test_chave_da_movimentacao_e_deterministica() -> None:
    registro = RegistroRpi(
        numero="940617064",
        tipo=TipoProcesso.MARCA,
        titulo="Marca",
        data_deposito=None,
        situacao=None,
        numero_rpi=2901,
        data_rpi=datetime(2026, 8, 11, tzinfo=UTC).date(),
        fonte_arquivo="RM2901.xml",
        titulares=(),
        movimentacoes=(MovimentacaoRpi(codigo="123", descricao="Despacho"),),
    )

    assert _chave_movimentacao(registro, "123", "Despacho") == _chave_movimentacao(
        registro, "123", "Despacho"
    )


def test_health_rpi_distingue_processamento_atraso_e_erro() -> None:
    agora = datetime(2026, 8, 14, 12, tzinfo=UTC)
    comum = {
        "ultima_rpi_oficial": 2901,
        "ultima_rpi_importada": 2901,
        "ultima_sincronizacao": agora - timedelta(hours=2),
        "status_integridade": "ok",
        "limite_atraso_horas": 12,
        "agora": agora,
    }
    assert avaliar_saude_rpi(status_sync="importando", **comum)[0] == "processando"
    assert (
        avaliar_saude_rpi(
            status_sync="atualizado",
            **{**comum, "ultima_sincronizacao": agora - timedelta(hours=13)},
        )[0]
        == "atrasado"
    )
    assert avaliar_saude_rpi(status_sync="falhou", **comum)[0] == "erro"
