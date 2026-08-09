"""align database constraints with the application model

Revision ID: m03e5c9d4f86
Revises: l92d4b8c3e75
"""

from collections.abc import Sequence

from alembic import op

revision: str = "m03e5c9d4f86"
down_revision: str | None = "l92d4b8c3e75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUNAS_DATA_OBRIGATORIAS = (
    ("controle_aprendizado_marca", "atualizado_em"),
    ("convites_organizacao", "criado_em"),
    ("credenciais_integracao", "criado_em"),
    ("dominios_organizacao", "criado_em"),
    ("execucoes_aprendizado_marca", "iniciado_em"),
    ("modelos_registrabilidade", "treinado_em"),
    ("organizacoes", "criado_em"),
    ("organizacoes", "atualizado_em"),
    ("pares_treinamento_marca", "criado_em"),
    ("planos_saas", "criado_em"),
    ("planos_saas", "atualizado_em"),
    ("previsoes_registrabilidade", "calculado_em"),
    ("rotulos_historicos_marca", "criado_em"),
    ("rotulos_historicos_marca", "atualizado_em"),
    ("sessoes_operacoes", "criado_em"),
    ("sessoes_operacoes", "ultimo_acesso_em"),
    ("usuarios_operacoes", "criado_em"),
    ("usuarios_operacoes", "atualizado_em"),
)


def upgrade() -> None:
    for tabela, coluna in COLUNAS_DATA_OBRIGATORIAS:
        op.execute(f'UPDATE "{tabela}" SET "{coluna}" = now() WHERE "{coluna}" IS NULL')
        op.execute(f'ALTER TABLE "{tabela}" ALTER COLUMN "{coluna}" SET NOT NULL')

    op.execute(
        "DROP INDEX IF EXISTS ix_eventos_cobranca_sandbox_referencia"
    )
    op.execute(
        "ALTER TABLE eventos_cobranca_sandbox "
        "DROP CONSTRAINT IF EXISTS eventos_cobranca_sandbox_referencia_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_eventos_cobranca_sandbox_referencia "
        "ON eventos_cobranca_sandbox (referencia)"
    )
    op.execute("DROP INDEX IF EXISTS ix_tokens_recuperacao_senha_token_hash")
    op.execute(
        "ALTER TABLE tokens_recuperacao_senha "
        "DROP CONSTRAINT IF EXISTS tokens_recuperacao_senha_token_hash_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_tokens_recuperacao_senha_token_hash "
        "ON tokens_recuperacao_senha (token_hash)"
    )
    op.create_index(
        "ix_usuarios_operacoes_mfa_ativo",
        "usuarios_operacoes",
        ["mfa_ativo"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_usuarios_operacoes_mfa_ativo", table_name="usuarios_operacoes")
    for tabela, coluna in reversed(COLUNAS_DATA_OBRIGATORIAS):
        op.execute(f'ALTER TABLE "{tabela}" ALTER COLUMN "{coluna}" DROP NOT NULL')
