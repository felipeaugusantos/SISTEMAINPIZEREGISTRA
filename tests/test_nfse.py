from datetime import date
from decimal import Decimal

import pytest

from app.nfse import AdaptadorNFSeIndisponivelError, AdaptadorNFSeSandbox, obter_adaptador_nfse

# --- Achado FASE7-6 da auditoria (04/09/2026): interface de adaptador de
# NFS-e, sem acoplar a um provedor/município específico. ---


async def test_emitir_devolve_numero_e_codigo_verificacao() -> None:
    adaptador = AdaptadorNFSeSandbox()
    nota = await adaptador.emitir(
        valor=Decimal("1000"),
        descricao_servico="Registro de marca",
        tomador_documento="12345678000199",
        tomador_nome="Cliente Teste",
        competencia=date(2026, 1, 1),
    )
    assert nota.numero.startswith("SANDBOX-")
    assert nota.codigo_verificacao


async def test_cancelar_nao_levanta_excecao() -> None:
    adaptador = AdaptadorNFSeSandbox()
    await adaptador.cancelar("SANDBOX-ABC123")


def test_obter_adaptador_sandbox() -> None:
    assert obter_adaptador_nfse("sandbox").nome == "sandbox"


def test_obter_adaptador_desconhecido_levanta_erro() -> None:
    with pytest.raises(AdaptadorNFSeIndisponivelError):
        obter_adaptador_nfse("focus-nfe")
