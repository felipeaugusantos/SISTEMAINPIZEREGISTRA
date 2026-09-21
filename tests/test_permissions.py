from fastapi.testclient import TestClient

from app.api.usuarios import UsuarioInput, UsuarioUpdate
from app.auth import hash_senha, obter_usuario_atual, verificar_senha
from app.main import app
from tests.conftest import auth_override, usuario_teste


def test_senhas_usam_hash_e_validacao() -> None:
    senha = "Uma-Senha-Forte-2026"
    hash_gerado = hash_senha(senha)
    assert senha not in hash_gerado
    assert verificar_senha(hash_gerado, senha)
    assert not verificar_senha(hash_gerado, "senha-incorreta")


def test_rota_permitida_para_operador_autorizado() -> None:
    usuario = usuario_teste("operador", {"risk.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/admin/risco")
        assert resposta.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_rota_negada_para_operador_sem_permissao() -> None:
    usuario = usuario_teste("operador", {"leads.view"})
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        resposta = TestClient(app).get("/admin/risco", follow_redirects=False)
        assert resposta.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_perfil_financeiro_acessa_apenas_pagina_financeira() -> None:
    from app.permissions import permissoes_do_perfil

    usuario = usuario_teste("financeiro", set(permissoes_do_perfil("financeiro")))
    app.dependency_overrides[obter_usuario_atual] = auth_override(usuario)
    try:
        cliente = TestClient(app)
        assert cliente.get("/admin/financeiro").status_code == 200
        assert cliente.get("/admin", follow_redirects=False).status_code == 403
        assert cliente.get("/admin/pesquisas", follow_redirects=False).status_code == 403
        assert cliente.get("/admin/usuarios", follow_redirects=False).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_perfis_ceo_e_tech_possuem_acesso_integral() -> None:
    from app.permissions import CHAVES_PERMISSAO, permissoes_do_perfil

    assert permissoes_do_perfil("ceo") == CHAVES_PERMISSAO
    assert permissoes_do_perfil("tech") == CHAVES_PERMISSAO


def test_perfil_supervisor_gerencia_juridico_mas_nao_confirma_prazo_critico() -> None:
    """Achado baixo da Fase 8 (auditoria jurídica, 21/09/2026): confirmar
    revisão humana de prazo CRÍTICO exige permissão à parte de
    legal.manage (ver app/api/juridico.py::atualizar_prazo)."""
    from app.permissions import permissoes_do_perfil

    permissoes = permissoes_do_perfil("supervisor")
    assert "legal.manage" in permissoes
    assert "legal.confirm_critical" not in permissoes


def test_http_basic_foi_removido() -> None:
    resposta = TestClient(app).get("/v1/admin/leads", auth=("admin", "qualquer-senha"))
    assert resposta.status_code == 401


def test_email_interno_do_admin_pode_ser_mantido() -> None:
    cadastro = UsuarioInput(nome="Administrador", usuario="admin", email="admin@zeregistra.local")
    alteracao = UsuarioUpdate(email="admin@zeregistra.local")
    assert cadastro.email == "admin@zeregistra.local"
    assert alteracao.email == "admin@zeregistra.local"
