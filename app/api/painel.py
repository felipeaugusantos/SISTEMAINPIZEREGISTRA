"""Painel executivo e central de notificações da tela de visão geral.

Consolida, em uma única tela, os indicadores que hoje estão espalhados pelas
rotinas (financeiro, jurídico, risco, comercial, aprendizado) e reúne as
notificações pendentes do sistema. Cada bloco respeita a permissão do módulo:
o CEO — que tem todas — vê tudo; um operador vê apenas o que lhe cabe.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.juridico import FUSO_BRASIL, STATUS_ATIVOS
from app.auth import UsuarioAutenticado, exigir_permissao
from app.database import get_session
from app.models import (
    AlertaSistema,
    AvaliacaoRiscoMarca,
    LancamentoFinanceiro,
    Lead,
    ModeloRegistrabilidade,
    NotificacaoJuridica,
    ParcelaFinanceira,
    PesquisaMarca,
    PrazoJuridico,
    PropostaComercial,
    StatusLead,
)
from app.trademarks.model_status import StatusModelo

router = APIRouter(prefix="/v1/admin", tags=["painel executivo"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
DashboardDep = Annotated[UsuarioAutenticado, Depends(exigir_permissao("dashboard.view"))]

# Alerta do sistema (código) -> tela onde o CEO resolve a pendência.
_DESTINO_ALERTA = {
    "RETENCAO_PENDENTE": "/admin/confiabilidade",
    "TRIAL_EXPIRADO": "/admin/saas",
    "PREVISOES_REPROCESSADAS": "/admin/aprendizado",
    "AGENTES_REPROCESSADOS": "/admin/aprendizado",
    "MODELO_APRENDIZADO_ATIVADO": "/admin/aprendizado",
    "MODELO_APRENDIZADO_AGUARDANDO_REVISOES": "/admin/aprendizado",
    "MODELO_APRENDIZADO_BLOQUEADO": "/admin/aprendizado",
    # Compatibilidade com alertas persistidos antes da nomenclatura formal da Fase 5.
    "MODELO_APRENDIZADO_REPROVADO": "/admin/aprendizado",
    "NOVA_PESQUISA": "/admin/pesquisas",
}
# Códigos de alerta visíveis à equipe comercial (leads.view), não apenas a quem tem
# production.manage — para não misturar ruído de produção na central de quem atende.
# Achado P1 da auditoria de Leads (03/09/2026): só NOVA_PESQUISA chegava a
# quem tinha apenas leads.view (sem production.manage) -- os alertas que o
# worker gera para automações de Leads/CRM (cadência, reengajamento,
# retenção de dados) ficavam invisíveis para esse perfil.
_CODIGOS_ALERTA_COMERCIAL = frozenset(
    {
        "NOVA_PESQUISA",
        "RETENCAO_PENDENTE",
        "REENGAJAMENTO_CRM_EXECUTADO",
        "CADENCIA_EMAILS_PROCESSADOS",
        "CADENCIA_PAUSADA_POR_RESPOSTA",
    }
)


async def _bloco_financeiro(session: AsyncSession, organizacao_id: int) -> dict:
    hoje = date.today()
    restante = ParcelaFinanceira.valor - func.coalesce(ParcelaFinanceira.valor_pago, 0)
    em_aberto = ParcelaFinanceira.status != "paga"
    linha = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(em_aberto, LancamentoFinanceiro.tipo == "receber"),
                                restante,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (and_(em_aberto, LancamentoFinanceiro.tipo == "pagar"), restante),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(em_aberto, ParcelaFinanceira.vencimento < hoje),
                                restante,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count().filter(and_(em_aberto, ParcelaFinanceira.vencimento < hoje)),
            )
            .select_from(ParcelaFinanceira)
            .join(
                LancamentoFinanceiro,
                LancamentoFinanceiro.id == ParcelaFinanceira.lancamento_id,
            )
            .where(
                ParcelaFinanceira.organizacao_id == organizacao_id,
                LancamentoFinanceiro.status != "cancelado",
            )
        )
    ).one()
    return {
        "receber_aberto": float(linha[0]),
        "pagar_aberto": float(linha[1]),
        "vencido": float(linha[2]),
        "parcelas_vencidas": int(linha[3]),
        "url": "/admin/financeiro",
    }


async def _bloco_juridico(session: AsyncSession, organizacao_id: int) -> dict:
    # Achado D1 da auditoria de 06/09/2026: "vence_hoje"/"proximos_7_dias" usavam
    # func.date(vencimento_em) == hoje, truncando o timestamptz no timezone da
    # sessao Postgres (UTC) em vez do calendario civil de Brasilia -- mesma
    # classe de bug ja corrigida em app.api.juridico (achado JUR-1, 04/09/2026),
    # mas essa correcao nunca chegou a esta copia duplicada da logica. Segue
    # aqui o mesmo padrao ja comprovado la: compara o timestamptz contra
    # limites absolutos (UTC) calculados a partir do dia civil de Brasilia, em
    # vez de truncar a coluna dentro do SQL.
    ativo = PrazoJuridico.status.in_(STATUS_ATIVOS)
    agora = datetime.now(UTC)
    hoje_brasil = agora.astimezone(FUSO_BRASIL).date()
    hoje_inicio = datetime.combine(hoje_brasil, time.min, FUSO_BRASIL).astimezone(UTC)
    hoje_fim = datetime.combine(hoje_brasil, time.max, FUSO_BRASIL).astimezone(UTC)
    sete_dias_fim = datetime.combine(hoje_brasil + timedelta(days=7), time.max, FUSO_BRASIL).astimezone(UTC)
    linha = (
        await session.execute(
            select(
                func.count().filter(and_(ativo, PrazoJuridico.vencimento_em < hoje_inicio)),
                func.count().filter(
                    and_(ativo, PrazoJuridico.vencimento_em >= hoje_inicio, PrazoJuridico.vencimento_em <= hoje_fim)
                ),
                func.count().filter(
                    and_(ativo, PrazoJuridico.vencimento_em > hoje_fim, PrazoJuridico.vencimento_em <= sete_dias_fim)
                ),
                func.count().filter(and_(ativo, PrazoJuridico.confirmado.is_(False))),
            ).where(PrazoJuridico.organizacao_id == organizacao_id)
        )
    ).one()
    return {
        "vencidos": int(linha[0]),
        "vence_hoje": int(linha[1]),
        "proximos_7_dias": int(linha[2]),
        "aguardando_confirmacao": int(linha[3]),
        "url": "/admin/operacao-juridica",
    }


async def _bloco_comercial(session: AsyncSession, organizacao_id: int) -> dict:
    linha = (
        await session.execute(
            select(
                select(func.count()).select_from(Lead).where(Lead.organizacao_id == organizacao_id).scalar_subquery(),
                select(func.count())
                .select_from(Lead)
                .where(Lead.organizacao_id == organizacao_id, Lead.status == StatusLead.NOVO)
                .scalar_subquery(),
                select(func.count())
                .select_from(PesquisaMarca)
                .where(PesquisaMarca.organizacao_id == organizacao_id)
                .scalar_subquery(),
            )
        )
    ).one()
    return {
        "leads_total": int(linha[0]),
        "leads_novos": int(linha[1]),
        "pesquisas_total": int(linha[2]),
        "url": "/admin/pesquisas",
    }


async def _bloco_risco(session: AsyncSession, organizacao_id: int) -> dict:
    linha = (
        await session.execute(
            select(
                func.count().filter(AvaliacaoRiscoMarca.nivel.in_(["alto", "critico"])),
                func.count().filter(AvaliacaoRiscoMarca.nivel_humano.is_(None)),
            )
            .select_from(AvaliacaoRiscoMarca)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(PesquisaMarca.organizacao_id == organizacao_id)
        )
    ).one()
    return {
        "elevados": int(linha[0]),
        "pendentes_revisao": int(linha[1]),
        "url": "/admin/pesquisas",
    }


async def _bloco_aprendizado(session: AsyncSession) -> dict:
    modelo = (
        await session.execute(
            select(ModeloRegistrabilidade)
            .where(ModeloRegistrabilidade.status == StatusModelo.ACTIVE.value)
            .order_by(ModeloRegistrabilidade.treinado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "modelo_ativo": modelo is not None,
        "versao": modelo.versao if modelo else None,
        "url": "/admin/aprendizado",
    }


@router.get("/painel-executivo")
async def painel_executivo(session: SessionDep, usuario: DashboardDep) -> dict:
    """Resumo cruzado das rotinas para a visão geral (cards por módulo permitido)."""
    organizacao_id = getattr(usuario, "organizacao_id", 1)
    painel: dict = {"comercial": await _bloco_comercial(session, organizacao_id)}
    if usuario.pode("finance.view"):
        painel["financeiro"] = await _bloco_financeiro(session, organizacao_id)
    if usuario.pode("legal.view"):
        painel["juridico"] = await _bloco_juridico(session, organizacao_id)
    if usuario.pode("risk.view"):
        painel["risco"] = await _bloco_risco(session, organizacao_id)
    if usuario.pode("learning.view"):
        painel["aprendizado"] = await _bloco_aprendizado(session)
    return painel


def _media_horas(intervalos: list[tuple[datetime | None, datetime | None]]) -> float | None:
    """Calcula tempos operacionais sem deixar registros inconsistentes distorcerem o KPI."""
    valores = [
        (fim - inicio).total_seconds() / 3600
        for inicio, fim in intervalos
        if inicio is not None and fim is not None and fim >= inicio
    ]
    return round(sum(valores) / len(valores), 1) if valores else None


@router.get("/indicadores-fluxo")
async def indicadores_fluxo(
    session: SessionDep,
    usuario: DashboardDep,
    dias: int = Query(default=90, ge=7, le=365),
) -> dict:
    """Conversão e tempo da jornada aceita → paga → jurídico → protocolo.

    O período é definido pelo aceite, evento que inicia a contratação. Assim,
    todas as taxas usam a mesma coorte e não misturam propostas antigas no
    denominador. Valores financeiros e etapas jurídicas continuam protegidos
    pelas permissões dos respectivos módulos.
    """
    agora = datetime.now(UTC)
    desde = agora - timedelta(days=dias)
    propostas = list(
        (
            await session.execute(
                select(PropostaComercial).where(
                    PropostaComercial.organizacao_id == usuario.organizacao_id,
                    PropostaComercial.status == "aceita",
                    PropostaComercial.aceito_em >= desde,
                )
            )
        )
        .scalars()
        .all()
    )

    aceitas = len(propostas)
    pagas = [item for item in propostas if item.pagamento_confirmado_em is not None]
    recebidas = [item for item in pagas if item.juridico_recebido_em is not None]
    protocoladas = [item for item in recebidas if item.protocolo_em is not None]

    pode_financeiro = usuario.pode("finance.view")
    pode_juridico = usuario.pode("legal.view")
    etapas = [{"id": "aceite", "label": "Propostas aceitas", "total": aceitas}]
    if pode_financeiro:
        etapas.append({"id": "pagamento", "label": "Pagamentos confirmados", "total": len(pagas)})
    if pode_juridico:
        etapas.extend(
            [
                {"id": "juridico", "label": "Recebidas pelo jurídico", "total": len(recebidas)},
                {"id": "protocolo", "label": "Protocoladas no INPI", "total": len(protocoladas)},
            ]
        )

    resposta: dict = {
        "periodo_dias": dias,
        "atualizado_em": agora,
        "etapas": etapas,
        "taxas": {},
        "tempos_medios_horas": {},
        "gargalos": {},
    }
    if pode_financeiro:
        resposta["valor_contratado"] = float(
            sum((item.honorarios or 0) + (item.taxa_gru or 0) for item in propostas)
        )
        resposta["taxas"]["aceite_pagamento"] = round(len(pagas) / aceitas, 4) if aceitas else None
        resposta["tempos_medios_horas"]["aceite_pagamento"] = _media_horas(
            [(item.aceito_em, item.pagamento_confirmado_em) for item in pagas]
        )
        resposta["gargalos"]["aguardando_pagamento"] = aceitas - len(pagas)
    if pode_juridico:
        resposta["taxas"]["pagamento_juridico"] = round(len(recebidas) / len(pagas), 4) if pagas else None
        resposta["taxas"]["juridico_protocolo"] = (
            round(len(protocoladas) / len(recebidas), 4) if recebidas else None
        )
        resposta["taxas"]["aceite_protocolo"] = round(len(protocoladas) / aceitas, 4) if aceitas else None
        resposta["tempos_medios_horas"]["pagamento_juridico"] = _media_horas(
            [(item.pagamento_confirmado_em, item.juridico_recebido_em) for item in recebidas]
        )
        resposta["tempos_medios_horas"]["juridico_protocolo"] = _media_horas(
            [(item.juridico_recebido_em, item.protocolo_em) for item in protocoladas]
        )
        resposta["tempos_medios_horas"]["aceite_protocolo"] = _media_horas(
            [(item.aceito_em, item.protocolo_em) for item in protocoladas]
        )
        resposta["gargalos"]["aguardando_juridico"] = len(pagas) - len(recebidas)
        resposta["gargalos"]["aguardando_protocolo"] = len(recebidas) - len(protocoladas)
    return resposta


@router.get("/notificacoes")
async def listar_notificacoes(
    session: SessionDep,
    usuario: DashboardDep,
    todas: bool = False,
    limite: int = 50,
) -> dict:
    """Notificações do sistema, unificando jurídico e alertas gerais.

    Por padrão retorna apenas as pendentes (uso do sino no topo do painel);
    com `todas=true` inclui também as já lidas/resolvidas, para a central
    completa em `/admin/notificacoes`.
    """
    organizacao_id = getattr(usuario, "organizacao_id", 1)
    itens: list[dict] = []

    if usuario.pode("legal.view"):
        filtros = [
            NotificacaoJuridica.organizacao_id == organizacao_id,
            NotificacaoJuridica.status != "arquivada",
            or_(
                NotificacaoJuridica.destinatario_id.is_(None),
                NotificacaoJuridica.destinatario_id == usuario.id,
            ),
        ]
        if not todas:
            filtros.append(NotificacaoJuridica.lida_em.is_(None))
        juridicas = (
            (
                await session.execute(
                    select(NotificacaoJuridica)
                    .where(*filtros)
                    .order_by(NotificacaoJuridica.criado_em.desc())
                    .limit(limite)
                )
            )
            .scalars()
            .all()
        )
        for item in juridicas:
            itens.append(
                {
                    "id": item.id,
                    "fonte": "juridico",
                    "severidade": "aviso" if item.tipo in {"vencido", "escalonado"} else "info",
                    "titulo": item.titulo,
                    "mensagem": item.mensagem,
                    "criado_em": item.criado_em,
                    "lida": item.lida_em is not None,
                    "url": "/admin/operacao-juridica",
                }
            )

    pode_producao = usuario.pode("production.manage")
    pode_comercial_alertas = usuario.pode("leads.view")
    if pode_producao or pode_comercial_alertas:
        filtros_sistema = [AlertaSistema.organizacao_id == organizacao_id]
        if pode_producao and not pode_comercial_alertas:
            filtros_sistema.append(AlertaSistema.codigo.not_in(_CODIGOS_ALERTA_COMERCIAL))
        elif pode_comercial_alertas and not pode_producao:
            filtros_sistema.append(AlertaSistema.codigo.in_(_CODIGOS_ALERTA_COMERCIAL))
        if not todas:
            filtros_sistema.append(AlertaSistema.resolvido_em.is_(None))
        alertas = (
            (
                await session.execute(
                    select(AlertaSistema)
                    .where(*filtros_sistema)
                    .order_by(AlertaSistema.criado_em.desc())
                    .limit(limite)
                )
            )
            .scalars()
            .all()
        )
        for item in alertas:
            itens.append(
                {
                    "id": item.id,
                    "fonte": "sistema",
                    "severidade": item.severidade,
                    "titulo": item.codigo.replace("_", " ").capitalize(),
                    "mensagem": item.mensagem,
                    "criado_em": item.criado_em,
                    "lida": item.resolvido_em is not None,
                    "url": _DESTINO_ALERTA.get(item.codigo, "/admin/producao"),
                }
            )

    itens.sort(key=lambda x: x["criado_em"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    pendentes = sum(1 for item in itens if not item["lida"])
    return {"total": pendentes, "total_itens": len(itens), "itens": itens}


@router.post("/notificacoes/{fonte}/{item_id}/lida")
async def marcar_notificacao_lida(fonte: str, item_id: int, session: SessionDep, usuario: DashboardDep) -> dict:
    """Marca uma notificação da central como lida/resolvida, respeitando a fonte."""
    organizacao_id = getattr(usuario, "organizacao_id", 1)
    agora = datetime.now(UTC)
    if fonte == "juridico" and usuario.pode("legal.view"):
        item = (
            await session.execute(
                select(NotificacaoJuridica).where(
                    NotificacaoJuridica.id == item_id,
                    NotificacaoJuridica.organizacao_id == organizacao_id,
                    or_(
                        NotificacaoJuridica.destinatario_id.is_(None),
                        NotificacaoJuridica.destinatario_id == usuario.id,
                    ),
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "Notificação não encontrada")
        item.status = "lida"
        item.lida_em = agora
        item.lida_por = usuario.ator
    elif fonte == "sistema" and (usuario.pode("production.manage") or usuario.pode("leads.view")):
        filtros_item = [AlertaSistema.id == item_id, AlertaSistema.organizacao_id == organizacao_id]
        if not usuario.pode("production.manage"):
            filtros_item.append(AlertaSistema.codigo.in_(_CODIGOS_ALERTA_COMERCIAL))
        item = (await session.execute(select(AlertaSistema).where(*filtros_item))).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "Notificação não encontrada")
        item.resolvido_em = agora
    else:
        raise HTTPException(404, "Notificação não encontrada")
    await session.commit()
    return {"lida": True}


@router.post("/notificacoes/{fonte}/{item_id}/nao-lida")
async def marcar_notificacao_nao_lida(fonte: str, item_id: int, session: SessionDep, usuario: DashboardDep) -> dict:
    """Reverte uma notificação da central para o estado não lida."""
    organizacao_id = getattr(usuario, "organizacao_id", 1)
    if fonte == "juridico" and usuario.pode("legal.view"):
        item = (
            await session.execute(
                select(NotificacaoJuridica).where(
                    NotificacaoJuridica.id == item_id,
                    NotificacaoJuridica.organizacao_id == organizacao_id,
                    or_(
                        NotificacaoJuridica.destinatario_id.is_(None),
                        NotificacaoJuridica.destinatario_id == usuario.id,
                    ),
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "Notificação não encontrada")
        item.status = "nova"
        item.lida_em = None
        item.lida_por = None
    elif fonte == "sistema" and (usuario.pode("production.manage") or usuario.pode("leads.view")):
        filtros_item = [AlertaSistema.id == item_id, AlertaSistema.organizacao_id == organizacao_id]
        if not usuario.pode("production.manage"):
            filtros_item.append(AlertaSistema.codigo.in_(_CODIGOS_ALERTA_COMERCIAL))
        item = (await session.execute(select(AlertaSistema).where(*filtros_item))).scalar_one_or_none()
        if item is None:
            raise HTTPException(404, "Notificação não encontrada")
        item.resolvido_em = None
    else:
        raise HTTPException(404, "Notificação não encontrada")
    await session.commit()
    return {"lida": False}


@router.post("/notificacoes/marcar-todas")
async def marcar_todas_notificacoes(
    session: SessionDep,
    usuario: DashboardDep,
    lida: bool,
) -> dict:
    """Marca ou reverte em lote todas as notificações visíveis ao usuário."""
    organizacao_id = getattr(usuario, "organizacao_id", 1)
    agora = datetime.now(UTC)
    afetadas = 0

    if usuario.pode("legal.view"):
        filtros = [
            NotificacaoJuridica.organizacao_id == organizacao_id,
            NotificacaoJuridica.status != "arquivada",
            or_(
                NotificacaoJuridica.destinatario_id.is_(None),
                NotificacaoJuridica.destinatario_id == usuario.id,
            ),
            NotificacaoJuridica.lida_em.is_(None) if lida else NotificacaoJuridica.lida_em.is_not(None),
        ]
        juridicas = (await session.execute(select(NotificacaoJuridica).where(*filtros))).scalars().all()
        for item in juridicas:
            item.status = "lida" if lida else "nova"
            item.lida_em = agora if lida else None
            item.lida_por = usuario.ator if lida else None
            afetadas += 1

    pode_producao = usuario.pode("production.manage")
    pode_comercial_alertas = usuario.pode("leads.view")
    if pode_producao or pode_comercial_alertas:
        filtros_sistema = [
            AlertaSistema.organizacao_id == organizacao_id,
            AlertaSistema.resolvido_em.is_(None) if lida else AlertaSistema.resolvido_em.is_not(None),
        ]
        if pode_producao and not pode_comercial_alertas:
            filtros_sistema.append(AlertaSistema.codigo.not_in(_CODIGOS_ALERTA_COMERCIAL))
        elif pode_comercial_alertas and not pode_producao:
            filtros_sistema.append(AlertaSistema.codigo.in_(_CODIGOS_ALERTA_COMERCIAL))
        alertas = (await session.execute(select(AlertaSistema).where(*filtros_sistema))).scalars().all()
        for item in alertas:
            item.resolvido_em = agora if lida else None
            afetadas += 1

    await session.commit()
    return {"afetadas": afetadas, "lida": lida}
