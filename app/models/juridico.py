from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    # Ainda em outros modulos, referenciadas aqui so como forward reference
    # em Mapped["..."] (resolvida em tempo de execucao pelo registry do
    # SQLAlchemy -- este import existe so para o ruff/checadores de tipo).
    from app.models._core import UsuarioOperacoes
    from app.models.carteira import ProcessoMonitorado


class FornecedorJuridico(Base):
    __tablename__ = "fornecedores_juridicos"
    __table_args__ = (UniqueConstraint("organizacao_id", "documento", name="uq_fornecedor_juridico_org_documento"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(180), index=True)
    documento: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContratoJuridico(Base):
    __tablename__ = "contratos_juridicos"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    fornecedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("fornecedores_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="ativo", index=True)
    vigencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    documento_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustoJuridico(Base):
    __tablename__ = "custos_juridicos"
    __table_args__ = (UniqueConstraint("organizacao_id", "idempotency_key", name="uq_custo_juridico_idempotencia"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    centro_custo_id: Mapped[int | None] = mapped_column(
        ForeignKey("centros_custo_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    departamento_id: Mapped[int | None] = mapped_column(
        ForeignKey("departamentos_financeiros.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fornecedor_id: Mapped[int | None] = mapped_column(
        ForeignKey("fornecedores_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contrato_id: Mapped[int | None] = mapped_column(
        ForeignKey("contratos_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processo_id: Mapped[int | None] = mapped_column(
        ForeignKey("processos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    categoria: Mapped[str] = mapped_column(String(30), index=True)
    descricao: Mapped[str] = mapped_column(String(240))
    valor: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    responsaveis: Mapped[list[int]] = mapped_column(JSON, default=list)
    idempotency_key: Mapped[str] = mapped_column(String(120), index=True)
    criado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PrazoJuridico(Base):
    """Prazo operacional vinculado a um processo monitorado."""

    __tablename__ = "prazos_juridicos"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id",
            "processo_monitorado_id",
            "movimentacao_origem_id",
            name="uq_prazo_juridico_movimentacao",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    processo_monitorado_id: Mapped[int] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="CASCADE"), index=True
    )
    movimentacao_origem_id: Mapped[int | None] = mapped_column(
        ForeignKey("movimentacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    responsavel_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    escalonar_para_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    titulo: Mapped[str] = mapped_column(String(180))
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    tipo: Mapped[str] = mapped_column(String(40), default="manifestacao", index=True)
    origem: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    contagem: Mapped[str] = mapped_column(String(20), default="corridos")
    data_base: Mapped[date] = mapped_column(Date, index=True)
    dias_prazo: Mapped[int] = mapped_column(Integer)
    vencimento_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pendente", index=True)
    prioridade: Mapped[str] = mapped_column(String(10), default="media", index=True)
    confirmado: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    confirmado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True
    )

    confirmado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    confirmacao_origem: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confirmacao_observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    antecedencia_dias: Mapped[int] = mapped_column(Integer, default=7)
    escalonar_dias_antes: Mapped[int] = mapped_column(Integer, default=2)
    escalonado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    processo_monitorado: Mapped["ProcessoMonitorado"] = relationship(back_populates="prazos_juridicos", lazy="selectin")
    responsavel: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[responsavel_id], lazy="selectin")
    escalonar_para: Mapped["UsuarioOperacoes | None"] = relationship(foreign_keys=[escalonar_para_id], lazy="selectin")


class PoliticaJuridica(Base):
    """Regras operacionais do módulo jurídico configuradas por organização.

    Achado 5.3 da auditoria (01/09/2026): por padrão nada muda — as duas
    exigências abaixo são opt-in, desligadas por padrão, para não travar
    operações pequenas que hoje concluem prazos sozinhas.
    """

    __tablename__ = "politicas_juridicas"
    __table_args__ = (UniqueConstraint("organizacao_id", name="uq_politica_juridica_organizacao"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    exigir_evidencia_conclusao: Mapped[bool] = mapped_column(Boolean, default=False)
    exigir_segunda_pessoa_critico: Mapped[bool] = mapped_column(Boolean, default=False)
    atualizado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RegraJuridicaVersionada(Base):
    """Histórico de parâmetros jurídicos (prazos legais em dias, datas-marco
    de vigência de norma) hoje fixos em app/api/juridico.py, sem registro de
    quando cada valor passou a valer nem da fonte que o justifica.

    Achado 5.5 da auditoria (02/09/2026), Fase 3: tabela de referência global
    (não é dado por organização — a lei é a mesma para todos os tenants),
    append-only. Cada linha vale no intervalo [vigencia_inicio, vigencia_fim)
    — vigencia_fim nulo significa "vigente até hoje". O código consulta esta
    tabela só em app.api.juridico._historico_regra/_valor_vigente; quando não
    há linha aplicável, o valor padrão hardcoded no código continua valendo
    (tabela vazia = comportamento idêntico ao anterior a esta migration).
    """

    __tablename__ = "regras_juridicas_versionadas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(80), index=True)
    valor: Mapped[dict] = mapped_column(JSON)
    vigencia_inicio: Mapped[date] = mapped_column(Date)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    fonte_legal: Mapped[str] = mapped_column(Text)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MovimentacaoAvaliadaJuridico(Base):
    """Marca que o motor jurídico (``executar_motor_organizacao``) já avaliou
    uma movimentação para uma organização e concluiu que nenhum prazo era
    cabível — sem essa marca, a movimentação ficaria para sempre no pool de
    candidatos da consulta (o filtro de exclusão hoje só olha se já existe
    ``PrazoJuridico``, e despachos não mapeados nunca geram um).

    Achado 5.6 da auditoria (02/09/2026), Fase 4: com organizações que
    acumulam mais de 2000 movimentações não-classificáveis, essas linhas
    ocupavam para sempre as vagas do ``LIMIT 2000`` da consulta, impedindo
    movimentações mais antigas e realmente acionáveis de serem avaliadas —
    um backlog silencioso e crescente. ``Movimentacao`` é global (compartilhada
    entre organizações que monitoram o mesmo processo), então esta marca tem
    que ser por organização, não pode ir na própria ``Movimentacao``.

    Contrapartida assumida conscientemente: se uma norma futura tornar uma
    movimentação hoje não-classificável em classificável, ela não será
    reavaliada automaticamente — precisaria de uma rotina de reprocessamento
    manual (fora do escopo desta correção).
    """

    __tablename__ = "movimentacoes_avaliadas_juridico"
    __table_args__ = (
        UniqueConstraint(
            "organizacao_id",
            "movimentacao_id",
            name="uq_movimentacao_avaliada_juridico",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    movimentacao_id: Mapped[int] = mapped_column(ForeignKey("movimentacoes.id", ondelete="CASCADE"), index=True)
    motivo: Mapped[str] = mapped_column(String(40))
    avaliado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentoEntregaJuridico(Base):
    """Documento anexado como evidência de uma entrega (protocolo, GRU paga,
    comprovante) registrada num prazo jurídico — com hash SHA-256 para
    verificação de integridade, seguindo o mesmo padrão de
    ``DocumentoAtivoPI``/``app.storage``.

    Achado 5.8 da auditoria (02/09/2026), Fase 7: ``registrar_entrega`` só
    aceitava ``protocolo``/``documento`` como texto livre, sem anexo real nem
    hash — qualquer texto passava como "evidência", sem verificação alguma.
    O anexo é opcional aqui (mantém compatibilidade com quem só registra o
    número de protocolo em texto); quando enviado, fica registrado com hash e
    pode ser conferido depois.
    """

    __tablename__ = "documentos_entrega_juridico"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int] = mapped_column(ForeignKey("prazos_juridicos.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(255))
    hash_documento: Mapped[str] = mapped_column(String(64), index=True)
    caminho: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ItemChecklistPrazo(Base):
    """Etapa de conferência de um prazo jurídico (checklist operacional)."""

    __tablename__ = "itens_checklist_prazo"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int] = mapped_column(ForeignKey("prazos_juridicos.id", ondelete="CASCADE"), index=True)
    descricao: Mapped[str] = mapped_column(String(300))
    concluido: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    concluido_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificacaoJuridica(Base):
    __tablename__ = "notificacoes_juridicas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int] = mapped_column(ForeignKey("prazos_juridicos.id", ondelete="CASCADE"), index=True)
    destinatario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios_operacoes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chave: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    titulo: Mapped[str] = mapped_column(String(180))
    mensagem: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="nova", index=True)
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lida_por: Mapped[str | None] = mapped_column(String(254), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    prazo: Mapped[PrazoJuridico] = relationship(lazy="selectin")
    destinatario: Mapped["UsuarioOperacoes | None"] = relationship(lazy="selectin")


class EventoJuridico(Base):
    """Trilha imutável de criação, alteração, entrega e leitura jurídica."""

    __tablename__ = "eventos_juridicos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizacao_id: Mapped[int] = mapped_column(ForeignKey("organizacoes.id", ondelete="CASCADE"), index=True)
    prazo_id: Mapped[int | None] = mapped_column(
        ForeignKey("prazos_juridicos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processo_monitorado_id: Mapped[int] = mapped_column(
        ForeignKey("processos_monitorados.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    ator: Mapped[str] = mapped_column(String(254), index=True)
    descricao: Mapped[str] = mapped_column(String(500))
    detalhes: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
