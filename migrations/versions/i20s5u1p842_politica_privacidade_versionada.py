"""politica de privacidade versionada e vinculo de consentimentos

Revision ID: i20s5u1p842
Revises: nh30d4k1w842
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i20s5u1p842"
down_revision: str | None = "nh30d4k1w842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "politicas_privacidade",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "organizacao_id",
            sa.BigInteger(),
            sa.ForeignKey("organizacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("versao", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="rascunho"),
        sa.Column("conteudo", sa.Text()),
        sa.Column("documento_referencia", sa.String(500)),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("publicado_em", sa.DateTime(timezone=True)),
        sa.Column("vigencia_em", sa.DateTime(timezone=True)),
        sa.Column(
            "criado_por_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("criado_por", sa.String(254), nullable=False),
        sa.Column(
            "aprovado_por_id",
            sa.BigInteger(),
            sa.ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"),
        ),
        sa.Column("aprovado_por", sa.String(254)),
        sa.Column("motivo_alteracao", sa.Text(), nullable=False),
        sa.Column(
            "requer_novo_consentimento",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.UniqueConstraint(
            "organizacao_id",
            "versao",
            name="uq_politica_privacidade_org_versao",
        ),
        sa.CheckConstraint(
            "status IN ('rascunho', 'publicada', 'revogada')",
            name="ck_politica_privacidade_status",
        ),
        sa.CheckConstraint(
            "num_nonnulls(conteudo, documento_referencia) = 1",
            name="ck_politica_privacidade_documento",
        ),
        sa.CheckConstraint(
            "status = 'rascunho' OR "
            "(publicado_em IS NOT NULL AND vigencia_em IS NOT NULL AND aprovado_por IS NOT NULL)",
            name="ck_politica_privacidade_publicacao",
        ),
        sa.CheckConstraint("length(sha256) = 64", name="ck_politica_privacidade_sha256"),
    )
    for coluna in (
        "organizacao_id",
        "status",
        "sha256",
        "vigencia_em",
        "criado_por_id",
        "aprovado_por_id",
    ):
        op.create_index(
            f"ix_politicas_privacidade_{coluna}",
            "politicas_privacidade",
            [coluna],
        )
    op.create_index(
        "uq_politica_privacidade_publicada_org",
        "politicas_privacidade",
        ["organizacao_id"],
        unique=True,
        postgresql_where=sa.text("status = 'publicada'"),
    )

    conexao = op.get_bind()
    linhas = conexao.execute(
        sa.text(
            """
            SELECT organizacao_id, versao, bool_or(atual) AS atual,
                   coalesce(min(referencia_em), now()) AS referencia_em
            FROM (
                SELECT id AS organizacao_id,
                       politica_privacidade_versao AS versao,
                       true AS atual,
                       criado_em AS referencia_em
                FROM organizacoes
                UNION ALL
                SELECT organizacao_id,
                       consentimento_versao_termo AS versao,
                       false AS atual,
                       consentimento_em AS referencia_em
                FROM leads
                WHERE consentimento_versao_termo IS NOT NULL
            ) versoes
            WHERE versao IS NOT NULL AND btrim(versao) <> ''
            GROUP BY organizacao_id, versao
            """
        )
    ).mappings()
    tabela = sa.table(
        "politicas_privacidade",
        sa.column("organizacao_id", sa.BigInteger()),
        sa.column("versao", sa.String()),
        sa.column("status", sa.String()),
        sa.column("documento_referencia", sa.String()),
        sa.column("sha256", sa.String()),
        sa.column("criado_em", sa.DateTime(timezone=True)),
        sa.column("publicado_em", sa.DateTime(timezone=True)),
        sa.column("vigencia_em", sa.DateTime(timezone=True)),
        sa.column("criado_por", sa.String()),
        sa.column("aprovado_por", sa.String()),
        sa.column("motivo_alteracao", sa.Text()),
        sa.column("requer_novo_consentimento", sa.Boolean()),
    )
    registros = []
    for linha in linhas:
        # O valor precisa permanecer idêntico ao consentimento histórico: a
        # migration cria o vínculo, mas não reescreve aceites já registrados.
        versao = linha["versao"]
        digest = hashlib.sha256(b"referencia:/privacidade").hexdigest()
        registros.append(
            {
                "organizacao_id": linha["organizacao_id"],
                "versao": versao,
                "status": "publicada" if linha["atual"] else "revogada",
                "documento_referencia": "/privacidade",
                "sha256": digest,
                "criado_em": linha["referencia_em"],
                "publicado_em": linha["referencia_em"],
                "vigencia_em": linha["referencia_em"],
                "criado_por": "migration:i20s5u1p842",
                "aprovado_por": "migration:i20s5u1p842",
                "motivo_alteracao": "Migração da versão legada sem alteração de consentimentos.",
                "requer_novo_consentimento": False,
            }
        )
    if registros:
        op.bulk_insert(tabela, registros)

    op.create_foreign_key(
        "fk_leads_consentimento_politica",
        "leads",
        "politicas_privacidade",
        ["organizacao_id", "consentimento_versao_termo"],
        ["organizacao_id", "versao"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )

    op.execute(
        """
        CREATE FUNCTION proteger_politica_privacidade_publicada()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.status IN ('publicada', 'revogada') THEN
                RAISE EXCEPTION 'politica de privacidade publicada e imutavel';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'revogada' THEN
                RAISE EXCEPTION 'politica de privacidade revogada e imutavel';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.status = 'publicada' THEN
                IF NEW.status <> 'revogada'
                   OR (to_jsonb(NEW) - 'status') <> (to_jsonb(OLD) - 'status') THEN
                    RAISE EXCEPTION 'politica de privacidade publicada e imutavel';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_proteger_politica_privacidade
        BEFORE UPDATE OR DELETE ON politicas_privacidade
        FOR EACH ROW EXECUTE FUNCTION proteger_politica_privacidade_publicada();
        """
    )
    op.execute('ALTER TABLE "politicas_privacidade" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "politicas_privacidade" FORCE ROW LEVEL SECURITY')
    tenant = "organizacao_id = nullif(current_setting('app.organizacao_id', true), '')::bigint"
    superadmin = "current_setting('app.superadmin', true) = 'true'"
    op.execute(
        'CREATE POLICY politicas_privacidade_tenant ON "politicas_privacidade" '
        f"USING ({tenant} OR {superadmin}) WITH CHECK ({tenant} OR {superadmin})"
    )
    op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON "politicas_privacidade" TO inpi_app')
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO inpi_app")


def downgrade() -> None:
    op.drop_constraint("fk_leads_consentimento_politica", "leads", type_="foreignkey")
    op.execute("DROP TRIGGER IF EXISTS trg_proteger_politica_privacidade ON politicas_privacidade")
    op.execute("DROP FUNCTION IF EXISTS proteger_politica_privacidade_publicada()")
    op.drop_table("politicas_privacidade")
