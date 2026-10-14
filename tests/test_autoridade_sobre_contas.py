import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException

from app.api.usuarios import (
    UsuarioUpdate,
    _exigir_autoridade,
    atualizar,
    desbloquear,
    redefinir_senha,
    revogar_sessoes,
)
from app.models import EventoAuditoria, PermissaoOperacoes, UsuarioOperacoes
from tests.conftest import FakeResult, FakeSession, usuario_teste

# Auditoria externa de 14/10/2026, S01: editar, redefinir senha, revogar sessões e desbloquear
# precisam de autoridade sobre o ALVO (superadmin protegido, permissões efetivas comparadas).

GESTOR = {"users.manage", "users.reset_password", "users.revoke_sessions", "leads.view", "crm.view"}


def _gestor():
    return usuario_teste(perfil="supervisor", permissoes=set(GESTOR))


def _alvo(perfil="operador", permissoes=("leads.view",), *, superadmin=False, id=5):
    return UsuarioOperacoes(
        id=id,
        organizacao_id=1,
        nome="Alvo",
        usuario=f"alvo{id}",
        email=f"alvo{id}@teste.local",
        perfil=perfil,
        senha_hash="hash",
        ativo=True,
        superadmin=superadmin,
        senha_versao=1,
        permissoes=[PermissaoOperacoes(chave=chave) for chave in permissoes],
    )


def _superadmin():
    return replace(usuario_teste(perfil="administrador"), superadmin=True)


def _negacoes(sessao: FakeSession) -> list[EventoAuditoria]:
    return [e for e in sessao.adicionados if isinstance(e, EventoAuditoria) and not e.sucesso]


def _recusa(ator, alvo, acao="NEGADO_TESTE") -> tuple[HTTPException, FakeSession]:
    sessao = FakeSession([])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(_exigir_autoridade(sessao, ator, alvo, acao))
    return erro.value, sessao


def test_gestor_nao_age_sobre_conta_com_permissao_que_ele_nao_tem() -> None:
    erro, sessao = _recusa(_gestor(), _alvo(permissoes=("leads.view", "finance.approve")))
    assert erro.status_code == 403
    assert sessao.commits == 1  # a negação é gravada antes de recusar
    (evento,) = _negacoes(sessao)
    assert evento.detalhes == {"motivo": "permissoes_superiores"}
    assert evento.recurso == "usuario:5" and evento.actor_id == 1 and evento.status_http == 403


@pytest.mark.parametrize("perfil", ["administrador", "ceo", "tech"])
def test_gestor_nao_age_sobre_perfil_de_acesso_total(perfil) -> None:
    erro, sessao = _recusa(_gestor(), _alvo(perfil=perfil, permissoes=()))
    assert erro.status_code == 403
    assert _negacoes(sessao)[0].detalhes == {"motivo": "perfil_acesso_total"}


def test_administrador_comum_nao_age_sobre_superadmin() -> None:
    erro, sessao = _recusa(usuario_teste(perfil="administrador"), _alvo(superadmin=True, perfil="administrador"))
    assert erro.status_code == 403
    assert _negacoes(sessao)[0].detalhes == {"motivo": "conta_superadmin"}


def test_gestor_age_sobre_conta_com_permissoes_dentro_das_suas() -> None:
    sessao = FakeSession([])
    asyncio.run(_exigir_autoridade(sessao, _gestor(), _alvo(permissoes=("leads.view",)), "NEGADO_TESTE"))
    asyncio.run(_exigir_autoridade(sessao, _gestor(), _alvo(permissoes=()), "NEGADO_TESTE"))
    assert _negacoes(sessao) == [] and sessao.commits == 0


def test_acesso_total_age_sobre_qualquer_conta_que_nao_seja_superadmin() -> None:
    sessao = FakeSession([])
    for perfil in ("administrador", "ceo", "tech"):
        ator = usuario_teste(perfil=perfil)
        asyncio.run(_exigir_autoridade(sessao, ator, _alvo(perfil="operador", permissoes=("finance.approve",)), "N"))
        asyncio.run(_exigir_autoridade(sessao, ator, _alvo(perfil="tech", permissoes=()), "N"))
    assert _negacoes(sessao) == []


def test_superadmin_age_sobre_superadmin() -> None:
    sessao = FakeSession([])
    asyncio.run(_exigir_autoridade(sessao, _superadmin(), _alvo(superadmin=True, perfil="administrador"), "N"))
    assert _negacoes(sessao) == []


def test_agir_sobre_a_propria_conta_nao_passa_pela_trava() -> None:
    sessao = FakeSession([])
    # mesmo com permissões a mais no registro, a própria conta não é "outra pessoa"
    asyncio.run(_exigir_autoridade(sessao, _gestor(), _alvo(id=1, permissoes=("finance.approve",)), "N"))
    assert _negacoes(sessao) == []


# --- os quatro endpoints aplicam a trava antes de qualquer alteração ---


def _superior():
    return _alvo(perfil="administrador", permissoes=())


def test_reset_de_senha_de_conta_superior_e_recusado_sem_trocar_a_senha() -> None:
    alvo = _superior()
    sessao = FakeSession([FakeResult(scalar=alvo)])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(redefinir_senha(5, sessao, _gestor()))
    assert erro.value.status_code == 403
    assert alvo.senha_hash == "hash" and alvo.senha_versao == 1
    assert _negacoes(sessao)[0].acao == "NEGADO_REDEF_SENHA"


def test_edicao_de_conta_superior_e_recusada_mesmo_trocando_so_o_email() -> None:
    alvo = _superior()
    sessao = FakeSession([FakeResult(scalar=alvo)])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(atualizar(5, UsuarioUpdate(email="novo@teste.local"), sessao, _gestor()))
    assert erro.value.status_code == 403
    assert alvo.email == "alvo5@teste.local"
    assert _negacoes(sessao)[0].acao == "NEGADO_ALTERAR"


def test_revogar_sessoes_de_conta_superior_e_recusado() -> None:
    sessao = FakeSession([FakeResult(scalar=_superior())])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(revogar_sessoes(5, sessao, _gestor()))
    assert erro.value.status_code == 403
    assert len(sessao.executados) == 1  # só a consulta do alvo; nenhuma revogação
    assert _negacoes(sessao)[0].acao == "NEGADO_REVOGAR"


def test_desbloquear_conta_superior_e_recusado() -> None:
    alvo = _superior()
    sessao = FakeSession([FakeResult(scalar=alvo)])
    with pytest.raises(HTTPException) as erro:
        asyncio.run(desbloquear(5, sessao, _gestor()))
    assert erro.value.status_code == 403
    assert _negacoes(sessao)[0].acao == "NEGADO_DESBLOQUEAR"


def test_fluxo_autorizado_continua_funcionando() -> None:
    alvo = _alvo(permissoes=("leads.view",))
    sessao = FakeSession([FakeResult(scalar=alvo)])
    resultado = asyncio.run(redefinir_senha(5, sessao, _gestor()))
    assert "senha_temporaria" in resultado
    assert _negacoes(sessao) == []
