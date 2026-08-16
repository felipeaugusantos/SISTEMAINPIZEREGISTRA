"""atualiza dados institucionais da organização padrão

Revision ID: zb54v7w5u741
Revises: za43u6v4t630
"""

from collections.abc import Sequence

from alembic import op

revision: str = "zb54v7w5u741"
down_revision: str | None = "za43u6v4t630"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE organizacoes
        SET nome = 'Zé Registra Especialista em Tecnologia, Marcas e Propriedade Intelectual Ltda. - ME',
            documento = '51.622.684/0001-02',
            email_contato = 'ze@zeregistra.com.br',
            telefone_contato = '+55 16 99799-4239',
            branding = branding::jsonb || '{"nome_exibido":"Zé Registra","cnpj":"51.622.684/0001-02","endereco":"R. Gen. Augusto Soares dos Santos, 100 - Parque Industrial Lagoinha, Ribeirão Preto - SP, 14095-240","telefone":"+55 16 99799-4239","email":"ze@zeregistra.com.br","site":"https://www.zeregistra.com.br","atividade":"Agente de propriedade industrial","fundacao":"31/07/2023"}'::jsonb
        WHERE slug = 'ze-registra'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE organizacoes
        SET nome = 'Zé Registra', documento = NULL, email_contato = NULL,
            telefone_contato = NULL,
            branding = '{"nome_exibido":"Zé Registra","cor_primaria":"#006b4f","logo_url":""}'::json
        WHERE slug = 'ze-registra'
        """
    )
