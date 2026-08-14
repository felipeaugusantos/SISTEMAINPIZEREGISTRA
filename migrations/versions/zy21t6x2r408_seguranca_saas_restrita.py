"""seguranca SaaS com autenticacao restrita e auditoria ampliada

Revision ID: zy21t6x2r408
Revises: zx10s5w1q397
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "zy21t6x2r408"
down_revision: str | None = "zx10s5w1q397"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT = "NULLIF(current_setting('app.organizacao_id', true), '')::bigint"
SUPER = "current_setting('app.superadmin', true) = 'true'"
SCOPE = "current_setting('app.auth_scope', true)"
VALUE = "current_setting('app.auth_value', true)"
AUX = "current_setting('app.auth_aux', true)"
BOOTSTRAP = "NULLIF(current_setting('app.organizacao_id', true), '') IS NULL"


def _substituir_leitura(tabela: str, expressao: str) -> None:
    op.execute(f'DROP POLICY IF EXISTS tenant_bootstrap_read ON "{tabela}"')
    op.execute(f'DROP POLICY IF EXISTS auth_lookup_read ON "{tabela}"')
    op.execute(f'CREATE POLICY auth_lookup_read ON "{tabela}" FOR SELECT USING ({expressao})')


def upgrade() -> None:
    op.add_column(
        "credenciais_integracao",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_credenciais_integracao_revoked_at",
        "credenciais_integracao",
        ["revoked_at"],
    )
    op.add_column(
        "usuarios_operacoes",
        sa.Column("mfa_segredo_versao", sa.Integer(), nullable=True),
    )
    op.execute("UPDATE usuarios_operacoes SET mfa_segredo_versao = 1 WHERE mfa_segredo IS NOT NULL")

    op.add_column(
        "eventos_auditoria",
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_eventos_auditoria_actor",
        "eventos_auditoria",
        "usuarios_operacoes",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_eventos_auditoria_actor_id", "eventos_auditoria", ["actor_id"])
    for coluna, tamanho in (("resource_type", 80), ("resource_id", 120)):
        op.add_column(
            "eventos_auditoria",
            sa.Column(coluna, sa.String(tamanho), nullable=True),
        )
        op.create_index(f"ix_eventos_auditoria_{coluna}", "eventos_auditoria", [coluna])
    op.add_column("eventos_auditoria", sa.Column("before_state", sa.JSON(), nullable=True))
    op.add_column("eventos_auditoria", sa.Column("after_state", sa.JSON(), nullable=True))

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_auth_usuario_permitido(p_usuario_id bigint)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT CASE current_setting('app.auth_scope', true)
                WHEN 'sessao' THEN EXISTS (
                    SELECT 1 FROM public.sessoes_operacoes s
                    WHERE s.usuario_id = p_usuario_id
                      AND s.token_hash = current_setting('app.auth_value', true)
                )
                WHEN 'recuperacao_token' THEN EXISTS (
                    SELECT 1 FROM public.tokens_recuperacao_senha t
                    WHERE t.usuario_id = p_usuario_id
                      AND t.token_hash = current_setting('app.auth_value', true)
                )
                WHEN 'oauth_identidade' THEN EXISTS (
                    SELECT 1 FROM public.identidades_externas i
                    WHERE i.usuario_id = p_usuario_id
                      AND i.provedor = current_setting('app.auth_aux', true)
                      AND i.provedor_usuario_id = current_setting('app.auth_value', true)
                )
                WHEN 'oauth_state' THEN EXISTS (
                    SELECT 1 FROM public.tentativas_oauth o
                    WHERE o.usuario_id = p_usuario_id
                      AND o.state_hash = current_setting('app.auth_value', true)
                )
                WHEN 'oauth_mfa' THEN EXISTS (
                    SELECT 1 FROM public.tentativas_oauth o
                    WHERE o.usuario_id = p_usuario_id
                      AND o.mfa_token_hash = current_setting('app.auth_value', true)
                )
                ELSE false
            END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.app_auth_usuario_permitido(bigint) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.app_auth_usuario_permitido(bigint) TO inpi_app")

    usuario_auth = (
        f"(({SCOPE} IN ('login', 'recuperacao_email', 'oauth_email')) "
        f"AND (lower(usuario) = {VALUE} OR lower(email) = {VALUE})) "
        f"OR public.app_auth_usuario_permitido(id)"
    )
    _substituir_leitura(
        "usuarios_operacoes", f"{SUPER} OR organizacao_id = {TENANT} OR {usuario_auth}"
    )
    _substituir_leitura(
        "sessoes_operacoes",
        f"{SUPER} OR ({SCOPE} = 'sessao' AND token_hash = {VALUE}) "
        f"OR EXISTS (SELECT 1 FROM usuarios_operacoes u "
        f"WHERE u.id = usuario_id AND u.organizacao_id = {TENANT})",
    )
    _substituir_leitura(
        "tokens_recuperacao_senha",
        f"{SUPER} OR ({SCOPE} = 'recuperacao_token' AND token_hash = {VALUE}) "
        f"OR EXISTS (SELECT 1 FROM usuarios_operacoes u "
        f"WHERE u.id = usuario_id AND u.organizacao_id = {TENANT})",
    )
    _substituir_leitura(
        "usuario_permissoes",
        f"{SUPER} OR EXISTS (SELECT 1 FROM usuarios_operacoes u WHERE u.id = usuario_id)",
    )
    _substituir_leitura(
        "convites_organizacao",
        f"{SUPER} OR organizacao_id = {TENANT} OR ({SCOPE} = 'convite' AND token_hash = {VALUE})",
    )
    _substituir_leitura(
        "credenciais_integracao",
        f"{SUPER} OR organizacao_id = {TENANT} OR "
        f"({SCOPE} = 'integracao' AND token_hash = {VALUE} "
        "AND ativo AND revoked_at IS NULL)",
    )
    _substituir_leitura(
        "dominios_organizacao",
        f"{SUPER} OR organizacao_id = {TENANT} "
        f"OR ({SCOPE} = 'dominio' AND dominio = {VALUE} AND ativo)",
    )
    _substituir_leitura(
        "identidades_externas",
        f"{SUPER} OR organizacao_id = {TENANT} OR "
        f"({SCOPE} = 'oauth_identidade' AND provedor = {AUX} "
        f"AND provedor_usuario_id = {VALUE})",
    )

    op.execute('ALTER TABLE "tentativas_oauth" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "tentativas_oauth" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY oauth_attempt_select ON "tentativas_oauth" FOR SELECT USING ('
        f"{SUPER} OR ({SCOPE} = 'oauth_state' AND state_hash = {VALUE}) "
        f"OR ({SCOPE} = 'oauth_mfa' AND mfa_token_hash = {VALUE}))"
    )
    op.execute(
        'CREATE POLICY oauth_attempt_insert ON "tentativas_oauth" FOR INSERT '
        f"WITH CHECK ({SUPER} OR {SCOPE} = 'oauth_criar')"
    )
    op.execute(
        'CREATE POLICY oauth_attempt_update ON "tentativas_oauth" FOR UPDATE USING ('
        f"{SUPER} OR ({SCOPE} = 'oauth_state' AND state_hash = {VALUE}) "
        f"OR ({SCOPE} = 'oauth_mfa' AND mfa_token_hash = {VALUE}))"
    )
    op.execute(
        'CREATE POLICY oauth_attempt_delete ON "tentativas_oauth" FOR DELETE USING ('
        f"{SUPER} OR expira_em < now() OR "
        f"({SCOPE} = 'oauth_state' AND state_hash = {VALUE}) "
        f"OR ({SCOPE} = 'oauth_mfa' AND mfa_token_hash = {VALUE}))"
    )

    op.execute('DROP POLICY IF EXISTS tenant_audit_insert ON "eventos_auditoria"')
    op.execute(
        'CREATE POLICY tenant_audit_insert ON "eventos_auditoria" FOR INSERT '
        f"WITH CHECK ({SUPER} OR organizacao_id = {TENANT})"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_audit_insert ON "eventos_auditoria"')
    op.execute(
        'CREATE POLICY tenant_audit_insert ON "eventos_auditoria" FOR INSERT WITH CHECK (true)'
    )
    for politica in (
        "oauth_attempt_delete",
        "oauth_attempt_update",
        "oauth_attempt_insert",
        "oauth_attempt_select",
    ):
        op.execute(f'DROP POLICY IF EXISTS {politica} ON "tentativas_oauth"')
    op.execute('ALTER TABLE "tentativas_oauth" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "tentativas_oauth" DISABLE ROW LEVEL SECURITY')

    for tabela in (
        "identidades_externas",
        "dominios_organizacao",
        "credenciais_integracao",
        "convites_organizacao",
        "usuario_permissoes",
        "tokens_recuperacao_senha",
        "sessoes_operacoes",
        "usuarios_operacoes",
    ):
        op.execute(f'DROP POLICY IF EXISTS auth_lookup_read ON "{tabela}"')
    for tabela in (
        "dominios_organizacao",
        "credenciais_integracao",
        "convites_organizacao",
        "usuarios_operacoes",
        "identidades_externas",
    ):
        op.execute(
            f'CREATE POLICY tenant_bootstrap_read ON "{tabela}" FOR SELECT '
            f"USING ({BOOTSTRAP} OR {SUPER} OR organizacao_id = {TENANT})"
        )
    for tabela, coluna in (
        ("sessoes_operacoes", "usuario_id"),
        ("tokens_recuperacao_senha", "usuario_id"),
        ("usuario_permissoes", "usuario_id"),
    ):
        op.execute(
            f'CREATE POLICY tenant_bootstrap_read ON "{tabela}" FOR SELECT USING ('
            f"{BOOTSTRAP} OR {SUPER} OR EXISTS (SELECT 1 FROM usuarios_operacoes u "
            f"WHERE u.id = {coluna} AND ({SUPER} OR u.organizacao_id = {TENANT})))"
        )
    op.execute("DROP FUNCTION IF EXISTS public.app_auth_usuario_permitido(bigint)")

    op.drop_column("eventos_auditoria", "after_state")
    op.drop_column("eventos_auditoria", "before_state")
    for coluna in ("resource_id", "resource_type"):
        op.drop_index(f"ix_eventos_auditoria_{coluna}", table_name="eventos_auditoria")
        op.drop_column("eventos_auditoria", coluna)
    op.drop_index("ix_eventos_auditoria_actor_id", table_name="eventos_auditoria")
    op.drop_constraint("fk_eventos_auditoria_actor", "eventos_auditoria", type_="foreignkey")
    op.drop_column("eventos_auditoria", "actor_id")
    op.drop_column("usuarios_operacoes", "mfa_segredo_versao")
    op.drop_index("ix_credenciais_integracao_revoked_at", table_name="credenciais_integracao")
    op.drop_column("credenciais_integracao", "revoked_at")
