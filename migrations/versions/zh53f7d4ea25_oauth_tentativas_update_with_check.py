"""Política de UPDATE de tentativas_oauth com WITH CHECK explícito.

Revisão do Codex no PR #186 (auditoria externa A02): num UPDATE sem WITH CHECK o PostgreSQL
aplica a cláusula USING também à linha NOVA. A política antiga só aceitava a linha quando
``mfa_token_hash`` (ou ``state_hash``) casava com o valor do contexto; ao concluir ou queimar
o desafio do MFA social o token vira NULL, a linha nova deixava de casar e o banco recusava
(violação de RLS) -- o login social por MFA só funcionaria para superadmin.

Agora o WITH CHECK permite a transição terminal (token NULL) no escopo ``oauth_mfa``, sem
afrouxar quem pode atualizar: a linha antiga continua precisando casar com o escopo/valor.

Revision ID: zh53f7d4ea25
Revises: zg52e6c3d914
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zh53f7d4ea25"
down_revision: str | None = "zg52e6c3d914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPER = "current_setting('app.superadmin', true) = 'true'"
SCOPE = "current_setting('app.auth_scope', true)"
VALUE = "current_setting('app.auth_value', true)"

USING = f"{SUPER} OR ({SCOPE} = 'oauth_state' AND state_hash = {VALUE}) OR ({SCOPE} = 'oauth_mfa' AND mfa_token_hash = {VALUE})"
WITH_CHECK = (
    f"{SUPER} OR ({SCOPE} = 'oauth_state' AND state_hash = {VALUE}) "
    f"OR ({SCOPE} = 'oauth_mfa' AND (mfa_token_hash IS NULL OR mfa_token_hash = {VALUE}))"
)


def upgrade() -> None:
    op.execute('DROP POLICY IF EXISTS oauth_attempt_update ON "tentativas_oauth"')
    op.execute(
        f'CREATE POLICY oauth_attempt_update ON "tentativas_oauth" FOR UPDATE USING ({USING}) WITH CHECK ({WITH_CHECK})'
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS oauth_attempt_update ON "tentativas_oauth"')
    op.execute(f'CREATE POLICY oauth_attempt_update ON "tentativas_oauth" FOR UPDATE USING ({USING})')
