import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.crm_admin import MetaComercialInput, definir_meta, listar_metas
from app.models import MetaComercial, UsuarioOperacoes
from tests.conftest import FakeResult, FakeSession, usuario_teste

# --- Item 50 da auditoria completa do CRM (06/09/2026): metas mensais de
# leads ganhos e valor faturado por operador. Decisão do usuário: ambos os
# critérios ao mesmo tempo. Não existe registro de "meta de equipe" -- é a
# soma das metas individuais do período, calculada na consulta. ---


def test_listar_metas_combina_meta_definida_e_resultado_real() -> None:
    ana = UsuarioOperacoes(id=1, organizacao_id=1, nome="Ana")
    beto = UsuarioOperacoes(id=2, organizacao_id=1, nome="Beto")
    meta_ana = MetaComercial(
        organizacao_id=1,
        operador_id=1,
        periodo="2026-09",
        meta_leads_ganhos=5,
        meta_valor_faturado=Decimal("10000"),
    )
    session = FakeSession(
        [
            FakeResult(itens=[ana, beto]),  # operadores ativos de perfil comercial
            FakeResult(itens=[meta_ana]),  # metas já definidas no período
            FakeResult(itens=[(1, 3)]),  # leads ganhos por operador (só Ana ganhou)
            FakeResult(itens=[(1, Decimal("6000")), (2, Decimal("2000"))]),  # valor faturado por operador
        ]
    )

    resultado = asyncio.run(listar_metas("2026-09", session, usuario_teste()))

    por_operador = {item["operador_id"]: item for item in resultado["operadores"]}
    assert por_operador[1]["meta_leads_ganhos"] == 5
    assert por_operador[1]["leads_ganhos"] == 3
    assert por_operador[1]["meta_valor_faturado"] == Decimal("10000")
    assert por_operador[1]["valor_faturado"] == Decimal("6000")
    # Beto não tem meta definida -- entra com zero, não é omitido da lista.
    assert por_operador[2]["meta_leads_ganhos"] == 0
    assert por_operador[2]["valor_faturado"] == Decimal("2000")
    # Equipe = soma das metas/resultados individuais, sem registro próprio.
    assert resultado["equipe"]["meta_leads_ganhos"] == 5
    assert resultado["equipe"]["leads_ganhos"] == 3
    assert resultado["equipe"]["meta_valor_faturado"] == Decimal("10000")
    assert resultado["equipe"]["valor_faturado"] == Decimal("8000")


def test_definir_meta_cria_registro_novo_quando_nao_existe() -> None:
    operador = UsuarioOperacoes(id=1, organizacao_id=1, nome="Ana")
    session = FakeSession(
        [
            FakeResult(scalar=operador),  # operador existe na organização
            FakeResult(scalar=None),  # ainda não tem meta neste período
        ]
    )
    dados = MetaComercialInput(periodo="2026-09", meta_leads_ganhos=8, meta_valor_faturado=Decimal("15000"))

    resultado = asyncio.run(definir_meta(1, dados, session, usuario_teste()))

    novas_metas = [item for item in session.adicionados if isinstance(item, MetaComercial)]
    assert len(novas_metas) == 1
    assert novas_metas[0].meta_leads_ganhos == 8
    assert resultado["meta_valor_faturado"] == Decimal("15000")


def test_definir_meta_atualiza_registro_existente_sem_duplicar() -> None:
    operador = UsuarioOperacoes(id=1, organizacao_id=1, nome="Ana")
    meta_existente = MetaComercial(
        organizacao_id=1, operador_id=1, periodo="2026-09", meta_leads_ganhos=5, meta_valor_faturado=Decimal("10000")
    )
    session = FakeSession(
        [
            FakeResult(scalar=operador),
            FakeResult(scalar=meta_existente),
        ]
    )
    dados = MetaComercialInput(periodo="2026-09", meta_leads_ganhos=9, meta_valor_faturado=Decimal("20000"))

    resultado = asyncio.run(definir_meta(1, dados, session, usuario_teste()))

    assert session.adicionados == []
    assert meta_existente.meta_leads_ganhos == 9
    assert resultado["meta_leads_ganhos"] == 9


def test_definir_meta_404_quando_operador_nao_existe() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    dados = MetaComercialInput(periodo="2026-09", meta_leads_ganhos=1, meta_valor_faturado=Decimal("0"))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(definir_meta(999, dados, session, usuario_teste()))
    assert excinfo.value.status_code == 404


def test_meta_comercial_input_rejeita_periodo_invalido() -> None:
    with pytest.raises(ValueError):
        MetaComercialInput(periodo="2026-13", meta_leads_ganhos=1, meta_valor_faturado=Decimal("0"))
