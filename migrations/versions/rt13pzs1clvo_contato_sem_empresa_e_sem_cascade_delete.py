"""Contato sem empresa obrigatoria e sem cascade delete (achados CRM-9/CRM-10).

Achados da auditoria de CRM/financeiro (04/09/2026):
- CRM-9: ``contatos.empresa_id`` era NOT NULL -- impossivel cadastrar um
  contato de cliente pessoa fisica, sem empresa vinculada.
- CRM-10: a FK usava ``ON DELETE CASCADE`` -- apagar uma empresa apagava em
  cascata todos os seus contatos e o historico junto. Nao ha hoje nenhum
  endpoint que apague uma empresa (o risco era estrutural, nao ativamente
  explorado), mas a constraint ficava pronta para causar perda de dado
  assim que um endpoint de exclusao fosse adicionado sem essa checagem.

Downgrade: reverte para NOT NULL + CASCADE -- so e seguro se nenhum
contato tiver sido cadastrado sem empresa nesse meio tempo (o downgrade
falha alto se houver, propositalmente, em vez de apagar dado ou inventar
uma empresa).

Nota: existe tambem uma FK composta `fk_contato_empresa_tenant`
(organizacao_id, empresa_id) -> empresas_crm(organizacao_id, id), usada
como isolamento extra de tenant (alem do RLS). Nao precisa de alteracao --
o Postgres usa MATCH SIMPLE por padrao em FKs compostas, entao uma linha
com empresa_id NULL satisfaz essa constraint automaticamente, sem
verificar o outro lado.
"""

from alembic import op

revision = "rt13pzs1clvo"
down_revision = "qs02oyr0bkun"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("contatos_empresa_id_fkey", "contatos", type_="foreignkey")
    op.alter_column("contatos", "empresa_id", nullable=True)
    op.create_foreign_key(
        "contatos_empresa_id_fkey",
        "contatos",
        "empresas_crm",
        ["empresa_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM contatos WHERE empresa_id IS NULL) THEN "
        "RAISE EXCEPTION "
        "'Existem contatos sem empresa -- nao e seguro reverter para NOT NULL sem decidir o que fazer com eles.'; "
        "END IF; END $$;"
    )
    op.drop_constraint("contatos_empresa_id_fkey", "contatos", type_="foreignkey")
    op.alter_column("contatos", "empresa_id", nullable=False)
    op.create_foreign_key(
        "contatos_empresa_id_fkey",
        "contatos",
        "empresas_crm",
        ["empresa_id"],
        ["id"],
        ondelete="CASCADE",
    )
