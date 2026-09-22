from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models._core import EmpresaCRM

if TYPE_CHECKING:
    # Ainda em outros modulos, referenciadas aqui so como forward reference
    # em Mapped["..."] (resolvida em tempo de execucao pelo registry do
    # SQLAlchemy -- este import existe so para o ruff/checadores de tipo).
    from app.models._core import Lead, UsuarioOperacoes
    from app.models.carteira import ProcessoMonitorado


class CategoriaFinanceira(Base):
    __tablename__ = "categorias_financeiras"
    __table_args__ = (UniqueConstraint("organizacao_id", "nome", "tipo", name="uq_categoria_financeira_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120), index=True)
    tipo: Mapped[str] = mapped_column(String(12), default="ambos", index=True)
    # Pedido do usuário (22/09/2026): subcategoria -- None é categoria raiz.
    # Profundidade limitada a 1 nível, validado em app/api/financeiro.py.
    categoria_pai_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FormaPagamentoFinanceira(Base):
    __tablename__ = "formas_pagamento_financeiras"
    __table_args__ = (UniqueConstraint("organizacao_id", "nome", name="uq_forma_pagamento_financeira_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120), index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="outro", index=True)
    permite_parcelamento: Mapped[bool] = mapped_column(Boolean, default=False)
    maximo_parcelas: Mapped[int] = mapped_column(Integer, default=1)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DepartamentoFinanceiro(Base):
    __tablename__ = "departamentos_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_departamento_financeiro_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(150))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CentroCustoFinanceiro(Base):
    __tablename__ = "centros_custo_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_centro_custo_financeiro_org"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    departamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("departamentos_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    codigo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(150))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanoContas(Base):
    """Achado FASE7-13 da auditoria (04/09/2026): plano de contas gerencial --
    ver app/plano_contas.py para o seed padrão e a lógica de DRE. Hierarquia
    simples (conta_pai_id) e um grupo_dre fixo (app.plano_contas.GRUPOS_DRE)
    que decide onde a conta entra no demonstrativo de resultado."""

    __tablename__ = "plano_contas"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_plano_contas_codigo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    conta_pai_id: Mapped[int | None] = mapped_column(
        ForeignKey("plano_contas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    codigo: Mapped[str] = mapped_column(String(20), index=True)
    nome: Mapped[str] = mapped_column(String(150))
    natureza: Mapped[str] = mapped_column(String(10))
    grupo_dre: Mapped[str] = mapped_column(String(30), index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtratoBancario(Base):
    """Achado FASE7-3 da auditoria (04/09/2026): um extrato OFX importado --
    ver app/ofx.py para o parser e app/api/conciliacao.py para o
    matching automático contra ParcelaFinanceira."""

    __tablename__ = "extratos_bancarios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    total_transacoes: Mapped[int] = mapped_column(Integer, default=0)
    importado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TransacaoBancaria(Base):
    """Uma linha de extrato bancário (crédito ou débito). status="conciliada"
    quando casada com uma ParcelaFinanceira (automático, se houver
    exatamente uma correspondência de valor+data, ou manual pelo
    operador). fitid é o identificador único do banco para a transação --
    reimportar o mesmo extrato nunca duplica (UniqueConstraint)."""

    __tablename__ = "transacoes_bancarias"
    __table_args__ = (UniqueConstraint("organizacao_id", "fitid", name="uq_transacao_bancaria_fitid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    extrato_id: Mapped[int] = mapped_column(ForeignKey("extratos_bancarios.id", ondelete="CASCADE"), index=True)
    fitid: Mapped[str] = mapped_column(String(120), index=True)
    data: Mapped[date] = mapped_column(Date, index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tipo: Mapped[str] = mapped_column(String(10), index=True)
    descricao: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    parcela_id: Mapped[int | None] = mapped_column(
        ForeignKey("parcelas_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conciliado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conciliado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NotaFiscalServico(Base):
    """Achado FASE7-6 da auditoria (04/09/2026): NFS-e emitida (ou tentada)
    para um lançamento de receita -- ver app/nfse.py para a interface de
    adaptador. Nunca emitida automaticamente: sempre uma ação explícita do
    operador para um lançamento específico."""

    __tablename__ = "notas_fiscais_servico"
    __table_args__ = (
        # Achado critico da auditoria financeira (15/09/2026): sem isto, duplo
        # clique/retry apos timeout emitia duas NFS-e reais para o mesmo
        # lancamento. Indice unico parcial -- so bloqueia quando ja existe uma
        # nota "emitida"; reemissao apos "erro"/"cancelada" continua permitida
        # (nao expressavel como UniqueConstraint comum, mesmo padrao de
        # PoliticaPrivacidade.uq_politica_privacidade_publicada_org).
        Index(
            "ux_notas_fiscais_servico_lancamento_emitida",
            "lancamento_id",
            unique=True,
            postgresql_where=text("status = 'emitida'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True
    )
    adaptador: Mapped[str] = mapped_column(String(30))
    numero: Mapped[str | None] = mapped_column(String(60), nullable=True)
    codigo_verificacao: Mapped[str | None] = mapped_column(String(60), nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="emitida", index=True)
    erro_detalhe: Mapped[str | None] = mapped_column(Text, nullable=True)
    emitida_por: Mapped[str] = mapped_column(String(254))
    emitida_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    cancelada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelada_por: Mapped[str | None] = mapped_column(String(254), nullable=True)


class ApontamentoHoras(Base):
    """Registro manual de horas trabalhadas -- Fase A da evolucao do CRM
    (05/09/2026): o sistema nao tinha nenhum controle de horas fatuaveis,
    so custo monetario (CustoJuridico). Vinculado a um lead (oportunidade
    comercial) e/ou a um processo monitorado (caso juridico) -- pelo menos
    um dos dois precisa estar preenchido. Sem valor/hora: o calculo de
    faturamento a partir de horas fica para uma fase futura, se necessario.
    """

    __tablename__ = "apontamentos_horas"
    __table_args__ = (
        CheckConstraint(
            "processo_monitorado_id IS NOT NULL OR lead_id IS NOT NULL",
            name="ck_apontamento_horas_vinculo",
        ),
        CheckConstraint("horas > 0", name="ck_apontamento_horas_positivas"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios_operacoes.id", ondelete="RESTRICT"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_monitorado_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="SET NULL"), nullable=True, index=True
    )
    data: Mapped[date] = mapped_column(Date, index=True)
    horas: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    descricao: Mapped[str] = mapped_column(String(500))
    faturavel: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    usuario: Mapped["UsuarioOperacoes"] = relationship(lazy="selectin")
    lead: Mapped["Lead | None"] = relationship(lazy="selectin")
    processo_monitorado: Mapped["ProcessoMonitorado | None"] = relationship(lazy="selectin")


class WebhookFinanceiro(Base):
    __tablename__ = "webhooks_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "referencia", name="uq_webhook_financeiro_org_referencia"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    referencia: Mapped[str] = mapped_column(String(150), index=True)
    evento: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="recebido", index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=1)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServicoFinanceiro(Base):
    __tablename__ = "servicos_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "codigo", name="uq_servico_financeiro_codigo"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(50), index=True)
    nome: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    recorrente: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContratacaoServico(Base):
    __tablename__ = "contratacoes_servicos"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    # Nulo quando a contratacao vem do aceite de uma proposta (valor
    # assinado, sem item de catalogo vinculado) -- Fase 4 do plano
    # proposta-financeiro (03/09/2026).
    servico_id: Mapped[int | None] = mapped_column(
        ForeignKey("servicos_financeiros.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposta_id: Mapped[int | None] = mapped_column(
        ForeignKey("propostas_comerciais.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    lancamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="contratada", index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LancamentoFinanceiro(Base):
    __tablename__ = "lancamentos_financeiros"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(
        ForeignKey("empresas_crm.id", ondelete="SET NULL"), nullable=True, index=True
    )

    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposta_id: Mapped[int | None] = mapped_column(
        ForeignKey("propostas_comerciais.id", ondelete="SET NULL"), nullable=True, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True, index=True)
    categoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Achado FASE7-13 da auditoria (04/09/2026): classificação contábil
    # gerencial (ver app/plano_contas.py) -- opcional para não quebrar
    # lançamentos existentes; sem ela, entra como "sem_classificacao" no DRE.
    conta_contabil_id: Mapped[int | None] = mapped_column(
        ForeignKey("plano_contas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    forma_pagamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("formas_pagamento_financeiras.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tipo: Mapped[str] = mapped_column(String(12), index=True)
    descricao: Mapped[str] = mapped_column(String(240), index=True)
    documento: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="aberto", index=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_por: Mapped[str] = mapped_column(String(254))
    cancelado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    cancelamento_motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    empresa_registro: Mapped[EmpresaCRM | None] = relationship(
        back_populates="lancamentos_financeiros", lazy="selectin"
    )
    categoria: Mapped[CategoriaFinanceira | None] = relationship(lazy="selectin")
    conta_contabil: Mapped["PlanoContas | None"] = relationship(lazy="selectin")
    forma_pagamento: Mapped[FormaPagamentoFinanceira | None] = relationship(lazy="selectin")
    parcelas: Mapped[list["ParcelaFinanceira"]] = relationship(
        back_populates="lancamento",
        cascade="all, delete-orphan",
        order_by="ParcelaFinanceira.numero",
    )


class ParcelaFinanceira(Base):
    __tablename__ = "parcelas_financeiras"
    __table_args__ = (UniqueConstraint("lancamento_id", "numero", name="uq_parcela_financeira_numero"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    vencimento: Mapped[date] = mapped_column(Date, index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valor_pago: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="aberta", index=True)
    pago_em: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    forma_pagamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("formas_pagamento_financeiras.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    forma_pagamento: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observacoes_baixa: Mapped[str | None] = mapped_column(Text, nullable=True)
    lancamento: Mapped[LancamentoFinanceiro] = relationship(back_populates="parcelas")


class ComissaoFinanceira(Base):
    """Achado FASE7-7 da auditoria (04/09/2026): comissão de operador sobre
    receita -- gerada automaticamente quando uma parcela de um lançamento
    "receber" vinculado a um Lead com responsável comissionado é baixada
    (ver app/api/financeiro.py::_gerar_comissao_se_aplicavel). Uma linha por
    parcela baixada, nunca duplicada (unique em parcela_id)."""

    __tablename__ = "comissoes_financeiras"
    __table_args__ = (UniqueConstraint("parcela_id", name="uq_comissao_financeira_parcela"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="CASCADE"), index=True
    )
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True
    )
    parcela_id: Mapped[int] = mapped_column(ForeignKey("parcelas_financeiras.id", ondelete="CASCADE"), index=True)
    valor_base: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    percentual: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    valor_comissao: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    pago_em: Mapped[date | None] = mapped_column(Date, nullable=True)
    pago_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    usuario: Mapped["UsuarioOperacoes"] = relationship(lazy="selectin")


class HistoricoFinanceiro(Base):
    __tablename__ = "historicos_financeiros"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos_financeiros.id", ondelete="CASCADE"), index=True)
    parcela_id: Mapped[int | None] = mapped_column(
        ForeignKey("parcelas_financeiras.id", ondelete="SET NULL"), nullable=True, index=True
    )
    acao: Mapped[str] = mapped_column(String(30), index=True)
    ator: Mapped[str] = mapped_column(String(254), index=True)
    descricao: Mapped[str] = mapped_column(String(500))
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RetribuicaoInpi(Base):
    """Tabela de retribuições do INPI (serviços de marca) — referência global.

    Os valores oficiais são nacionais (iguais para todos os tenants); por isso a
    tabela não é multi-tenant. `confirmado` indica que o valor foi conferido
    contra a tabela oficial vigente (guarda contra usar valor de referência).
    """

    __tablename__ = "retribuicoes_inpi"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    servico: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    descricao: Mapped[str] = mapped_column(String(200))
    grupo: Mapped[str] = mapped_column(String(20), default="marca", index=True)
    codigo: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    valor_normal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    valor_reduzido: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fase_sugerida: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    confirmado: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReciboFinanceiro(Base):
    __tablename__ = "recibos_financeiros"
    __table_args__ = (UniqueConstraint("organizacao_id", "parcela_id", name="uq_recibo_financeiro_parcela"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    parcela_id: Mapped[int] = mapped_column(ForeignKey("parcelas_financeiras.id", ondelete="CASCADE"), index=True)
    numero: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    emitido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dados: Mapped[dict] = mapped_column(JSON, default=dict)


class RenovacaoFinanceira(Base):
    __tablename__ = "renovacoes_financeiras"
    __table_args__ = (
        UniqueConstraint("organizacao_id", "processo_id", "tipo", "referencia", name="uq_renovacao_financeira"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="renovacao")
    referencia: Mapped[str] = mapped_column(String(40))
    vencimento: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
