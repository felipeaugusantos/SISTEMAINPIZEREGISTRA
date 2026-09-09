"""cadastro tecnico e imutavel de versoes do sistema

Revision ID: j21t6v2q953
Revises: i20s5u1p842
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j21t6v2q953"
down_revision: str | None = "i20s5u1p842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "versoes_sistema",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("versao", sa.String(60), nullable=False),
        sa.Column("titulo", sa.String(180), nullable=False),
        sa.Column("problema_identificado", sa.Text(), nullable=False),
        sa.Column("solucao_aplicada", sa.Text(), nullable=False),
        sa.Column("tipo_atualizacao", sa.String(30), nullable=False),
        sa.Column(
            "modulos_afetados",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("implantada_em", sa.DateTime(timezone=True)),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("migration_revision", sa.String(64)),
        sa.Column(
            "evidencias_testes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "riscos_conhecidos",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("instrucoes", sa.Text(), nullable=False),
        sa.Column("plano_rollback", sa.Text(), nullable=False),
        sa.Column("conteudo_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="rascunho"),
        sa.Column(
            "criado_por_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "publicado_por_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("publicado_por", sa.String(254)),
        sa.Column("publicado_em", sa.DateTime(timezone=True)),
        sa.Column(
            "arquivado_por_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("arquivado_por", sa.String(254)),
        sa.Column("arquivado_em", sa.DateTime(timezone=True)),
        sa.Column("arquivamento_motivo", sa.Text()),
        sa.UniqueConstraint("versao", name="uq_versoes_sistema_versao"),
        sa.CheckConstraint(
            "tipo_atualizacao IN ('critica', 'correcao', 'funcionalidade')",
            name="ck_versao_sistema_tipo",
        ),
        sa.CheckConstraint(
            "status IN ('rascunho', 'publicada', 'arquivada')",
            name="ck_versao_sistema_status",
        ),
        sa.CheckConstraint("length(commit_sha) = 40", name="ck_versao_sistema_commit_sha"),
        sa.CheckConstraint("length(conteudo_hash) = 64", name="ck_versao_sistema_hash"),
        sa.CheckConstraint(
            "json_typeof(modulos_afetados) = 'array'",
            name="ck_versao_sistema_modulos_array",
        ),
        sa.CheckConstraint(
            "json_typeof(evidencias_testes) = 'array'",
            name="ck_versao_sistema_evidencias_array",
        ),
        sa.CheckConstraint(
            "json_typeof(riscos_conhecidos) = 'array'",
            name="ck_versao_sistema_riscos_array",
        ),
        sa.CheckConstraint(
            "status = 'rascunho' OR "
            "(implantada_em IS NOT NULL AND publicado_em IS NOT NULL AND publicado_por IS NOT NULL)",
            name="ck_versao_sistema_publicacao",
        ),
        sa.CheckConstraint(
            "status <> 'arquivada' OR "
            "(arquivado_em IS NOT NULL AND arquivado_por IS NOT NULL AND arquivamento_motivo IS NOT NULL)",
            name="ck_versao_sistema_arquivamento",
        ),
    )
    for coluna in (
        "versao",
        "tipo_atualizacao",
        "status",
        "implantada_em",
        "commit_sha",
        "migration_revision",
        "conteudo_hash",
        "criado_por_id",
        "criado_em",
        "publicado_por_id",
        "publicado_em",
        "arquivado_por_id",
        "arquivado_em",
    ):
        op.create_index(f"ix_versoes_sistema_{coluna}", "versoes_sistema", [coluna])

    op.execute(
        """
        CREATE FUNCTION proteger_versao_sistema_publicada()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.status IN ('publicada', 'arquivada') THEN
                RAISE EXCEPTION 'versao publicada ou arquivada e imutavel';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'arquivada' THEN
                RAISE EXCEPTION 'versao arquivada e imutavel';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'publicada' THEN
                IF NEW.status <> 'arquivada'
                   OR (to_jsonb(NEW) - ARRAY[
                       'status', 'atualizado_em', 'arquivado_por_id',
                       'arquivado_por', 'arquivado_em', 'arquivamento_motivo'
                   ]) <> (to_jsonb(OLD) - ARRAY[
                       'status', 'atualizado_em', 'arquivado_por_id',
                       'arquivado_por', 'arquivado_em', 'arquivamento_motivo'
                   ]) THEN
                    RAISE EXCEPTION 'versao publicada e imutavel';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        CREATE TRIGGER trg_proteger_versao_sistema
        BEFORE UPDATE OR DELETE ON versoes_sistema
        FOR EACH ROW EXECUTE FUNCTION proteger_versao_sistema_publicada();
        """
    )

    superadmin = "current_setting('app.superadmin', true) = 'true'"
    op.execute('ALTER TABLE "versoes_sistema" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "versoes_sistema" FORCE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY versoes_sistema_read ON "versoes_sistema" FOR SELECT USING (true)')
    op.execute(
        'CREATE POLICY versoes_sistema_insert ON "versoes_sistema" FOR INSERT '
        f"WITH CHECK ({superadmin})"
    )
    op.execute(
        'CREATE POLICY versoes_sistema_update ON "versoes_sistema" FOR UPDATE '
        f"USING ({superadmin}) WITH CHECK ({superadmin})"
    )
    op.execute(
        'CREATE POLICY versoes_sistema_delete ON "versoes_sistema" FOR DELETE '
        f"USING ({superadmin})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "versoes_sistema" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_proteger_versao_sistema ON versoes_sistema")
    op.execute("DROP FUNCTION IF EXISTS proteger_versao_sistema_publicada()")
    op.drop_table("versoes_sistema")
