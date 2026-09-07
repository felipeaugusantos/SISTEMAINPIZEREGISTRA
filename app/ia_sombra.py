"""IA em sombra (leads): resumo do histórico de atendimento + sugestão de
próxima ação, gerados em segundo plano por um modelo local (Ollama).

Escopo deliberadamente restrito (decisão do usuário): nunca gera rascunho
de mensagem para o cliente, nunca envia nada sozinha -- toda sugestão
nasce com status "pendente" e exige revisão humana explícita (aprovar ou
descartar) antes de qualquer uso. Desligada por padrão em dois níveis:
settings.ia_sombra_enabled (kill-switch global) e PoliticaCRM.ia_sombra_ativa
(opt-in por organização) -- os dois precisam estar ativos para o job gerar
qualquer coisa.

O modelo roda localmente (sem chave de API, sem custo por chamada, nenhum
dado de lead sai do servidor) via Ollama (compose.yaml, profile
"ia-sombra", desligado por padrão -- ver docstring de settings.ia_sombra_enabled).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ContatoLead,
    Lead,
    PesquisaMarca,
    PoliticaCRM,
    RespostaEmailLead,
    StatusLead,
    SugestaoIALead,
)
from app.settings import get_settings

MAXIMO_LEADS_POR_EXECUCAO = 20
TAMANHO_MAXIMO_CORPO_RESPOSTA = 500
MARCADOR_RESUMO = "RESUMO:"
MARCADOR_PROXIMA_ACAO = "PROXIMA_ACAO:"

ChamadaIA = Callable[[str], Awaitable[str]]


def _prompt_sistema() -> str:
    return (
        "Você é um assistente de apoio ao atendimento comercial de um escritório de "
        "registro de marcas. Vai receber o histórico de um lead (oportunidade comercial) "
        "e deve responder em português, em texto simples, EXATAMENTE neste formato, sem "
        "nenhum texto antes ou depois:\n\n"
        f"{MARCADOR_RESUMO} <um parágrafo curto resumindo o histórico deste lead>\n"
        f"{MARCADOR_PROXIMA_ACAO} <uma frase objetiva sugerindo a próxima ação do "
        "operador humano>\n\n"
        "Regras obrigatórias: nunca escreva uma mensagem pronta para enviar ao cliente; "
        "nunca invente informação que não esteja no histórico abaixo; se o histórico for "
        "insuficiente, diga isso no resumo em vez de adivinhar."
    )


def montar_contexto_lead(
    lead: Lead,
    contatos: list[ContatoLead],
    respostas: list[RespostaEmailLead],
    pesquisa: PesquisaMarca | None,
) -> str:
    """Monta o texto de entrada do modelo a partir dos dados já carregados do lead.

    Não reaproveita a timeline completa do lead (app.api.leads.timeline_lead,
    10 fontes) -- só o recorte relevante para resumo/sugestão, para manter o
    prompt pequeno (modelo local, CPU) e a lógica testável sem depender do
    endpoint HTTP.
    """
    linhas = [
        f"Lead: {lead.nome} ({lead.empresa or 'empresa não informada'})",
        f"Origem: {lead.origem} · Fase atual: {lead.fase} · Status: {lead.status}",
        f"Criado em: {lead.criado_em.date().isoformat() if lead.criado_em else 'desconhecido'}",
    ]
    if pesquisa is not None:
        linhas.append(f"Última pesquisa de marca: \"{pesquisa.marca}\" (risco: {pesquisa.risco_nivel or 'não calculado'})")
    if contatos:
        linhas.append("Últimos contatos registrados:")
        for contato in contatos:
            data = contato.criado_em.date().isoformat() if contato.criado_em else "?"
            linhas.append(f"- [{data}] {contato.canal}: {contato.resultado or ''} {contato.observacao or ''}".strip())
    else:
        linhas.append("Nenhum contato humano registrado ainda.")
    if respostas:
        linhas.append("Últimas respostas de e-mail do lead:")
        for resposta in respostas:
            data = resposta.recebido_em.date().isoformat() if resposta.recebido_em else "?"
            corpo = (resposta.corpo or "")[:TAMANHO_MAXIMO_CORPO_RESPOSTA]
            linhas.append(f"- [{data}] {corpo}")
    return "\n".join(linhas)


def _analisar_resposta(texto: str) -> tuple[str, str]:
    """Extrai (resumo, sugestao_proxima_acao) do texto devolvido pelo modelo.

    Tolerante a formato malformado (modelo pequeno nem sempre obedece
    instruções à risca): sem os marcadores esperados, todo o texto vira o
    resumo e a sugestão fica vazia -- nunca derruba o job por causa disso.
    """
    if MARCADOR_RESUMO not in texto or MARCADOR_PROXIMA_ACAO not in texto:
        return texto.strip(), ""
    _, resto = texto.split(MARCADOR_RESUMO, 1)
    resumo, sugestao = resto.split(MARCADOR_PROXIMA_ACAO, 1)
    return resumo.strip(), sugestao.strip()


async def chamar_ollama(prompt: str, *, http_client: httpx.AsyncClient | None = None) -> str:
    settings = get_settings()
    url = f"http://{settings.ia_sombra_host}:{settings.ia_sombra_port}/api/generate"
    corpo = {"model": settings.ia_sombra_modelo, "prompt": prompt, "stream": False}
    cliente_proprio = http_client is None
    cliente = http_client or httpx.AsyncClient(timeout=settings.ia_sombra_timeout_segundos)
    try:
        resposta = await cliente.post(url, json=corpo)
        resposta.raise_for_status()
        return str(resposta.json().get("response", ""))
    finally:
        if cliente_proprio:
            await cliente.aclose()


async def gerar_sugestao_lead(session: AsyncSession, lead: Lead, *, chamar_ia: ChamadaIA = chamar_ollama) -> SugestaoIALead:
    """Gera (e persiste, sem commit) uma sugestão para um lead. Nunca propaga
    exceção de chamada ao modelo -- um erro vira SugestaoIALead.erro, para o
    job de manutenção seguir para o próximo lead sem interromper a leva."""
    settings = get_settings()
    contatos = list(
        (
            await session.execute(
                select(ContatoLead).where(ContatoLead.lead_id == lead.id).order_by(ContatoLead.criado_em.desc()).limit(5)
            )
        ).scalars()
    )
    respostas = list(
        (
            await session.execute(
                select(RespostaEmailLead)
                .where(RespostaEmailLead.lead_id == lead.id)
                .order_by(RespostaEmailLead.recebido_em.desc())
                .limit(3)
            )
        ).scalars()
    )
    pesquisa = (
        await session.execute(
            select(PesquisaMarca).where(PesquisaMarca.lead_id == lead.id).order_by(PesquisaMarca.criado_em.desc()).limit(1)
        )
    ).scalar_one_or_none()

    eventos = [d for d in (lead.atualizado_em, *(c.criado_em for c in contatos), *(r.recebido_em for r in respostas)) if d]
    baseado_em_evento_em = max(eventos) if eventos else datetime.now(UTC)

    contexto = montar_contexto_lead(lead, contatos, respostas, pesquisa)
    sugestao = SugestaoIALead(
        organizacao_id=lead.organizacao_id,
        lead_id=lead.id,
        modelo=settings.ia_sombra_modelo,
        baseado_em_evento_em=baseado_em_evento_em,
    )
    try:
        texto = await chamar_ia(f"{_prompt_sistema()}\n\nHistórico do lead:\n{contexto}")
        sugestao.resumo, sugestao.sugestao_proxima_acao = _analisar_resposta(texto)
    except Exception as exc:  # noqa: BLE001 -- job de manutenção não pode quebrar por falha do modelo
        sugestao.erro = f"{type(exc).__name__}: {exc}"
    session.add(sugestao)
    return sugestao


async def gerar_sugestoes_ia_pendentes(session: AsyncSession, *, chamar_ia: ChamadaIA = chamar_ollama) -> int:
    """Job de manutenção periódica (worker.TAREFAS_MANUTENCAO_HORARIA): gera
    sugestões para leads com atividade nova, nas organizações que optaram
    por ativar a IA em sombra. Sempre agendado -- inerte (devolve 0) quando
    a flag global está desligada, mesmo padrão de cadencia.enviar_emails_pendentes
    para email_enabled."""
    settings = get_settings()
    if not settings.ia_sombra_enabled:
        return 0

    organizacoes_ativas = list(
        (
            await session.execute(
                select(PoliticaCRM.organizacao_id).where(PoliticaCRM.ia_sombra_ativa.is_(True))
            )
        ).scalars()
    )
    if not organizacoes_ativas:
        return 0

    ultima_sugestao_por_lead = dict(
        (
            await session.execute(
                select(SugestaoIALead.lead_id, func.max(SugestaoIALead.baseado_em_evento_em)).group_by(
                    SugestaoIALead.lead_id
                )
            )
        ).all()
    )
    ultimo_contato_por_lead = dict(
        (
            await session.execute(
                select(ContatoLead.lead_id, func.max(ContatoLead.criado_em)).group_by(ContatoLead.lead_id)
            )
        ).all()
    )
    ultima_resposta_por_lead = dict(
        (
            await session.execute(
                select(RespostaEmailLead.lead_id, func.max(RespostaEmailLead.recebido_em)).group_by(
                    RespostaEmailLead.lead_id
                )
            )
        ).all()
    )
    leads_abertos = list(
        (
            await session.execute(
                select(Lead).where(
                    Lead.organizacao_id.in_(organizacoes_ativas),
                    Lead.arquivado_em.is_(None),
                    Lead.status.notin_((StatusLead.CONVERTIDO, StatusLead.DESCARTADO)),
                )
            )
        ).scalars()
    )

    criadas = 0
    for lead in leads_abertos:
        if criadas >= MAXIMO_LEADS_POR_EXECUCAO:
            break
        eventos_lead = [
            d
            for d in (lead.atualizado_em, ultimo_contato_por_lead.get(lead.id), ultima_resposta_por_lead.get(lead.id))
            if d is not None
        ]
        ultima_atividade = max(eventos_lead) if eventos_lead else None
        ultima_sugestao = ultima_sugestao_por_lead.get(lead.id)
        if ultima_sugestao is not None and (ultima_atividade is None or ultima_atividade <= ultima_sugestao):
            continue
        await gerar_sugestao_lead(session, lead, chamar_ia=chamar_ia)
        criadas += 1
    return criadas
