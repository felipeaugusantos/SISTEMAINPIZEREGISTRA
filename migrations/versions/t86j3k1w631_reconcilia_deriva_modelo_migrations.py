"""reconcilia deriva entre models.py e migrations (Fase 2 da reanalise)

Revision ID: t86j3k1w631
Revises: s75i2j0v520

`alembic check` acusava dezenas de diferencas entre app/models.py e o
historico real de migrations, non-blocking no CI de proposito ate essa
reconciliacao acontecer (ver docs/fase*, comentario no ci.yml). A maior
parte das diferencas era so indice/constraint que existe no banco mas
sumiu do modelo em algum refactor (ix_prospects_org_status, o FK composto
fk_leads_consentimento_politica, os dois indices unicos parciais de
politicas_privacidade/pre_cadastros_processo, etc.) -- resolvida so
devolvendo a declaracao em app/models.py, sem tocar o banco. Esta migration
cobre as tres diferencas que exigem alterar o banco de verdade:

1. Colunas de timestamp (criado_em/atualizado_em/assinado_em) que o modelo
   ja declara como Mapped[datetime] (nao-opcional, ou seja NOT NULL) mas
   cuja coluna real no banco ainda estava nullable=True -- nunca migrado
   pra NOT NULL quando o tipo do modelo foi definido. Confirmado em
   producao: zero linhas com valor nulo nessas colunas hoje (todas as
   tabelas afetadas sao de funcionalidades do portal do cliente/vigilancia,
   ainda sem uso real -- 0 linhas em todas), entao SET NOT NULL eh seguro
   e imediato, sem necessidade de backfill real.
2. feature_flags_eventos.grupo ja tem `index=True` no modelo (usado pelo
   monitoramento por grupo, Fase 5) mas o indice nunca foi criado no banco.
3. Onze colunas unicas (ex.: versoes_sistema.versao, feature_flags.codigo)
   estao no banco como UniqueConstraint nomeada + indice comum separados
   (padrao antigo do SQLAlchemy pra `unique=True, index=True`), mas o
   SQLAlchemy 2.0 atual gera um unico indice unico pra essa mesma
   declaracao -- drift introduzido por uma troca de versao da lib, nao por
   uma mudanca de codigo. Confirmado em producao: todas as tabelas
   afetadas tem no maximo 196 linhas (prospects), risco de lock
   desprezivel. Convertido pro formato atual em vez de forcar o modelo a
   imitar o formato antigo, pra nao repetir esse mesmo drift no proximo
   upgrade de SQLAlchemy.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t86j3k1w631"
down_revision: str | None = "s75i2j0v520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUNAS_NOT_NULL = (
    ("arquivos_clientes_portal", "criado_em"),
    ("assinaturas_documentos_lead", "assinado_em"),
    ("clientes_portal", "criado_em"),
    ("clientes_portal", "atualizado_em"),
    ("colidencias_vigilancia", "criado_em"),
    ("contratacoes_servicos", "criado_em"),
    ("mensagens_clientes_portal", "criado_em"),
    ("notificacoes_clientes_portal", "criado_em"),
    ("preferencias_vigilancia", "atualizado_em"),
    ("servicos_financeiros", "criado_em"),
    ("sessoes_clientes_portal", "criado_em"),
    ("versoes_documentos_lead", "criado_em"),
)

# (tabela, coluna, nome_da_constraint_antiga, nome_do_indice)
COLUNAS_UNICAS_A_CONSOLIDAR = (
    ("contratacoes_servicos", "proposta_id", "uq_contratacao_servico_proposta", "ix_contratacoes_servicos_proposta_id"),
    ("embeddings_lead", "lead_id", "uq_embeddings_lead_lead_id", "ix_embeddings_lead_lead_id"),
    ("feature_flags", "codigo", "feature_flags_codigo_key", "ix_feature_flags_codigo"),
    ("modelos_ranking_busca", "versao", "modelos_ranking_busca_versao_key", "ix_modelos_ranking_busca_versao"),
    ("processos_heartbeat", "processo", "processos_heartbeat_processo_key", "ix_processos_heartbeat_processo"),
    ("prospects", "lead_id", "uq_prospects_lead_id", "ix_prospects_lead_id"),
    ("recibos_financeiros", "numero", "recibos_financeiros_numero_key", "ix_recibos_financeiros_numero"),
    (
        "recuperacoes_clientes_portal",
        "token_hash",
        "recuperacoes_clientes_portal_token_hash_key",
        "ix_recuperacoes_clientes_portal_token_hash",
    ),
    (
        "sessoes_clientes_portal",
        "token_hash",
        "sessoes_clientes_portal_token_hash_key",
        "ix_sessoes_clientes_portal_token_hash",
    ),
    (
        "solicitacoes_anonimizacao_lead",
        "token_hash",
        "uq_solicitacao_anonimizacao_token",
        "ix_solicitacoes_anonimizacao_lead_token_hash",
    ),
    ("versoes_sistema", "versao", "uq_versoes_sistema_versao", "ix_versoes_sistema_versao"),
)


def upgrade() -> None:
    for tabela, coluna in COLUNAS_NOT_NULL:
        # Defensivo: as tabelas afetadas estao vazias em producao hoje, mas
        # o backfill custa nada e protege qualquer ambiente com dado legado.
        op.execute(f'UPDATE "{tabela}" SET "{coluna}" = now() WHERE "{coluna}" IS NULL')
        op.alter_column(tabela, coluna, nullable=False)
    op.create_index("ix_feature_flags_eventos_grupo", "feature_flags_eventos", ["grupo"])
    for tabela, coluna, constraint, indice in COLUNAS_UNICAS_A_CONSOLIDAR:
        # IF EXISTS dos dois lados: producao acumulou historico de migrations
        # nem sempre identico a uma base recriada do zero (achado real desta
        # migration -- prospects.ix_prospects_lead_id existe em producao mas
        # nao nasce de uma replay limpa do historico), entao o estado de
        # partida pode variar por ambiente; o estado final e' sempre o mesmo.
        op.execute(f'ALTER TABLE "{tabela}" DROP CONSTRAINT IF EXISTS "{constraint}"')
        op.execute(f'DROP INDEX IF EXISTS "{indice}"')
        op.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{indice}" ON "{tabela}" ("{coluna}")')


def downgrade() -> None:
    for tabela, coluna, constraint, indice in COLUNAS_UNICAS_A_CONSOLIDAR:
        op.execute(f'DROP INDEX IF EXISTS "{indice}"')
        op.execute(f'CREATE INDEX IF NOT EXISTS "{indice}" ON "{tabela}" ("{coluna}")')
        op.execute(f'ALTER TABLE "{tabela}" ADD CONSTRAINT "{constraint}" UNIQUE ("{coluna}")')
    op.drop_index("ix_feature_flags_eventos_grupo", table_name="feature_flags_eventos")
    for tabela, coluna in COLUNAS_NOT_NULL:
        op.alter_column(tabela, coluna, nullable=True)
