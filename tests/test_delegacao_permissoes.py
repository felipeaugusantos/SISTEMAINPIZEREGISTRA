import asyncio

import pytest
from fastapi import HTTPException

from app.api.usuarios import UsuarioInput, UsuarioUpdate, _exigir_delegavel, atualizar, criar
from app.permissions import CHAVES_PERMISSAO, PERFIS_ACESSO_TOTAL, permissoes_efetivas
from tests.conftest import FakeResult, FakeSession, usuario_teste

# Auditoria externa de 13/10/2026, A05: a trava de concessão só olhava o NOME do perfil; um usuário com
# users.manage fora dos perfis de acesso total criava um operador com todas as permissões. Mais a
# correção de contas ceo/tech antigas, que nunca recebiam as permissões criadas depois delas.

MANAGER = {"users.manage", "leads.view", "crm.view"}


def _gestor():
    """Gestor delegado: tem users.manage mas NÃO é de acesso total."""
    return usuario_teste(perfil="supervisor", permissoes=set(MANAGER))


def test_perfis_de_acesso_total() -> None:
    assert PERFIS_ACESSO_TOTAL == {"administrador", "ceo", "tech"}


def test_a05_gestor_delegado_nao_concede_o_que_nao_tem() -> None:
    with pytest.raises(HTTPException) as erro:
        _exigir_delegavel(_gestor(), {"leads.view", "finance.approve", "legal.confirm_critical"})
    assert erro.value.status_code == 403
    assert "finance.approve" in erro.value.detail
    assert "legal.confirm_critical" in erro.value.detail
    assert "leads.view" not in erro.value.detail


def test_a05_gestor_delegado_pode_conceder_subconjunto_do_que_tem() -> None:
    _exigir_delegavel(_gestor(), {"leads.view", "crm.view"})
    _exigir_delegavel(_gestor(), set())


def test_a05_perfis_de_acesso_total_concedem_qualquer_permissao() -> None:
    for perfil in PERFIS_ACESSO_TOTAL:
        _exigir_delegavel(usuario_teste(perfil=perfil), set(CHAVES_PERMISSAO))


def test_a05_criar_operador_com_a_lista_inteira_e_recusado_para_gestor_delegado() -> None:
    dados = UsuarioInput(
        nome="Novo Operador",
        usuario="novo.operador",
        email="novo@teste.local",
        perfil="operador",
        permissoes=sorted(CHAVES_PERMISSAO),
    )
    sessao = FakeSession([])  # a recusa acontece antes de qualquer consulta ou escrita
    with pytest.raises(HTTPException) as erro:
        asyncio.run(criar(dados, sessao, _gestor()))
    assert erro.value.status_code == 403
    assert sessao.executados == []
    assert sessao.adicionados == []


def test_a05_atualizar_para_operador_com_a_lista_inteira_e_recusado_para_gestor_delegado() -> None:
    class Alvo:
        id = 99
        perfil = "operador"
        ativo = True
        permissoes: list = []

    sessao = FakeSession([FakeResult(scalar=Alvo())])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(
            atualizar(99, UsuarioUpdate(perfil="operador", permissoes=sorted(CHAVES_PERMISSAO)), sessao, _gestor())
        )
    assert erro.value.status_code == 403
    assert sessao.commits == 0


def test_ceo_e_tech_recebem_todas_as_permissoes_inclusive_as_criadas_depois() -> None:
    antigas = {"leads.view", "crm.view"}  # conta criada antes das permissões novas existirem
    for perfil in ("ceo", "tech"):
        assert permissoes_efetivas(perfil, antigas) == CHAVES_PERMISSAO


def test_outros_perfis_mantem_apenas_as_permissoes_concedidas() -> None:
    concedidas = {"leads.view", "crm.view"}
    assert permissoes_efetivas("comercial", concedidas) == frozenset(concedidas)
    assert permissoes_efetivas("operador", []) == frozenset()
    assert permissoes_efetivas("financeiro", {"finance.view"}) == frozenset({"finance.view"})
