"""Fase 8: testes de regressao de seguranca contra Postgres real.

Complementa tests/test_saas_rls_postgres.py (mesma tecnica: asyncpg real,
role de aplicacao efemera, pytest.skip se o banco nao estiver disponivel)
para as tabelas que nasceram nas Fases 3-5 e ainda nao tinham uma bateria
de isolamento real: interacoes_versoes_sistema (confirmacao de leitura),
problemas_versoes_sistema (reporte de problema) e feature_flags_organizacoes
(rollout por organizacao). Critério de aceite da Fase 8: "nenhuma
organizacao consegue consultar ou alterar as configuracoes de outra".

Tambem cobre, contra o banco de verdade (nao FakeSession):
- imutabilidade real de VersaoSistema publicada (o trigger do banco, nao
  so a checagem em Python de app/api/versoes_sistema.py);
- a UniqueConstraint uq_interacao_versao_usuario;
- concorrencia real de duas ativacoes simultaneas de feature flag pra
  mesma organizacao (UniqueConstraint uq_feature_flag_organizacao).
"""

import asyncio
import os
from urllib.parse import quote, urlsplit
from uuid import uuid4

import asyncpg
import pytest

ADMIN_DSN = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://inpi:inpi@localhost:5432/inpi")


async def _conectar(dsn: str) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL real indisponivel para teste de seguranca: {type(exc).__name__}")


async def _criar_role_aplicacao(admin: asyncpg.Connection) -> tuple[str, str]:
    role = f"fase8_test_{uuid4().hex[:12]}"
    senha = uuid4().hex + uuid4().hex
    await admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{senha}' INHERIT")
    await admin.execute(f'GRANT inpi_app TO "{role}"')
    origem = urlsplit(ADMIN_DSN)
    host = origem.hostname or "localhost"
    porta = origem.port or 5432
    banco = origem.path.lstrip("/") or "inpi"
    dsn = f"postgresql://{quote(role)}:{quote(senha)}@{host}:{porta}/{banco}"
    return role, dsn


async def _derrubar_role(admin: asyncpg.Connection, role: str) -> None:
    await admin.execute(f'REVOKE inpi_app FROM "{role}"')
    await admin.execute(f'DROP ROLE IF EXISTS "{role}"')


async def _criar_versao_publicada(admin: asyncpg.Connection, *, sufixo: str) -> int:
    """Cria uma VersaoSistema ja publicada direto via SQL (bypassa a API
    de proposito -- estes testes validam a protecao do PROPRIO banco, nao
    a validacao da camada de aplicacao, ja coberta em test_versoes_sistema.py)."""
    return await admin.fetchval(
        """
        INSERT INTO versoes_sistema
            (versao, titulo, problema_identificado, solucao_aplicada, tipo_atualizacao,
             modulos_afetados, implantada_em, commit_sha, evidencias_testes, instrucoes,
             plano_rollback, conteudo_hash, status, criado_por, publicado_por, publicado_em)
        VALUES ($1, 'Versao de teste Fase 8', 'Problema de teste com mais de vinte caracteres.',
                'Solucao de teste com mais de vinte caracteres.', 'correcao', '["producao"]',
                now(), $2, '[{"resultado": "aprovado"}]', 'Instrucoes de teste.',
                'Plano de rollback de teste.', $3, 'publicada',
                'fase8@teste.local', 'fase8@teste.local', now())
        RETURNING id
        """,
        f"fase8-{sufixo}",
        "f" * 40,
        "a" * 64,
    )


async def _criar_organizacao(admin: asyncpg.Connection, *, sufixo: str, tag: str) -> tuple[int, int]:
    plano_id = await admin.fetchval("SELECT id FROM planos_saas ORDER BY id LIMIT 1")
    if plano_id is None:
        pytest.skip("Base de integracao precisa de ao menos um plano SaaS")
    org_id = await admin.fetchval(
        "INSERT INTO organizacoes (nome, slug, plano_id) VALUES ($1, $2, $3) RETURNING id",
        f"Fase8 {tag} {sufixo}",
        f"fase8-{tag}-{sufixo}",
        plano_id,
    )
    usuario_id = await admin.fetchval(
        """
        INSERT INTO usuarios_operacoes (organizacao_id, nome, usuario, email, senha_hash, alterar_senha)
        VALUES ($1, $2, $3, $4, 'hash-teste', false) RETURNING id
        """,
        org_id,
        f"Usuario {tag}",
        f"fase8.{tag}.{sufixo}",
        f"fase8.{tag}.{sufixo}@example.test",
    )
    return org_id, usuario_id


# --- Isolamento entre organizações (critério de aceite da Fase 8) ---------


@pytest.mark.asyncio
async def test_rls_real_isola_interacoes_e_problemas_versao_entre_organizacoes() -> None:
    admin = await _conectar(ADMIN_DSN)
    role: str | None = None
    sufixo = uuid4().hex[:12]
    org_a = org_b = None
    versao_id = None
    try:
        versao_id = await _criar_versao_publicada(admin, sufixo=sufixo)
        org_a, usuario_a = await _criar_organizacao(admin, sufixo=sufixo, tag="a")
        org_b, usuario_b = await _criar_organizacao(admin, sufixo=sufixo, tag="b")

        interacao_a = await admin.fetchval(
            """
            INSERT INTO interacoes_versoes_sistema (versao_sistema_id, organizacao_id, usuario_id, confirmado_em)
            VALUES ($1, $2, $3, now()) RETURNING id
            """,
            versao_id,
            org_a,
            usuario_a,
        )
        interacao_b = await admin.fetchval(
            """
            INSERT INTO interacoes_versoes_sistema (versao_sistema_id, organizacao_id, usuario_id, confirmado_em)
            VALUES ($1, $2, $3, now()) RETURNING id
            """,
            versao_id,
            org_b,
            usuario_b,
        )
        problema_a = await admin.fetchval(
            """
            INSERT INTO problemas_versoes_sistema
                (versao_sistema_id, organizacao_id, usuario_id, categoria, descricao, status)
            VALUES ($1, $2, $3, 'erro', 'Descricao de teste com mais de vinte caracteres.', 'aberto')
            RETURNING id
            """,
            versao_id,
            org_a,
            usuario_a,
        )

        role, app_dsn = await _criar_role_aplicacao(admin)
        app_conn = await _conectar(app_dsn)
        try:
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.organizacao_id', $1, true), set_config('app.superadmin', 'false', true)",
                    str(org_a),
                )
                # Consulta: só enxerga a própria organização.
                assert await app_conn.fetchval(
                    "SELECT count(*) FROM interacoes_versoes_sistema WHERE id = $1", interacao_a
                ) == 1
                assert await app_conn.fetchval(
                    "SELECT count(*) FROM interacoes_versoes_sistema WHERE id = $1", interacao_b
                ) == 0

                # Alteração cruzada: UPDATE em linha de outra organização não afeta nada.
                assert (
                    await app_conn.execute(
                        "UPDATE interacoes_versoes_sistema SET adiado_ate = now() WHERE id = $1", interacao_b
                    )
                    == "UPDATE 0"
                )
                # Confere que a própria organização consegue mexer no que é dela.
                assert (
                    await app_conn.execute("DELETE FROM problemas_versoes_sistema WHERE id = $1", problema_a)
                    == "DELETE 1"
                )

                # Inserção cruzada: tentar gravar um relato em nome de outra organização é rejeitado pela policy.
                insercao_cruzada = app_conn.transaction()
                await insercao_cruzada.start()
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await app_conn.execute(
                        """
                        INSERT INTO problemas_versoes_sistema
                            (versao_sistema_id, organizacao_id, usuario_id, categoria, descricao, status)
                        VALUES ($1, $2, $3, 'erro', 'Tentativa de escrita cruzada entre organizacoes.', 'aberto')
                        """,
                        versao_id,
                        org_b,
                        usuario_a,
                    )
                await insercao_cruzada.rollback()
        finally:
            await app_conn.close()
    finally:
        if role:
            await _derrubar_role(admin, role)
        for org_id in (org_a, org_b):
            if org_id is not None:
                await admin.execute(
                    "DELETE FROM problemas_versoes_sistema WHERE organizacao_id = $1", org_id
                )
                await admin.execute(
                    "DELETE FROM interacoes_versoes_sistema WHERE organizacao_id = $1", org_id
                )
                await admin.execute("DELETE FROM usuarios_operacoes WHERE organizacao_id = $1", org_id)
                await admin.execute("DELETE FROM organizacoes WHERE id = $1", org_id)
        if versao_id is not None:
            # Versao "publicada" e imutavel no banco (trigger) -- nao da pra
            # hard-delete, so arquivar (unica transicao permitida a partir
            # de "publicada").
            await admin.execute(
                """
                UPDATE versoes_sistema
                SET status = 'arquivada', arquivado_em = now(), arquivado_por = 'fase8@teste.local',
                    arquivamento_motivo = 'Limpeza de dado de teste da Fase 8.'
                WHERE id = $1
                """,
                versao_id,
            )
        await admin.close()


@pytest.mark.asyncio
async def test_uq_interacao_versao_usuario_impede_confirmacao_duplicada() -> None:
    """Unicidade real (versao_sistema_id, organizacao_id, usuario_id) --
    sem isso, duas confirmações simultâneas do mesmo usuário poderiam
    virar duas linhas (app/api/atualizacoes.py::_interacao já faz um
    SELECT antes de inserir, mas só a constraint garante contra corrida)."""
    admin = await _conectar(ADMIN_DSN)
    org_id = versao_id = None
    try:
        sufixo = uuid4().hex[:12]
        versao_id = await _criar_versao_publicada(admin, sufixo=sufixo)
        org_id, usuario_id = await _criar_organizacao(admin, sufixo=sufixo, tag="uq")
        await admin.execute(
            "INSERT INTO interacoes_versoes_sistema (versao_sistema_id, organizacao_id, usuario_id) VALUES ($1, $2, $3)",
            versao_id,
            org_id,
            usuario_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await admin.execute(
                "INSERT INTO interacoes_versoes_sistema (versao_sistema_id, organizacao_id, usuario_id) VALUES ($1, $2, $3)",
                versao_id,
                org_id,
                usuario_id,
            )
    finally:
        if org_id is not None:
            await admin.execute("DELETE FROM interacoes_versoes_sistema WHERE organizacao_id = $1", org_id)
            await admin.execute("DELETE FROM usuarios_operacoes WHERE organizacao_id = $1", org_id)
            await admin.execute("DELETE FROM organizacoes WHERE id = $1", org_id)
        if versao_id is not None:
            # Versao "publicada" e imutavel no banco (trigger) -- nao da pra
            # hard-delete, so arquivar (unica transicao permitida a partir
            # de "publicada").
            await admin.execute(
                """
                UPDATE versoes_sistema
                SET status = 'arquivada', arquivado_em = now(), arquivado_por = 'fase8@teste.local',
                    arquivamento_motivo = 'Limpeza de dado de teste da Fase 8.'
                WHERE id = $1
                """,
                versao_id,
            )
        await admin.close()


# --- Imutabilidade das versões publicadas (o trigger do banco) -------------


@pytest.mark.asyncio
async def test_trigger_real_bloqueia_update_direto_em_versao_publicada() -> None:
    """app/api/versoes_sistema.py::editar_versao já recusa editar uma
    versão publicada (testado com FakeSession) -- este teste prova que a
    proteção também existe no PRÓPRIO banco (trg_proteger_versao_sistema),
    não só na camada de aplicação: nem um UPDATE direto via SQL, bypassando
    a API inteira, consegue alterar uma versão publicada."""
    admin = await _conectar(ADMIN_DSN)
    versao_id = None
    try:
        versao_id = await _criar_versao_publicada(admin, sufixo=uuid4().hex[:12])
        with pytest.raises(asyncpg.RaiseError, match="imutavel"):
            await admin.execute("UPDATE versoes_sistema SET titulo = 'alterado' WHERE id = $1", versao_id)
        with pytest.raises(asyncpg.RaiseError, match="imutavel"):
            await admin.execute("DELETE FROM versoes_sistema WHERE id = $1", versao_id)
    finally:
        if versao_id is not None:
            # O mesmo trigger que bloqueia UPDATE numa versao "publicada"
            # tambem bloqueia DELETE numa versao "arquivada" -- e por
            # design (imutabilidade real), entao nao da pra fazer um
            # hard-delete de limpeza aqui. O registro de teste fica
            # arquivado (sufixo aleatorio evita colisao entre execucoes).
            await admin.execute(
                """
                UPDATE versoes_sistema
                SET status = 'arquivada', arquivado_em = now(), arquivado_por = 'fase8@teste.local',
                    arquivamento_motivo = 'Limpeza de dado de teste da Fase 8.'
                WHERE id = $1
                """,
                versao_id,
            )
        await admin.close()


# --- Concorrência na ativação de feature flag -------------------------------


@pytest.mark.asyncio
async def test_concorrencia_real_ativacao_feature_flag_organizacao() -> None:
    """Duas conexões reais tentando ativar a MESMA flag para a MESMA
    organização ao mesmo tempo -- só uma pode vencer (uq_feature_flag_organizacao);
    a outra tem que falhar de forma limpa (UniqueViolationError), nunca
    criar uma segunda linha silenciosamente."""
    admin = await _conectar(ADMIN_DSN)
    org_id = flag_id = None
    try:
        sufixo = uuid4().hex[:12]
        org_id, _usuario_id = await _criar_organizacao(admin, sufixo=sufixo, tag="conc")
        flag_id = await admin.fetchval(
            """
            INSERT INTO feature_flags (codigo, nome, descricao, modulos_envolvidos, dependencias, estado_padrao)
            VALUES ($1, 'Flag de concorrencia', 'Descricao de teste.', '[]', '[]', 'desligado')
            RETURNING id
            """,
            f"fase8-concorrencia-{sufixo}",
        )

        conexao_1 = await _conectar(ADMIN_DSN)
        conexao_2 = await _conectar(ADMIN_DSN)
        try:

            async def _ativar(conexao: asyncpg.Connection) -> str | None:
                try:
                    await conexao.execute(
                        "INSERT INTO feature_flags_organizacoes (feature_flag_id, organizacao_id, estado) "
                        "VALUES ($1, $2, 'ativo')",
                        flag_id,
                        org_id,
                    )
                    return None
                except asyncpg.UniqueViolationError:
                    return "conflito"

            resultados = await asyncio.gather(_ativar(conexao_1), _ativar(conexao_2))
        finally:
            await conexao_1.close()
            await conexao_2.close()

        # Exatamente uma das duas tentativas venceu -- a outra bateu na
        # constraint, nunca as duas conseguiram inserir uma linha cada.
        # (sorted() quebraria aqui: None e str nao sao comparaveis em Python 3.)
        assert resultados.count(None) == 1
        assert resultados.count("conflito") == 1
        total = await admin.fetchval(
            "SELECT count(*) FROM feature_flags_organizacoes WHERE feature_flag_id = $1 AND organizacao_id = $2",
            flag_id,
            org_id,
        )
        assert total == 1
    finally:
        if flag_id is not None:
            await admin.execute("DELETE FROM feature_flags_organizacoes WHERE feature_flag_id = $1", flag_id)
            await admin.execute("DELETE FROM feature_flags WHERE id = $1", flag_id)
        if org_id is not None:
            await admin.execute("DELETE FROM usuarios_operacoes WHERE organizacao_id = $1", org_id)
            await admin.execute("DELETE FROM organizacoes WHERE id = $1", org_id)
        await admin.close()
