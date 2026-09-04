"""adiciona indices declarados no modelo mas ausentes no banco (achado FASE6-2)

Revision ID: wp43q1s5d064
Revises: wo32p0r4c953

Achado FASE6-2 da auditoria (04/09/2026, alembic check): estas colunas
já são declaradas com index=True em app/models.py há uma ou mais fases,
mas a migração que criou a tabela/coluna nunca incluiu o
op.create_index correspondente -- ou seja, essas colunas rodaram em
produção sem índice até agora. Todas são simples CREATE INDEX (sem
alteração de dado, sem tocar em constraint), agrupadas numa única
migração por serem a mesma categoria de correção -- cada linha foi
conferida contra o índice que o modelo já pedia, não é um autogenerate
aplicado às cegas.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "wp43q1s5d064"
down_revision: str | None = "wo32p0r4c953"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (nome_do_indice, tabela, [colunas])
INDICES: list[tuple[str, str, list[str]]] = [
    ("ix_arquivos_clientes_portal_organizacao_id", "arquivos_clientes_portal", ["organizacao_id"]),
    ("ix_assinaturas_documentos_lead_assinado_em", "assinaturas_documentos_lead", ["assinado_em"]),
    ("ix_assinaturas_documentos_lead_cliente_id", "assinaturas_documentos_lead", ["cliente_id"]),
    ("ix_assinaturas_documentos_lead_hash_documento", "assinaturas_documentos_lead", ["hash_documento"]),
    ("ix_assinaturas_documentos_lead_organizacao_id", "assinaturas_documentos_lead", ["organizacao_id"]),
    ("ix_ativos_pi_criado_em", "ativos_pi", ["criado_em"]),
    ("ix_centros_custo_financeiros_ativo", "centros_custo_financeiros", ["ativo"]),
    ("ix_centros_custo_financeiros_codigo", "centros_custo_financeiros", ["codigo"]),
    ("ix_centros_custo_financeiros_departamento_id", "centros_custo_financeiros", ["departamento_id"]),
    ("ix_centros_custo_financeiros_organizacao_id", "centros_custo_financeiros", ["organizacao_id"]),
    ("ix_contratacoes_servicos_organizacao_id", "contratacoes_servicos", ["organizacao_id"]),
    ("ix_contratacoes_servicos_processo_id", "contratacoes_servicos", ["processo_id"]),
    ("ix_contratacoes_servicos_servico_id", "contratacoes_servicos", ["servico_id"]),
    ("ix_contratacoes_servicos_status", "contratacoes_servicos", ["status"]),
    ("ix_contratos_juridicos_fornecedor_id", "contratos_juridicos", ["fornecedor_id"]),
    ("ix_contratos_juridicos_organizacao_id", "contratos_juridicos", ["organizacao_id"]),
    ("ix_contratos_juridicos_processo_id", "contratos_juridicos", ["processo_id"]),
    ("ix_contratos_juridicos_status", "contratos_juridicos", ["status"]),
    ("ix_contratos_juridicos_vigencia_fim", "contratos_juridicos", ["vigencia_fim"]),
    ("ix_custos_juridicos_categoria", "custos_juridicos", ["categoria"]),
    ("ix_custos_juridicos_centro_custo_id", "custos_juridicos", ["centro_custo_id"]),
    ("ix_custos_juridicos_contrato_id", "custos_juridicos", ["contrato_id"]),
    ("ix_custos_juridicos_criado_em", "custos_juridicos", ["criado_em"]),
    ("ix_custos_juridicos_departamento_id", "custos_juridicos", ["departamento_id"]),
    ("ix_custos_juridicos_fornecedor_id", "custos_juridicos", ["fornecedor_id"]),
    ("ix_custos_juridicos_idempotency_key", "custos_juridicos", ["idempotency_key"]),
    ("ix_custos_juridicos_organizacao_id", "custos_juridicos", ["organizacao_id"]),
    ("ix_custos_juridicos_processo_id", "custos_juridicos", ["processo_id"]),
    ("ix_departamentos_financeiros_ativo", "departamentos_financeiros", ["ativo"]),
    ("ix_departamentos_financeiros_codigo", "departamentos_financeiros", ["codigo"]),
    ("ix_departamentos_financeiros_organizacao_id", "departamentos_financeiros", ["organizacao_id"]),
    ("ix_documentos_ativos_pi_criado_em", "documentos_ativos_pi", ["criado_em"]),
    ("ix_fornecedores_juridicos_ativo", "fornecedores_juridicos", ["ativo"]),
    ("ix_fornecedores_juridicos_documento", "fornecedores_juridicos", ["documento"]),
    ("ix_fornecedores_juridicos_nome", "fornecedores_juridicos", ["nome"]),
    ("ix_fornecedores_juridicos_organizacao_id", "fornecedores_juridicos", ["organizacao_id"]),
    ("ix_mensagens_clientes_portal_criado_em", "mensagens_clientes_portal", ["criado_em"]),
    ("ix_mensagens_clientes_portal_organizacao_id", "mensagens_clientes_portal", ["organizacao_id"]),
    ("ix_notificacoes_clientes_portal_criado_em", "notificacoes_clientes_portal", ["criado_em"]),
    ("ix_preferencias_vigilancia_ativo", "preferencias_vigilancia", ["ativo"]),
    ("ix_preferencias_vigilancia_organizacao_id", "preferencias_vigilancia", ["organizacao_id"]),
    ("ix_projetos_busca_marca_criado_em", "projetos_busca_marca", ["criado_em"]),
    ("ix_propostas_comerciais_protocolo_comprovante_id", "propostas_comerciais", ["protocolo_comprovante_id"]),
    ("ix_propostas_comerciais_responsavel_protocolo_id", "propostas_comerciais", ["responsavel_protocolo_id"]),
    ("ix_prospect_enriquecimentos_criado_em", "prospect_enriquecimentos", ["criado_em"]),
    ("ix_recibos_financeiros_organizacao_id", "recibos_financeiros", ["organizacao_id"]),
    ("ix_renovacoes_financeiras_organizacao_id", "renovacoes_financeiras", ["organizacao_id"]),
    ("ix_renovacoes_financeiras_processo_id", "renovacoes_financeiras", ["processo_id"]),
    ("ix_renovacoes_financeiras_status", "renovacoes_financeiras", ["status"]),
    ("ix_servicos_financeiros_ativo", "servicos_financeiros", ["ativo"]),
    ("ix_servicos_financeiros_organizacao_id", "servicos_financeiros", ["organizacao_id"]),
    ("ix_versoes_documentos_lead_hash_documento", "versoes_documentos_lead", ["hash_documento"]),
    ("ix_versoes_documentos_lead_organizacao_id", "versoes_documentos_lead", ["organizacao_id"]),
    ("ix_webhooks_financeiros_evento", "webhooks_financeiros", ["evento"]),
    ("ix_webhooks_financeiros_organizacao_id", "webhooks_financeiros", ["organizacao_id"]),
    ("ix_webhooks_financeiros_referencia", "webhooks_financeiros", ["referencia"]),
    ("ix_webhooks_financeiros_status", "webhooks_financeiros", ["status"]),
]


def upgrade() -> None:
    for nome, tabela, colunas in INDICES:
        op.create_index(nome, tabela, colunas)


def downgrade() -> None:
    for nome, tabela, _colunas in reversed(INDICES):
        op.drop_index(nome, table_name=tabela)
