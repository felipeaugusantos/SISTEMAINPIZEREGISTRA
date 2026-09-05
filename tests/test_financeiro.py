from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.financeiro import (
    STATUS_CLIENTE,
    _cancelar_comissao_da_parcela,
    _empresa_cliente,
    _gerar_comissao_se_aplicavel,
    _mes_seguinte,
    _parcelar,
    _validar_parcelamento,
)
from app.auth import hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import (
    ComissaoFinanceira,
    FormaPagamentoFinanceira,
    LancamentoFinanceiro,
    Lead,
    ParcelaFinanceira,
    Processo,
    StatusLead,
    TipoProcesso,
    UsuarioOperacoes,
)
from app.permissions import PERMISSOES_FINANCEIRO, destino_inicial, permissoes_do_perfil
from app.plano_contas import CONTAS_PADRAO
from tests.conftest import FakeResult, FakeSession, auth_override, sessao_override, usuario_teste


def test_parcelamento_preserva_total_e_corre_datas() -> None:
    parcelas = _parcelar(Decimal("100.00"), 3)
    assert parcelas == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(parcelas) == Decimal("100.00")
    assert _mes_seguinte(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _mes_seguinte(date(2026, 1, 31), 2) == date(2026, 3, 31)


def test_pagina_financeira_e_protegida_por_permissao() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="operador", permissoes={"finance.view"})
    )
    try:
        with TestClient(app) as client:
            response = client.get("/admin/financeiro")
        assert response.status_code == 200
        assert "Controle contas a pagar e receber" in response.text
        assert "Novo lançamento" in response.text
        assert "admin-financeiro.css?v=9" in response.text
        assert "admin-financeiro.js?v=9" in response.text
    finally:
        app.dependency_overrides.pop(obter_usuario_atual, None)


def test_submenus_de_contas_financeiras_usam_a_mesma_protecao() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="financeiro", permissoes={"finance.view"})
    )
    try:
        with TestClient(app) as client:
            pagar = client.get("/admin/financeiro/contas-a-pagar")
            receber = client.get("/admin/financeiro/contas-a-receber")
        assert pagar.status_code == 200
        assert receber.status_code == 200
        assert "Contas a pagar" in pagar.text
        assert "Contas a receber" in receber.text
    finally:
        app.dependency_overrides.pop(obter_usuario_atual, None)


def test_tela_de_formas_de_pagamento_usa_permissao_financeira() -> None:
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="financeiro", permissoes={"finance.view"})
    )
    try:
        with TestClient(app) as client:
            response = client.get("/admin/financeiro/formas-pagamento")
        assert response.status_code == 200
        assert "Formas de pagamento" in response.text
        assert "Máximo de parcelas" in response.text
        assert "admin-financeiro-formas.js?v=1" in response.text
    finally:
        app.dependency_overrides.pop(obter_usuario_atual, None)


# --- Achado 04/09/2026: a FASE7 implementou plano de contas/DRE, lucratividade,
# comissoes, conciliacao bancaria e NFS-e so como API -- sem nenhuma tela no
# admin, o usuario nao tinha como acessar nada disso pelo sistema. ---


def test_telas_da_fase7_existem_e_sao_protegidas_por_permissao_financeira() -> None:
    paginas = [
        ("/admin/financeiro/plano-contas", "Plano de contas"),
        ("/admin/financeiro/lucratividade", "Lucratividade"),
        ("/admin/financeiro/comissoes", "Comissões"),
        ("/admin/financeiro/conciliacao", "Conciliação bancária"),
        ("/admin/financeiro/nfse", "NFS-e"),
    ]
    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="financeiro", permissoes={"finance.view"})
    )
    try:
        with TestClient(app) as client:
            for rota, titulo in paginas:
                resposta = client.get(rota)
                assert resposta.status_code == 200, rota
                assert titulo in resposta.text, rota
    finally:
        app.dependency_overrides.pop(obter_usuario_atual, None)

    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="operador", permissoes=set())
    )
    try:
        with TestClient(app) as client:
            for rota, _titulo in paginas:
                resposta = client.get(rota, follow_redirects=False)
                assert resposta.status_code == 403, rota
    finally:
        app.dependency_overrides.pop(obter_usuario_atual, None)


def test_cadastro_de_empresa_financeira_fica_isolado_no_tenant() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    usuario = usuario_teste("administrador", {"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/empresas",
            json={"nome": "Empresa Financeira Teste", "documento": "12345678000199"},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
        assert resposta.status_code == 201
        assert resposta.json()["nome"] == "Empresa Financeira Teste"
    finally:
        app.dependency_overrides.clear()


def test_log_financeiro_e_exclusivo_dos_perfis_autorizados() -> None:
    for perfil in ("administrador", "tech", "ceo", "financeiro"):
        app.dependency_overrides[obter_usuario_atual] = auth_override(
            usuario_teste(perfil=perfil, permissoes={"finance.view"})
        )
        try:
            response = TestClient(app).get("/admin/producao/log-financeiro")
            assert response.status_code == 200
            assert "Log Financeiro" in response.text
            assert 'class="finance-log-table"' in response.text
        finally:
            app.dependency_overrides.clear()

    app.dependency_overrides[obter_usuario_atual] = auth_override(
        usuario_teste(perfil="operador", permissoes={"finance.view"})
    )
    try:
        response = TestClient(app).get("/admin/producao/log-financeiro", follow_redirects=False)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_forma_de_pagamento_limita_parcelamento() -> None:
    import pytest
    from fastapi import HTTPException

    cartao = FormaPagamentoFinanceira(
        nome="Cartão 10x",
        tipo="cartao_credito",
        permite_parcelamento=True,
        maximo_parcelas=10,
    )
    _validar_parcelamento(cartao, 10)
    with pytest.raises(HTTPException) as erro:
        _validar_parcelamento(cartao, 11)
    assert erro.value.status_code == 422
    assert "no máximo 10" in erro.value.detail


def test_perfil_comercial_recebe_operacao_financeira_sem_aprovacao() -> None:
    from app.permissions import permissoes_do_perfil

    permissoes = permissoes_do_perfil("comercial")
    assert {"finance.view", "finance.manage", "finance.export"} <= permissoes
    assert "finance.approve" not in permissoes


def test_perfil_financeiro_tem_somente_o_modulo_financeiro() -> None:
    permissoes = permissoes_do_perfil("financeiro")
    assert permissoes == PERMISSOES_FINANCEIRO
    assert all(chave.startswith("finance.") for chave in permissoes)
    assert destino_inicial("financeiro", permissoes) == "/admin/financeiro"


def test_perfil_financeiro_nao_aceita_permissao_de_outro_modulo() -> None:
    import pytest
    from fastapi import HTTPException

    from app.api.usuarios import _validar

    with pytest.raises(HTTPException) as erro:
        _validar("financeiro", ["finance.view", "leads.view"])
    assert erro.value.status_code == 422


def test_cadastro_exibe_perfil_financeiro_com_matriz_restrita() -> None:
    from pathlib import Path

    script = Path("app/web/static/admin-usuarios.js").read_text(encoding="utf-8")
    assert 'financeiro=perfil==="financeiro"' in script
    assert 'p.chave.startsWith("finance.")' in script
    assert "Perfil restrito: acesso exclusivo" in script
    assert '["ceo","tech"]' in script
    assert "Somente o perfil Tech visualiza Execuções recentes" in script


def test_status_de_lead_que_formam_cliente_financeiro() -> None:
    assert STATUS_CLIENTE == {
        StatusLead.PROPOSTA_ENVIADA,
        StatusLead.CONVERTIDO,
    }
    assert StatusLead.EM_CONTATO not in STATUS_CLIENTE


def test_processo_monitorado_tambem_compõe_criterio_de_cliente() -> None:
    expressao = str(_empresa_cliente(usuario_teste()))
    assert "leads" in expressao
    assert "processos_monitorados" in expressao
    assert " OR " in expressao


def test_interface_financeira_padroniza_tipografia_e_estados() -> None:
    from pathlib import Path

    script = Path("app/web/static/admin-financeiro.js").read_text(encoding="utf-8")
    estilos = Path("app/web/static/admin-financeiro.css").read_text(encoding="utf-8")
    assert 'class="finance-summary"' in script
    assert 'cancelada:"Cancelada"' in script
    assert 'item.status==="cancelado"?"cancelada":p.status' in script
    assert ".finance-summary" in estilos
    assert ".installment-value" in estilos
    assert 'financeMode=location.pathname.endsWith("/contas-a-pagar")' in script
    assert "renderExecutive(data)" in script
    assert "Saldo projetado" in script
    assert "Agenda de vencimentos" in script
    assert 'method:editing?"PUT":"POST"' in script
    assert 'data-edit="${item.id}"' in script
    assert 'data-finance-tab="pagar"' in Path("app/web/admin-financeiro.html").read_text(encoding="utf-8")
    pagina = Path("app/web/admin-financeiro.html").read_text(encoding="utf-8")
    assert 'id="finance-executive"' in pagina
    assert 'id="finance-clear"' in pagina
    assert 'id="finance-back"' in pagina
    assert "← Voltar ao painel" in pagina
    assert 'document.querySelector("#finance-back").hidden=financeMode==="overview"' in script
    assert ".finance-executive" in estilos
    assert ".projected-balance" in estilos
    assert ".finance-list-heading" in estilos
    assert ".cancel-entry:hover" in estilos
    assert "background:#a83e2c" in estilos
    assert 'id="history-dialog"' in pagina
    assert 'data-history="${item.id}"' in script
    assert "/historico`" in script
    assert 'name="forma_pagamento_id"' in pagina
    assert ".finance-history-item" in estilos
    formas = Path("app/web/static/admin-financeiro-formas.js").read_text(encoding="utf-8")
    assert "/v1/admin/financeiro/formas-pagamento" in formas
    assert "permite_parcelamento" in formas
    shell = Path("app/web/static/admin-shell.js").read_text(encoding="utf-8")
    assert 'label: "Formas de pagamento"' in shell
    assert 'label: "Log Financeiro"' in shell
    assert 'profiles: ["administrador", "tech", "ceo", "financeiro"]' in shell
    assert 'data-submenu-toggle="${section.id}"' in shell
    assert 'label: "Contas a pagar"' in shell
    assert 'label: "Contas a receber"' in shell
    assert 'class="${section.parent ? "admin-nav-subitem ' in shell
    assert 'data-submenu-toggle="${section.id}"' in shell
    assert "localStorage.setItem(`zr_admin_submenu_${parent}`" in shell
    assert "financeHasActiveChild" in shell


def _lancamento_teste(status_parcela: str = "aberta") -> LancamentoFinanceiro:
    lancamento = LancamentoFinanceiro(
        id=10,
        organizacao_id=1,
        tipo="pagar",
        descricao="Fornecedor de teste",
        competencia=date(2026, 8, 1),
        valor_total=Decimal("100.00"),
        status="parcial" if status_parcela == "paga" else "aberto",
        criado_por="admin",
    )
    lancamento.parcelas = [
        ParcelaFinanceira(
            id=20,
            organizacao_id=1,
            lancamento_id=10,
            numero=1,
            vencimento=date(2026, 8, 20),
            valor=Decimal("100.00"),
            valor_pago=Decimal("100.00") if status_parcela == "paga" else Decimal(0),
            status=status_parcela,
        )
    ]
    return lancamento


def test_edicao_de_conta_financeira_e_auditada() -> None:
    lancamento = _lancamento_teste()
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lancamento))
    usuario = usuario_teste(perfil="financeiro", permissoes={"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    payload = {
        "descricao": "Fornecedor atualizado",
        "documento": "NF-10",
        "competencia": "2026-08-01",
        "valor_total": 125,
        "primeiro_vencimento": "2026-08-20",
        "quantidade_parcelas": 1,
        "empresa_id": None,
        "categoria_id": None,
        "observacoes": "Conferido",
    }
    try:
        resposta = TestClient(app).put(
            "/v1/admin/financeiro/lancamentos/10",
            json=payload,
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "atualizado"
    assert lancamento.descricao == "Fornecedor atualizado"
    assert lancamento.valor_total == Decimal("125")
    assert lancamento.parcelas[0].valor == Decimal("125")


def test_edicao_nao_reparcela_conta_que_ja_tem_baixa() -> None:
    lancamento = _lancamento_teste("paga")
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=lancamento))
    usuario = usuario_teste(perfil="financeiro", permissoes={"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    payload = {
        "descricao": "Fornecedor de teste",
        "competencia": "2026-08-01",
        "valor_total": 200,
        "primeiro_vencimento": "2026-08-20",
        "quantidade_parcelas": 1,
    }
    try:
        resposta = TestClient(app).put(
            "/v1/admin/financeiro/lancamentos/10",
            json=payload,
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 409
    assert "estorne primeiro" in resposta.json()["detail"]


# --- Achado FASE7-13/14 da auditoria (04/09/2026): plano de contas
# gerencial e DRE. ---


def test_criar_conta_plano_com_sucesso() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    usuario = usuario_teste("administrador", {"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/plano-contas",
            json={"codigo": "3.1", "nome": "Honorários", "natureza": "receita", "grupo_dre": "receita_bruta"},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 201
    assert resposta.json()["codigo"] == "3.1"


def test_criar_conta_plano_com_codigo_duplicado_retorna_409() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=1))
    usuario = usuario_teste("administrador", {"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/plano-contas",
            json={"codigo": "3.1", "nome": "Honorários", "natureza": "receita", "grupo_dre": "receita_bruta"},
            headers={"X-CSRF-Token": "csrf-teste"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 409


def test_seed_plano_contas_padrao_cria_todas_quando_vazio() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[]))
    usuario = usuario_teste("administrador", {"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/plano-contas/seed-padrao", headers={"X-CSRF-Token": "csrf-teste"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 201
    assert resposta.json()["criadas"] == len(CONTAS_PADRAO)


def test_seed_plano_contas_padrao_pula_codigos_ja_existentes() -> None:
    codigo_ja_existente = CONTAS_PADRAO[0][0]
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[codigo_ja_existente]))
    usuario = usuario_teste("administrador", {"finance.manage"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/plano-contas/seed-padrao", headers={"X-CSRF-Token": "csrf-teste"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 201
    assert resposta.json()["criadas"] == len(CONTAS_PADRAO) - 1


def test_dre_agrega_por_grupo_e_calcula_resultado() -> None:
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(itens=[("receita_bruta", "receber", Decimal("1000")), ("custos_diretos", "pagar", Decimal("200"))]),
        FakeResult(scalar=Decimal("0")),
    )
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get(
            "/v1/admin/financeiro/dre?competencia_de=2026-01-01&competencia_ate=2026-01-31"
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["receita_bruta"] == "1000"
    assert corpo["lucro_bruto"] == "800"
    assert corpo["resultado_liquido"] == "800"


def test_dre_intervalo_invertido_retorna_422() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get(
            "/v1/admin/financeiro/dre?competencia_de=2026-02-01&competencia_ate=2026-01-01"
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 422


# --- Achado FASE7-8/9 da auditoria (04/09/2026): custo por processo e
# lucratividade por cliente/carteira. ---


def _processo(**kwargs: object) -> Processo:
    base: dict = {
        "id": 1,
        "numero": "923456789",
        "numero_normalizado": "923456789",
        "tipo": TipoProcesso.MARCA,
        "titulo": "Marca Teste",
    }
    base.update(kwargs)
    return Processo(**base)


def test_custo_processo_inexistente_retorna_404() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/v1/admin/financeiro/custo-processo/999")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 404


def test_custo_processo_agrega_receita_e_custos() -> None:
    processo = _processo()
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(scalar=processo),
        FakeResult(scalar=Decimal("5000")),  # receita (lancamentos "receber")
        FakeResult(scalar=Decimal("1000")),  # custo_lancamentos ("pagar")
        FakeResult(scalar=Decimal("500")),  # custo_juridico
    )
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/v1/admin/financeiro/custo-processo/1")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["receita"] == "5000"
    assert corpo["custo_total"] == "1500"
    assert corpo["margem"] == "3500"
    assert corpo["margem_pct"] == 0.7


def test_lucratividade_clientes_agrega_por_empresa_e_ordena_por_margem() -> None:
    app.dependency_overrides[get_session] = sessao_override(
        FakeResult(
            itens=[
                (1, "Cliente A", "receber", Decimal("10000")),
                (1, "Cliente A", "pagar", Decimal("8000")),
                (2, "Cliente B", "receber", Decimal("5000")),
                (2, "Cliente B", "pagar", Decimal("1000")),
            ]
        )
    )
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get(
            "/v1/admin/financeiro/lucratividade/clientes?competencia_de=2026-01-01&competencia_ate=2026-01-31"
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    corpo = resposta.json()
    # Cliente B (margem 4000) deve vir antes de Cliente A (margem 2000).
    assert [c["nome"] for c in corpo["clientes"]] == ["Cliente B", "Cliente A"]
    assert corpo["carteira"]["receita"] == "15000"
    assert corpo["carteira"]["custo"] == "9000"
    assert corpo["carteira"]["margem"] == "6000"


def test_lucratividade_clientes_intervalo_invertido_retorna_422() -> None:
    app.dependency_overrides[get_session] = sessao_override()
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get(
            "/v1/admin/financeiro/lucratividade/clientes?competencia_de=2026-02-01&competencia_ate=2026-01-01"
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 422


# --- Achado FASE7-7 da auditoria (04/09/2026): comissão de operador sobre
# receita, gerada na baixa e cancelada no estorno. Testadas diretamente
# (sem TestClient) por serem funções auxiliares chamadas de dentro de
# baixar()/estornar(), não endpoints próprios. ---


async def test_gerar_comissao_se_aplicavel_cria_quando_responsavel_comissionado() -> None:
    lancamento = LancamentoFinanceiro(id=1, organizacao_id=1, tipo="receber", lead_id=5)
    parcela = ParcelaFinanceira(id=10, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("1000"))
    parcela.lancamento = lancamento
    lead = Lead(id=5, organizacao_id=1, responsavel_id=7)
    operador = UsuarioOperacoes(id=7, organizacao_id=1, percentual_comissao=Decimal("10.00"))
    session = FakeSession(objetos_get=[lead, operador])

    await _gerar_comissao_se_aplicavel(session, parcela)

    comissoes = [obj for obj in session.adicionados if isinstance(obj, ComissaoFinanceira)]
    assert len(comissoes) == 1
    assert comissoes[0].valor_comissao == Decimal("100.00")
    assert comissoes[0].usuario_id == 7
    assert comissoes[0].parcela_id == 10


async def test_gerar_comissao_se_aplicavel_nao_cria_para_lancamento_a_pagar() -> None:
    lancamento = LancamentoFinanceiro(id=1, organizacao_id=1, tipo="pagar", lead_id=5)
    parcela = ParcelaFinanceira(id=10, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("1000"))
    parcela.lancamento = lancamento
    session = FakeSession()

    await _gerar_comissao_se_aplicavel(session, parcela)

    assert session.adicionados == []


async def test_gerar_comissao_se_aplicavel_nao_cria_sem_lead_vinculado() -> None:
    lancamento = LancamentoFinanceiro(id=1, organizacao_id=1, tipo="receber", lead_id=None)
    parcela = ParcelaFinanceira(id=10, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("1000"))
    parcela.lancamento = lancamento
    session = FakeSession()

    await _gerar_comissao_se_aplicavel(session, parcela)

    assert session.adicionados == []


async def test_gerar_comissao_se_aplicavel_nao_cria_sem_percentual_configurado() -> None:
    lancamento = LancamentoFinanceiro(id=1, organizacao_id=1, tipo="receber", lead_id=5)
    parcela = ParcelaFinanceira(id=10, organizacao_id=1, lancamento_id=1, numero=1, valor_pago=Decimal("1000"))
    parcela.lancamento = lancamento
    lead = Lead(id=5, organizacao_id=1, responsavel_id=7)
    operador = UsuarioOperacoes(id=7, organizacao_id=1, percentual_comissao=None)
    session = FakeSession(objetos_get=[lead, operador])

    await _gerar_comissao_se_aplicavel(session, parcela)

    assert session.adicionados == []


async def test_cancelar_comissao_da_parcela_marca_como_cancelada() -> None:
    comissao = ComissaoFinanceira(
        id=1, organizacao_id=1, usuario_id=7, lancamento_id=1, parcela_id=10,
        valor_base=Decimal("1000"), percentual=Decimal("10"), valor_comissao=Decimal("100"), status="pendente",
    )
    session = FakeSession([FakeResult(scalar=comissao)])

    await _cancelar_comissao_da_parcela(session, 10)

    assert comissao.status == "cancelada"


async def test_cancelar_comissao_da_parcela_sem_comissao_nao_faz_nada() -> None:
    session = FakeSession([FakeResult(scalar=None)])

    await _cancelar_comissao_da_parcela(session, 10)  # não deve levantar exceção


def test_listar_comissoes_filtra_por_usuario_e_status() -> None:
    comissao = ComissaoFinanceira(
        id=1, organizacao_id=1, usuario_id=7, lancamento_id=1, parcela_id=10,
        valor_base=Decimal("1000"), percentual=Decimal("10"), valor_comissao=Decimal("100"),
        status="pendente", pago_em=None, criado_em=date(2026, 1, 1),
    )
    app.dependency_overrides[get_session] = sessao_override(FakeResult(itens=[comissao]))
    usuario = usuario_teste("administrador", {"finance.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/v1/admin/financeiro/comissoes?usuario_id=7&status=pendente")
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo["itens"]) == 1
    assert corpo["itens"][0]["valor_comissao"] == "100"


def test_pagar_comissao_inexistente_retorna_404() -> None:
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=None))
    usuario = usuario_teste("administrador", {"finance.approve"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/comissoes/999/pagar", headers={"X-CSRF-Token": "csrf-teste"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 404


def test_pagar_comissao_ja_paga_retorna_409() -> None:
    comissao = ComissaoFinanceira(
        id=1, organizacao_id=1, usuario_id=7, lancamento_id=1, parcela_id=10,
        valor_base=Decimal("1000"), percentual=Decimal("10"), valor_comissao=Decimal("100"), status="paga",
    )
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=comissao))
    usuario = usuario_teste("administrador", {"finance.approve"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/comissoes/1/pagar", headers={"X-CSRF-Token": "csrf-teste"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 409


def test_pagar_comissao_pendente_com_sucesso() -> None:
    comissao = ComissaoFinanceira(
        id=1, organizacao_id=1, usuario_id=7, lancamento_id=1, parcela_id=10,
        valor_base=Decimal("1000"), percentual=Decimal("10"), valor_comissao=Decimal("100"), status="pendente",
    )
    app.dependency_overrides[get_session] = sessao_override(FakeResult(scalar=comissao))
    usuario = usuario_teste("administrador", {"finance.approve"})
    object.__setattr__(usuario, "csrf_hash", hash_token("csrf-teste"))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).post(
            "/v1/admin/financeiro/comissoes/1/pagar", headers={"X-CSRF-Token": "csrf-teste"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 200
    assert comissao.status == "paga"
    assert comissao.pago_em == date.today()
