import re
import unicodedata
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ORDEM_FASE_LEAD,
    EmpresaCRM,
    EventoDominio,
    HistoricoFaseLead,
    Lead,
    LembreteCRM,
    PoliticaCRM,
    RegraAutomacao,
    StatusLead,
)


def registrar_consentimento_titular(lead: Lead, versao_termo: str) -> None:
    """Consentimento capturado de verdade no formulário público (checkbox
    marcado pelo titular). versao_termo vem de Organizacao.politica_privacidade_versao
    -- a versão que a pessoa efetivamente viu ao marcar o checkbox. Achado L13
    do plano Leads/CRM."""
    lead.consentimento_em = datetime.now(UTC)
    lead.consentimento_versao_termo = versao_termo
    lead.consentimento_base_legal = "consentimento_titular"


def registrar_consentimento_operador(lead: Lead, operador_id: int) -> None:
    """Lead cadastrado por um atendente (ligação, reunião etc.) -- o titular não
    marcou nenhum checkbox nesse momento, então a base legal é outra (execução
    de atendimento já solicitado pelo próprio contato), não "consentimento".
    Sem versao_termo: nenhum termo foi de fato exibido nesse fluxo. Achado L13
    do plano Leads/CRM."""
    lead.consentimento_em = datetime.now(UTC)
    lead.consentimento_base_legal = "interesse_legitimo_atendimento"
    lead.consentimento_registrado_por = operador_id


def registrar_evento_operacional(
    session: AsyncSession,
    *,
    organizacao_id: int,
    dominio: str,
    tipo: str,
    entidade_tipo: str,
    entidade_id: int | str,
    ator: str,
    ator_id: int | None = None,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> EventoDominio:
    evento = EventoDominio(
        organizacao_id=organizacao_id,
        dominio=dominio,
        tipo=tipo,
        versao=1,
        entidade_tipo=entidade_tipo,
        entidade_id=str(entidade_id),
        ator_id=ator_id,
        ator=ator[:254],
        payload=payload or {},
        idempotency_key=idempotency_key,
    )
    session.add(evento)
    return evento


async def obter_politica_crm(session: AsyncSession, organizacao_id: int) -> PoliticaCRM:
    politica = (
        await session.execute(select(PoliticaCRM).where(PoliticaCRM.organizacao_id == organizacao_id))
    ).scalar_one_or_none()
    return politica or PoliticaCRM(
        organizacao_id=organizacao_id,
        exigir_responsavel=True,
        atribuir_ao_operador=False,
        exigir_proxima_acao=True,
        dias_proxima_acao_padrao=None,
    )


async def aplicar_politica_oportunidade(session: AsyncSession, lead: Lead, operador_id: int | None = None) -> list[str]:
    """Aplica defaults e retorna os campos obrigatórios ainda ausentes."""
    politica = await obter_politica_crm(session, lead.organizacao_id)
    if lead.responsavel_id is None and politica.atribuir_ao_operador and operador_id:
        lead.responsavel_id = operador_id
    if lead.proxima_acao_em is None and politica.dias_proxima_acao_padrao is not None:
        lead.proxima_acao_em = datetime.now(UTC) + timedelta(days=politica.dias_proxima_acao_padrao)
    faltando: list[str] = []
    if politica.exigir_responsavel and lead.responsavel_id is None:
        faltando.append("responsável")
    if politica.exigir_proxima_acao and lead.proxima_acao_em is None:
        faltando.append("próxima ação")
    return faltando


# --- Regras de automação embutidas (Terceira entrega, item 2) --------------
# evento: "fase" ou "status"; gatilho: valor que dispara a regra.
REGRAS_AUTOMACAO: dict[str, dict] = {
    "followup_proposta": {
        "evento": "fase",
        "gatilho": "proposta_enviada",
        "dias": 2,
        "titulo": "Follow-up da proposta",
        "tipo": "enviar_proposta",
        "prioridade": "media",
        "descricao": "Retornar ao cliente sobre a proposta enviada.",
        "label": "Ao enviar proposta → follow-up",
    },
    "cobrar_pagamento": {
        "evento": "fase",
        "gatilho": "proposta_aceita",
        "dias": 1,
        "titulo": "Cobrar pagamento / emitir cobrança",
        "tipo": "cobrar_documentos",
        "prioridade": "alta",
        "descricao": "Proposta aceita — combinar pagamento e emitir a cobrança.",
        "label": "Ao aceitar proposta → cobrar pagamento",
    },
    "acompanhar_protocolo": {
        "evento": "fase",
        "gatilho": "protocolo_inpi",
        "dias": 3,
        "titulo": "Acompanhar protocolo no INPI",
        "tipo": "acompanhar_processo",
        "prioridade": "media",
        "descricao": "Confirmar o protocolo e o número do processo no INPI.",
        "label": "Ao protocolar → acompanhar no INPI",
    },
    "reengajar_sem_retorno": {
        "evento": "status",
        "gatilho": "sem_retorno",
        "dias": 7,
        "titulo": "Reengajar oportunidade sem retorno",
        "tipo": "retorno",
        "prioridade": "media",
        "descricao": "Cliente parou de responder — tentar reengajar.",
        "label": "Sem retorno → reengajar em N dias",
    },
}


async def aplicar_regras_automacao(session: AsyncSession, lead: Lead, evento: str, valor: str, por: str) -> list[str]:
    """Dispara as regras de automação embutidas para um evento do lead.

    Cria um LembreteCRM (tarefa/alerta) para cada regra ativa cujo gatilho bate.
    Retorna as chaves aplicadas. As regras podem ser ligadas/desligadas e ter os
    dias ajustados por organização em ``regras_automacao``.
    """
    candidatas = {
        chave: regra
        for chave, regra in REGRAS_AUTOMACAO.items()
        if regra["evento"] == evento and regra["gatilho"] == valor
    }
    if not candidatas:
        return []
    overrides = {
        row.chave: row
        for row in (
            await session.execute(
                select(RegraAutomacao).where(
                    RegraAutomacao.organizacao_id == lead.organizacao_id,
                    RegraAutomacao.chave.in_(candidatas.keys()),
                )
            )
        ).scalars()
    }
    aplicadas: list[str] = []
    for chave, regra in candidatas.items():
        override = overrides.get(chave)
        if override is not None and not override.ativo:
            continue
        dias = override.dias if override is not None else regra["dias"]
        chave_idempotencia = f"lead:{lead.id}:{evento}:{valor}:{chave}"
        # INSERT com ON CONFLICT DO NOTHING em vez de SELECT-depois-INSERT: duas
        # requisicoes quase simultaneas (ex.: duplo PATCH) podiam ambas passar
        # pelo SELECT antes de qualquer uma comitar e colidir na constraint unica
        # so no commit, virando erro 500 nao tratado. O upsert resolve a corrida
        # no proprio banco, de forma atomica.
        inserido = (
            await session.execute(
                pg_insert(LembreteCRM)
                .values(
                    organizacao_id=lead.organizacao_id,
                    lead_id=lead.id,
                    responsavel_id=lead.responsavel_id,
                    tipo=regra["tipo"],
                    prioridade=regra["prioridade"],
                    titulo=regra["titulo"],
                    descricao=regra["descricao"],
                    lembrar_em=datetime.now(UTC) + timedelta(days=max(0, dias)),
                    status="pendente",
                    criado_por=f"Automação ({por})"[:254],
                    criado_por_id=None,
                    idempotency_key=chave_idempotencia,
                )
                .on_conflict_do_nothing(constraint="uq_lembrete_crm_idempotencia")
                .returning(LembreteCRM.id)
            )
        ).scalar_one_or_none()
        if inserido is None:
            continue
        registrar_evento_operacional(
            session,
            organizacao_id=lead.organizacao_id,
            dominio="crm",
            tipo="automacao.lembrete_criado",
            entidade_tipo="lead",
            entidade_id=lead.id,
            ator=por,
            payload={"regra": chave, "evento": evento, "valor": valor, "dias": dias},
            idempotency_key=chave_idempotencia,
        )
        aplicadas.append(chave)
    return aplicadas


# --- Sincronização status (pipeline CRM) <-> fase (funil) ------------------
# A fase do funil é o eixo mais rico; ao mudar a fase o status espelha o mapa
# abaixo. A fase "contato_inicial" não força status (novo/em_contato são
# ambos válidos no começo).
MAPA_FASE_STATUS: dict[str, StatusLead] = {
    "relatorio_enviado": StatusLead.QUALIFICADO,
    "proposta_enviada": StatusLead.PROPOSTA_ENVIADA,
    "proposta_aceita": StatusLead.CONVERTIDO,
    "pagamento_realizado": StatusLead.CONVERTIDO,
    "protocolo_inpi": StatusLead.CONVERTIDO,
    "processo_inpi": StatusLead.CONVERTIDO,
}
# Fase representativa de cada status — usada para avançar o funil quando o
# status muda (nunca retrocede). sem_retorno/descartado não mexem na fase.
MAPA_STATUS_FASE: dict[str, str] = {
    "novo": "contato_inicial",
    "em_contato": "contato_inicial",
    "qualificado": "relatorio_enviado",
    "proposta_enviada": "proposta_enviada",
    "convertido": "proposta_aceita",
}


async def avancar_fase_lead(session: AsyncSession, lead: Lead, nova_fase: str, por: str, forcar: bool = False) -> bool:
    """Move o lead para ``nova_fase``, espelha o status e registra o histórico.

    Automático (``forcar=False``) só avança no funil — nunca retrocede. Manual
    (``forcar=True``) permite qualquer fase. Ao mudar a fase, o status do
    pipeline é sincronizado por ``MAPA_FASE_STATUS``. Retorna ``True`` se a fase
    mudou.
    """
    if nova_fase not in ORDEM_FASE_LEAD or lead.fase == nova_fase:
        return False
    if not forcar and ORDEM_FASE_LEAD.index(nova_fase) <= ORDEM_FASE_LEAD.index(lead.fase):
        return False
    lead.fase = nova_fase
    novo_status = MAPA_FASE_STATUS.get(nova_fase)
    if novo_status is not None and lead.status != novo_status:
        lead.status = novo_status
        # Achado L9 da auditoria Leads/CRM (Fase 1, 03/09/2026): esta era a
        # única via de conversão que não espelhava a lógica já existente no
        # PATCH manual de status (app/api/leads.py) -- um lead convertido
        # automaticamente pelo aceite de proposta ficava com status=CONVERTIDO
        # mas resultado=None, divergindo da métrica de taxa_conversao (que lê
        # Lead.resultado, não Lead.status).
        if novo_status == StatusLead.CONVERTIDO:
            lead.resultado = "ganho"
            lead.motivo_perda = None
            lead.motivo_perda_detalhe = None
    session.add(HistoricoFaseLead(organizacao_id=lead.organizacao_id, lead_id=lead.id, fase=nova_fase, por=por))
    registrar_evento_operacional(
        session,
        organizacao_id=lead.organizacao_id,
        dominio="crm",
        tipo="crm.fase_alterada",
        entidade_tipo="lead",
        entidade_id=lead.id,
        ator=por,
        payload={"fase": nova_fase, "status": lead.status.value},
    )
    await aplicar_regras_automacao(session, lead, "fase", nova_fase, por)
    return True


async def sincronizar_fase_por_status(session: AsyncSession, lead: Lead, por: str) -> bool:
    """Avança a fase do funil para refletir o ``status`` atual do lead.

    Só avança (nunca retrocede); ``sem_retorno``/``descartado`` não têm fase
    correspondente e deixam o funil como está.
    """
    fase_alvo = MAPA_STATUS_FASE.get(lead.status)
    if fase_alvo is None:
        return False
    return await avancar_fase_lead(session, lead, fase_alvo, por=por, forcar=False)


def normalizar_empresa(nome: str) -> str:
    sem_acentos = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", nome) if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", sem_acentos.strip()).casefold()


async def obter_ou_criar_empresa(session: AsyncSession, organizacao_id: int, nome: str | None) -> EmpresaCRM | None:
    nome_limpo = re.sub(r"\s+", " ", (nome or "").strip())
    if not nome_limpo:
        return None
    normalizado = normalizar_empresa(nome_limpo)
    empresa = (
        await session.execute(
            select(EmpresaCRM).where(
                EmpresaCRM.organizacao_id == organizacao_id,
                EmpresaCRM.nome_normalizado == normalizado,
            )
        )
    ).scalar_one_or_none()
    if empresa is None:
        empresa = EmpresaCRM(
            organizacao_id=organizacao_id,
            nome=nome_limpo,
            nome_normalizado=normalizado,
        )
        session.add(empresa)
        await session.flush()
    return empresa


async def buscar_lead_ativo_por_email(session: AsyncSession, organizacao_id: int, email: str) -> Lead | None:
    """Localiza o contato ativo pelo identificador único usado pelo banco.

    A empresa da pesquisa não faz parte da chave única do contato. O vínculo
    específico com a empresa fica registrado na própria pesquisa.
    """
    email_normalizado = email.strip().lower()
    if not email_normalizado:
        return None
    return (
        await session.execute(
            select(Lead)
            .where(
                Lead.organizacao_id == organizacao_id,
                Lead.arquivado_em.is_(None),
                func.lower(Lead.email) == email_normalizado,
            )
            .order_by(Lead.atualizado_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
