import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import criar_sessao, hash_senha, hash_token, obter_usuario_atual
from app.database import get_session
from app.main import app
from app.models import UsuarioOperacoes
from tests.conftest import FakeResult, FakeSession, auth_override, usuario_teste
from tests.test_mfa_obrigatorio import _requisicao_autenticada, _sessao_de_operador

# Auditoria externa de 13/10/2026, A04: a sessão administrativa guarda a geração da senha com que foi
# autenticada e deixa de valer se a senha mudar depois (corrida login x redefinição).


def _autenticar(sessao, token: str, caminho: str = "/v1/auth/me"):
    session = FakeSession([FakeResult(scalar=sessao)])
    return session, asyncio.run(obter_usuario_atual(_requisicao_autenticada(caminho, token), session))


def test_sessao_com_a_mesma_geracao_da_senha_continua_valendo() -> None:
    sessao, token = _sessao_de_operador(alterar_senha=False, mfa_ativo=True)
    sessao.senha_versao = 3
    sessao.usuario.senha_versao = 3
    _, resultado = _autenticar(sessao, token)
    assert resultado.id == 1
    assert sessao.revogada_em is None


def test_contas_e_sessoes_antigas_sem_o_campo_continuam_valendo() -> None:
    # Linhas anteriores à migration: default 1 nas duas tabelas (ou None em objetos ainda não gravados).
    sessao, token = _sessao_de_operador(alterar_senha=False, mfa_ativo=True)
    sessao.senha_versao = None
    sessao.usuario.senha_versao = 1
    _, resultado = _autenticar(sessao, token)
    assert resultado.id == 1


def test_a04_sessao_de_senha_antiga_e_recusada_e_revogada() -> None:
    sessao, token = _sessao_de_operador(alterar_senha=False, mfa_ativo=True)
    sessao.senha_versao = 1  # gravada por um login que validou a senha antiga
    sessao.usuario.senha_versao = 2  # a redefinição já aconteceu
    session = FakeSession([FakeResult(scalar=sessao)])

    with pytest.raises(HTTPException) as erro:
        asyncio.run(obter_usuario_atual(_requisicao_autenticada("/v1/auth/me", token), session))

    assert erro.value.status_code == 401
    assert sessao.revogada_em is not None
    assert sessao.motivo_revogacao == "senha_alterada"
    assert session.commits == 1


def test_a04_painel_redireciona_para_o_login_quando_a_senha_mudou() -> None:
    sessao, token = _sessao_de_operador(alterar_senha=False, mfa_ativo=True)
    sessao.senha_versao = 1
    sessao.usuario.senha_versao = 2
    with pytest.raises(HTTPException) as erro:
        asyncio.run(obter_usuario_atual(_requisicao_autenticada("/admin", token), FakeSession([FakeResult(scalar=sessao)])))
    assert erro.value.status_code == 303
    assert erro.value.headers["Location"] == "/login"


def test_a04_corrida_login_x_redefinicao_em_sequencia() -> None:
    """O cenário do achado: o login lê e valida a senha antiga, a redefinição confirma, e SÓ ENTÃO
    o login grava a sessão. A sessão nasce com a geração lida (1) e não passa na primeira requisição."""
    sessao_base, token = _sessao_de_operador(alterar_senha=False, mfa_ativo=True)
    usuario = sessao_base.usuario
    usuario.senha_versao = 1

    versao_lida_no_login = usuario.senha_versao  # lida junto com o hash verificado
    usuario.senha_versao = (usuario.senha_versao or 1) + 1  # a redefinição de senha confirma no meio
    requisicao = _requisicao_autenticada("/v1/auth/me", token)
    sessao_criada, _token, _csrf = criar_sessao(usuario.id, requisicao, senha_versao=versao_lida_no_login)
    sessao_criada.usuario = usuario
    sessao_criada.token_hash = hash_token(token)
    sessao_criada.ultimo_acesso_em = sessao_base.ultimo_acesso_em

    assert sessao_criada.senha_versao == 1
    with pytest.raises(HTTPException) as erro:
        asyncio.run(obter_usuario_atual(requisicao, FakeSession([FakeResult(scalar=sessao_criada)])))
    assert erro.value.status_code == 401


def test_criar_sessao_guarda_a_geracao_informada() -> None:
    sessao, _token, _csrf = criar_sessao(7, _requisicao_autenticada("/", "x"), senha_versao=5)
    assert sessao.senha_versao == 5


def _e_incremento_do_banco(valor: object) -> bool:
    """True se o valor é uma expressão SQL do tipo ``senha_versao + 1`` (e não um int calculado)."""
    texto = str(valor)
    return not isinstance(valor, int) and "senha_versao" in texto and "+" in texto


class _SessaoComTenant(FakeSession):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.info = {"organizacao_id": 1}


def test_trocar_a_propria_senha_sobe_a_geracao_e_mantem_a_sessao_atual_valida() -> None:
    senha_atual = "SenhaAtual-123!x"
    registro = UsuarioOperacoes(
        id=1,
        organizacao_id=1,
        nome="Admin Teste",
        usuario="admin",
        email="admin@teste.local",
        perfil="administrador",
        senha_hash=hash_senha(senha_atual),
        ativo=True,
        alterar_senha=True,
        senha_versao=4,
        mfa_ativo=False,
        permissoes=[],
    )
    sessao_fake = _SessaoComTenant([], objetos_get=[registro])

    async def _sessao():
        yield sessao_fake

    app.dependency_overrides[get_session] = _sessao
    app.dependency_overrides[obter_usuario_atual] = auth_override(replace(usuario_teste(), csrf_hash=hash_token("tok")))
    try:
        resposta = TestClient(app, raise_server_exceptions=False).post(
            "/v1/auth/trocar-senha",
            json={"senha_atual": senha_atual, "nova_senha": "NovaSenha-456!y"},
            headers={"X-CSRF-Token": "tok"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    # O incremento é do BANCO (senha_versao = senha_versao + 1), não um inteiro calculado em Python.
    assert _e_incremento_do_banco(registro.senha_versao)
    comandos = [str(instrucao) for instrucao in sessao_fake.executados]
    # uma atualização revoga as OUTRAS sessões; outra passa a sessão em uso para a nova geração,
    # lida do próprio banco por subconsulta (nenhum valor calculado no Python)
    assert any("SET senha_versao=(SELECT usuarios_operacoes.senha_versao" in comando for comando in comandos)


async def test_migration_cria_as_colunas_com_padrao_1_sem_quebrar_linhas_existentes() -> None:
    import os

    import asyncpg

    dsn = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")
    try:
        conexao = await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponivel: {type(exc).__name__}")
    try:
        linhas = await conexao.fetch(
            """
            SELECT table_name, column_default, is_nullable, data_type
            FROM information_schema.columns
            WHERE column_name = 'senha_versao' AND table_name IN ('usuarios_operacoes', 'sessoes_operacoes')
            """
        )
        por_tabela = {linha["table_name"]: linha for linha in linhas}
        assert set(por_tabela) == {"usuarios_operacoes", "sessoes_operacoes"}
        for linha in por_tabela.values():
            assert linha["column_default"] == "1"
            assert linha["is_nullable"] == "NO"
            assert linha["data_type"] == "integer"
        # Linhas já existentes receberam 1 nas duas tabelas: contas e sessões antigas seguem iguais.
        divergentes = await conexao.fetchval(
            """
            SELECT count(*) FROM sessoes_operacoes s JOIN usuarios_operacoes u ON u.id = s.usuario_id
            WHERE s.senha_versao <> u.senha_versao AND u.senha_versao = 1 AND s.senha_versao = 1
            """
        )
        assert divergentes == 0
    finally:
        await conexao.close()


def test_redefinicao_pelo_administrador_incrementa_a_geracao_no_banco() -> None:
    from app.api.usuarios import redefinir_senha

    alvo = UsuarioOperacoes(
        id=5,
        organizacao_id=1,
        nome="Operador",
        usuario="operador",
        email="operador@teste.local",
        perfil="operador",
        senha_hash="hash-antigo",
        ativo=True,
        senha_versao=1,
        permissoes=[],
    )
    sessao = _SessaoComTenant([FakeResult(scalar=alvo)])

    resultado = asyncio.run(redefinir_senha(5, sessao, usuario_teste(perfil="administrador")))

    assert "senha_temporaria" in resultado
    assert _e_incremento_do_banco(alvo.senha_versao)


def test_nenhum_caminho_calcula_a_geracao_da_senha_em_python() -> None:
    # Revisão do Codex (PR #192): ler e somar em Python deixa duas trocas simultâneas gravarem o mesmo número.
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "app"
    suspeitos = [
        str(arquivo.relative_to(raiz.parent))
        for arquivo in raiz.rglob("*.py")
        if "senha_versao or 1) + 1" in arquivo.read_text(encoding="utf-8")
    ]
    assert suspeitos == []


def test_tela_de_usuarios_avisa_ao_redefinir_a_propria_senha() -> None:
    # Redefinir a própria senha encerra a sessão (A04): a tela avisa antes e leva ao login depois,
    # em vez de mostrar "Falha na operação" quando a lista é recarregada com 401.
    from pathlib import Path

    script = Path("app/web/static/admin-usuarios.js").read_text(encoding="utf-8")
    assert "state.usuario_atual_id" in script
    assert "sua sessão será encerrada" in script
    assert 'location.href="/login"' in script


def test_confirmacao_da_tela_nao_tem_quebra_de_linha_real_dentro_da_string() -> None:
    # Revisão do Codex (PR #194): quebra real dentro de "..." é erro de sintaxe e derruba a página inteira.
    from pathlib import Path

    script = Path("app/web/static/admin-usuarios.js").read_text(encoding="utf-8")
    barra_n = chr(92) + "n"
    assert "SUA PRÓPRIA senha." + barra_n + barra_n + "Ao confirmar" in script
    for numero, linha in enumerate(script.splitlines(), start=1):
        assert linha.count('"') % 2 == 0, f"aspas desbalanceadas na linha {numero}"
