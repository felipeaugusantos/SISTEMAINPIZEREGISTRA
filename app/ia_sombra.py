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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cadencia_email import dentro_do_horario_comercial
from app.crm import obter_politica_crm
from app.feature_flags import flag_ativa_para_organizacao, registrar_resultado_flag
from app.models import (
    AvaliacaoRiscoMarca,
    ContatoLead,
    EmbeddingLead,
    ExplicacaoAnaliseMarca,
    Lead,
    PesquisaMarca,
    PoliticaCRM,
    QualificacaoIALead,
    RespostaEmailLead,
    StatusLead,
    SugestaoIALead,
)
from app.queueing import enfileirar
from app.settings import get_settings

MAXIMO_LEADS_POR_EXECUCAO = 20
TAMANHO_MAXIMO_CORPO_RESPOSTA = 500
MARCADOR_RESUMO = "RESUMO:"
MARCADOR_PROXIMA_ACAO = "PROXIMA_ACAO:"
RESULTADOS_LEAD_CONHECIDOS = ("ganho", "perdido")
# Rollout gradual do RAG local (Fase 4, feature flags por organização):
# desligado por padrão, ativado organização por organização em
# /admin/feature-flags antes do rollout geral. Ver
# app.feature_flags.flag_ativa_para_organizacao.
FEATURE_FLAG_RAG = "rag-local-ia-sombra"

ChamadaIA = Callable[[str], Awaitable[str]]
ChamadaEmbedding = Callable[[str], Awaitable[list[float]]]


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
    avaliacao_risco: AvaliacaoRiscoMarca | None = None,
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
        nivel = avaliacao_risco.nivel if avaliacao_risco is not None else "não calculado"
        linhas.append(f"Última pesquisa de marca: \"{pesquisa.marca}\" (risco: {nivel})")
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


async def gerar_embedding_ollama(texto: str, *, http_client: httpx.AsyncClient | None = None) -> list[float]:
    """Gera o embedding de um texto via Ollama (mesmo servidor de
    chamar_ollama, endpoint /api/embeddings), usado pelo RAG local
    (pgvector) para indexar e buscar leads parecidos. Local, sem chave de
    API, sem custo por chamada."""
    settings = get_settings()
    url = f"http://{settings.ia_sombra_host}:{settings.ia_sombra_port}/api/embeddings"
    corpo = {"model": settings.ia_sombra_embedding_modelo, "prompt": texto}
    cliente_proprio = http_client is None
    cliente = http_client or httpx.AsyncClient(timeout=settings.ia_sombra_timeout_segundos)
    try:
        resposta = await cliente.post(url, json=corpo)
        resposta.raise_for_status()
        return list(resposta.json().get("embedding", []))
    finally:
        if cliente_proprio:
            await cliente.aclose()


async def buscar_leads_similares(
    session: AsyncSession,
    organizacao_id: int,
    embedding_consulta: list[float],
    *,
    excluir_lead_id: int,
    limite: int = 3,
) -> list[EmbeddingLead]:
    """Leads com resultado conhecido (ganho/perdido) mais parecidos com o
    embedding de consulta, por distância de cosseno (pgvector). Usado como
    precedente real na sugestão de próxima ação de outro lead."""
    return list(
        (
            await session.execute(
                select(EmbeddingLead)
                .where(
                    EmbeddingLead.organizacao_id == organizacao_id,
                    EmbeddingLead.lead_id != excluir_lead_id,
                )
                .order_by(EmbeddingLead.embedding.cosine_distance(embedding_consulta))
                .limit(limite)
            )
        ).scalars()
    )


async def _contexto_casos_semelhantes(
    session: AsyncSession, *, lead: Lead, contexto: str, gerar_embedding: ChamadaEmbedding
) -> str:
    """Precedentes reais (leads com resultado conhecido) parecidos com o
    lead atual, para o modelo ter exemplos concretos em vez de só o
    histórico isolado. Indisponibilidade do modelo de embeddings ou do
    pgvector nunca derruba a sugestão principal -- vira "sem precedentes".

    Atrás da feature flag FEATURE_FLAG_RAG (rollout gradual por
    organização, Fase 4) -- desligada por padrão, backend valida a flag
    aqui mesmo (não só esconder algo em tela, que nem existiria pra este
    job de fundo)."""
    if not await flag_ativa_para_organizacao(session, FEATURE_FLAG_RAG, lead.organizacao_id):
        return ""
    try:
        embedding_consulta = await gerar_embedding(contexto)
        similares = await buscar_leads_similares(
            session, lead.organizacao_id, embedding_consulta, excluir_lead_id=lead.id
        )
    except Exception as exc:  # noqa: BLE001 -- indisponibilidade do RAG nunca quebra a sugestão principal
        # Reporte opcional para o monitoramento por grupo da Fase 5 --
        # "uso" já é registrado automaticamente por
        # flag_ativa_para_organizacao acima; aqui só o resultado, pra
        # alimentar o circuito de interrupção automática se o Ollama/
        # pgvector começar a falhar muito.
        await registrar_resultado_flag(
            FEATURE_FLAG_RAG, lead.organizacao_id, "falha_integracao", detalhes={"erro": type(exc).__name__}
        )
        return ""
    if not similares:
        return ""
    linhas = ["Casos semelhantes já concluídos (use só como referência, nunca repita literalmente):"]
    linhas.extend(f"- Resultado: {item.resultado} — {item.resumo_indexado}" for item in similares)
    return "\n".join(linhas)


def montar_resumo_lead_resultado(
    lead: Lead, contatos: list[ContatoLead], respostas: list[RespostaEmailLead], pesquisa: PesquisaMarca | None
) -> str:
    """Texto indexado em EmbeddingLead para um lead com resultado conhecido
    -- mesmo recorte de montar_contexto_lead, mais o resultado final."""
    contexto = montar_contexto_lead(lead, contatos, respostas, pesquisa)
    return f"{contexto}\nResultado final: {lead.resultado}"


async def indexar_lead_para_rag(
    session: AsyncSession, lead: Lead, *, gerar_embedding: ChamadaEmbedding = gerar_embedding_ollama
) -> EmbeddingLead | None:
    """Indexa (embedding, upsert por lead_id) um lead com resultado
    conhecido (ganho/perdido) para servir de precedente às sugestões de
    outros leads. Sem resultado ainda não há o que aprender -- devolve None
    sem indexar nem consultar nada."""
    if lead.resultado not in RESULTADOS_LEAD_CONHECIDOS:
        return None
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
    resumo = montar_resumo_lead_resultado(lead, contatos, respostas, pesquisa)
    embedding = await gerar_embedding(resumo)
    existente = (
        await session.execute(select(EmbeddingLead).where(EmbeddingLead.lead_id == lead.id))
    ).scalar_one_or_none()
    if existente is not None:
        existente.resumo_indexado = resumo
        existente.resultado = lead.resultado
        existente.modelo = settings.ia_sombra_embedding_modelo
        existente.embedding = embedding
        return existente
    registro = EmbeddingLead(
        organizacao_id=lead.organizacao_id,
        lead_id=lead.id,
        resumo_indexado=resumo,
        resultado=lead.resultado,
        modelo=settings.ia_sombra_embedding_modelo,
        embedding=embedding,
    )
    session.add(registro)
    return registro


async def indexar_embeddings_leads_pendentes(
    session: AsyncSession, *, gerar_embedding: ChamadaEmbedding = gerar_embedding_ollama
) -> int:
    """Job de manutenção periódica: indexa leads com resultado conhecido que
    ainda não têm embedding (ou cujo resultado mudou desde a última
    indexação), nas organizações com IA em sombra ativa. Mesmo par de flags
    (settings.ia_sombra_enabled + PoliticaCRM.ia_sombra_ativa) dos outros
    jobs desta frente -- ver app.worker "leads.indexar_rag"."""
    settings = get_settings()
    if not settings.ia_sombra_enabled:
        return 0
    organizacoes_ativas = list(
        (await session.execute(select(PoliticaCRM.organizacao_id).where(PoliticaCRM.ia_sombra_ativa.is_(True)))).scalars()
    )
    if not organizacoes_ativas:
        return 0

    pendentes = list(
        (
            await session.execute(
                select(Lead)
                .outerjoin(EmbeddingLead, EmbeddingLead.lead_id == Lead.id)
                .where(
                    Lead.organizacao_id.in_(organizacoes_ativas),
                    Lead.resultado.in_(RESULTADOS_LEAD_CONHECIDOS),
                    or_(EmbeddingLead.id.is_(None), EmbeddingLead.resultado != Lead.resultado),
                )
                .limit(MAXIMO_LEADS_POR_EXECUCAO)
            )
        ).scalars()
    )
    indexados = 0
    for lead in pendentes:
        try:
            await indexar_lead_para_rag(session, lead, gerar_embedding=gerar_embedding)
        except Exception:  # noqa: BLE001 -- job de manutenção não pode quebrar por falha do modelo
            continue
        indexados += 1
    return indexados


async def gerar_sugestao_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    chamar_ia: ChamadaIA = chamar_ollama,
    gerar_embedding: ChamadaEmbedding = gerar_embedding_ollama,
) -> SugestaoIALead:
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
    avaliacao_risco = (
        (
            await session.execute(
                select(AvaliacaoRiscoMarca).where(AvaliacaoRiscoMarca.pesquisa_id == pesquisa.id)
            )
        ).scalar_one_or_none()
        if pesquisa is not None
        else None
    )

    eventos = [d for d in (lead.atualizado_em, *(c.criado_em for c in contatos), *(r.recebido_em for r in respostas)) if d]
    baseado_em_evento_em = max(eventos) if eventos else datetime.now(UTC)

    contexto = montar_contexto_lead(lead, contatos, respostas, pesquisa, avaliacao_risco)
    casos_semelhantes = await _contexto_casos_semelhantes(
        session, lead=lead, contexto=contexto, gerar_embedding=gerar_embedding
    )
    if casos_semelhantes:
        contexto = f"{contexto}\n\n{casos_semelhantes}"
    # Valores explícitos em vez de depender dos defaults de coluna do
    # SQLAlchemy: eles só são aplicados no flush -- se algo ler o objeto
    # antes disso (como o caminho de erro abaixo, que nunca toca resumo/
    # sugestao_proxima_acao), os atributos ficariam None em vez de "".
    sugestao = SugestaoIALead(
        organizacao_id=lead.organizacao_id,
        lead_id=lead.id,
        modelo=settings.ia_sombra_modelo,
        resumo="",
        sugestao_proxima_acao="",
        status="pendente",
        baseado_em_evento_em=baseado_em_evento_em,
    )
    try:
        texto = await chamar_ia(f"{_prompt_sistema()}\n\nHistórico do lead:\n{contexto}")
        sugestao.resumo, sugestao.sugestao_proxima_acao = _analisar_resposta(texto)
    except Exception as exc:  # noqa: BLE001 -- job de manutenção não pode quebrar por falha do modelo
        sugestao.erro = f"{type(exc).__name__}: {exc}"
    session.add(sugestao)
    return sugestao


async def gerar_sugestoes_ia_pendentes(
    session: AsyncSession,
    *,
    chamar_ia: ChamadaIA = chamar_ollama,
    gerar_embedding: ChamadaEmbedding = gerar_embedding_ollama,
) -> int:
    """Job de manutenção periódica (worker.TAREFAS_MANUTENCAO_HORARIA): gera
    sugestões para leads com atividade nova, nas organizações que optaram
    por ativar a IA em sombra. Sempre agendado -- inerte (devolve 0) quando
    a flag global está desligada, mesmo padrão de cadencia.enviar_emails_pendentes
    para email_enabled.

    Só roda FORA do horário comercial (mesma janela de app.cadencia_email,
    invertida): cada chamada ao modelo local consome CPU cheia por vários
    segundos, e até MAXIMO_LEADS_POR_EXECUCAO chamadas por execução
    competiriam com o tráfego real da API/banco justo durante o horário de
    maior uso -- decisão do usuário após avaliar os riscos de deixar a IA
    em sombra ativa."""
    settings = get_settings()
    if not settings.ia_sombra_enabled:
        return 0
    if dentro_do_horario_comercial(
        datetime.now(UTC), settings.cadencia_email_horario_inicio, settings.cadencia_email_horario_fim
    ):
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
        await gerar_sugestao_lead(session, lead, chamar_ia=chamar_ia, gerar_embedding=gerar_embedding)
        criadas += 1
    return criadas


# --- Frente A: explicação em linguagem simples do risco já calculado -----
# (análise de marca). NUNCA recalcula nem substitui o resultado técnico do
# motor determinístico (app.trademarks.risk) -- só traduz o que já está
# persistido em AvaliacaoRiscoMarca. ---

MARCADOR_EXPLICACAO = "EXPLICACAO:"


def _prompt_sistema_explicacao_risco() -> str:
    return (
        "Você é um assistente que traduz para linguagem simples o resultado de uma "
        "análise de risco de conflito de marcas, já calculado por um motor determinístico. "
        "Responda em português, em texto simples, EXATAMENTE neste formato, sem nenhum "
        "texto antes ou depois:\n\n"
        f"{MARCADOR_EXPLICACAO} <um ou dois parágrafos curtos explicando, em linguagem "
        "simples, por que o risco foi classificado nesse nível, citando os principais "
        "conflitos encontrados>\n\n"
        "Regras obrigatórias: nunca sugira um nível de risco diferente do já calculado; "
        "nunca invente conflito, processo ou número que não esteja nos dados abaixo; "
        "deixe claro que é uma tradução do cálculo determinístico, não uma nova análise; "
        "nunca afirme que a marca está disponível ou garantida para registro."
    )


def montar_contexto_avaliacao_risco(avaliacao: AvaliacaoRiscoMarca) -> str:
    """Monta o texto de entrada a partir só do que já está persistido em
    AvaliacaoRiscoMarca (pontuação, nível, principais conflitos) -- sem
    nenhuma nova consulta ao motor de busca/risco."""
    linhas = [f"Pontuação de risco: {avaliacao.pontuacao}/100", f"Nível classificado: {avaliacao.nivel}"]
    if avaliacao.nivel_humano:
        linhas.append(f"Avaliação humana registrada: {avaliacao.nivel_humano} ({avaliacao.observacoes_humanas or ''})".strip())
    conflitos = avaliacao.principais_conflitos or []
    if conflitos:
        linhas.append("Principais conflitos encontrados:")
        for conflito in conflitos[:5]:
            titulo = conflito.get("titulo", "sem título")
            pontuacao_conflito = conflito.get("pontuacao")
            nivel_conflito = conflito.get("nivel")
            linhas.append(f"- {titulo} (pontuação {pontuacao_conflito}, nível {nivel_conflito})")
    else:
        linhas.append("Nenhum conflito relevante encontrado.")
    return "\n".join(linhas)


def _analisar_explicacao(texto: str) -> str:
    if MARCADOR_EXPLICACAO not in texto:
        return texto.strip()
    return texto.split(MARCADOR_EXPLICACAO, 1)[1].strip()


async def gerar_explicacao_risco(
    session: AsyncSession, avaliacao: AvaliacaoRiscoMarca, organizacao_id: int, *, chamar_ia: ChamadaIA = chamar_ollama
) -> ExplicacaoAnaliseMarca:
    """Gera (e persiste, sem commit) a explicação em linguagem simples de uma
    avaliação de risco. Nunca propaga exceção de chamada ao modelo.

    organizacao_id vem de fora (join com PesquisaMarca já feito pelo
    chamador) -- AvaliacaoRiscoMarca não tem organizacao_id nem relação
    carregada para PesquisaMarca."""
    settings = get_settings()
    explicacao = ExplicacaoAnaliseMarca(
        organizacao_id=organizacao_id,
        pesquisa_id=avaliacao.pesquisa_id,
        avaliacao_risco_id=avaliacao.id,
        modelo=settings.ia_sombra_modelo,
        explicacao="",
        status="pendente",
        baseado_em_calculado_em=avaliacao.calculado_em,
    )
    try:
        contexto = montar_contexto_avaliacao_risco(avaliacao)
        texto = await chamar_ia(f"{_prompt_sistema_explicacao_risco()}\n\nResultado calculado:\n{contexto}")
        explicacao.explicacao = _analisar_explicacao(texto)
    except Exception as exc:  # noqa: BLE001 -- job de manutenção não pode quebrar por falha do modelo
        explicacao.erro = f"{type(exc).__name__}: {exc}"
    session.add(explicacao)
    return explicacao


async def gerar_explicacoes_risco_pendentes(session: AsyncSession, *, chamar_ia: ChamadaIA = chamar_ollama) -> int:
    """Job de manutenção periódica: gera explicações para avaliações de
    risco novas ou recalculadas desde a última explicação, nas organizações
    que optaram por ativar a IA em sombra. Inerte (devolve 0) quando a flag
    global está desligada ou dentro do horário comercial -- mesmo motivo de
    gerar_sugestoes_ia_pendentes."""
    settings = get_settings()
    if not settings.ia_sombra_enabled:
        return 0
    if dentro_do_horario_comercial(
        datetime.now(UTC), settings.cadencia_email_horario_inicio, settings.cadencia_email_horario_fim
    ):
        return 0

    organizacoes_ativas = list(
        (await session.execute(select(PoliticaCRM.organizacao_id).where(PoliticaCRM.ia_sombra_ativa.is_(True)))).scalars()
    )
    if not organizacoes_ativas:
        return 0

    ultima_explicacao_por_avaliacao = dict(
        (
            await session.execute(
                select(ExplicacaoAnaliseMarca.avaliacao_risco_id, func.max(ExplicacaoAnaliseMarca.baseado_em_calculado_em))
                .group_by(ExplicacaoAnaliseMarca.avaliacao_risco_id)
            )
        ).all()
    )
    avaliacoes = (
        await session.execute(
            select(AvaliacaoRiscoMarca, PesquisaMarca.organizacao_id)
            .join(PesquisaMarca, PesquisaMarca.id == AvaliacaoRiscoMarca.pesquisa_id)
            .where(PesquisaMarca.organizacao_id.in_(organizacoes_ativas))
        )
    ).all()

    criadas = 0
    for avaliacao, organizacao_id in avaliacoes:
        if criadas >= MAXIMO_LEADS_POR_EXECUCAO:
            break
        ultima = ultima_explicacao_por_avaliacao.get(avaliacao.id)
        if ultima is not None and avaliacao.calculado_em <= ultima:
            continue
        await gerar_explicacao_risco(session, avaliacao, organizacao_id, chamar_ia=chamar_ia)
        criadas += 1
    return criadas


# --- Frente B: qualificação da IA na captação de leads --------------------
# Prioridade + observação sugeridas no momento da chegada de um lead novo
# (formulário público ou conversão do Radar de Prospecção), a partir só dos
# dados disponíveis na captação -- sem histórico de contato, que ainda não
# existe. Gerada uma única vez por lead (job sob demanda, não sweep
# periódico -- ver app.worker "leads.qualificar_ia"). ---

MARCADOR_PRIORIDADE = "PRIORIDADE:"
MARCADOR_OBSERVACAO = "OBSERVACAO:"
PRIORIDADES_VALIDAS = frozenset({"alta", "media", "baixa"})


def _prompt_sistema_qualificacao_captacao() -> str:
    return (
        "Você é um assistente de apoio à qualificação comercial de um escritório de "
        "registro de marcas. Vai receber os dados de um lead recém-chegado (ainda sem "
        "nenhum contato humano) e deve sugerir uma prioridade de atendimento. Responda em "
        "português, em texto simples, EXATAMENTE neste formato, sem nenhum texto antes ou "
        "depois:\n\n"
        f"{MARCADOR_PRIORIDADE} <apenas uma palavra: alta, media ou baixa>\n"
        f"{MARCADOR_OBSERVACAO} <uma frase curta explicando o motivo da prioridade sugerida>\n\n"
        "Regras obrigatórias: nunca invente informação que não esteja nos dados abaixo; "
        "se os dados forem insuficientes para avaliar, sugira prioridade media e diga isso "
        "na observação; esta sugestão nunca decide sozinha -- é só apoio para o comercial "
        "priorizar quem atender primeiro."
    )


def montar_contexto_captacao_lead(lead: Lead) -> str:
    """Monta o texto de entrada só com os dados disponíveis no momento da
    criação do lead (sem histórico de contato, que ainda não existe)."""
    linhas = [
        f"Nome: {lead.nome}",
        f"Empresa: {lead.empresa or 'não informada'}",
        f"Marca de interesse: {lead.marca or 'não informada'}",
        f"Origem: {lead.origem}",
    ]
    if lead.utm_source or lead.utm_medium or lead.utm_campaign:
        linhas.append(f"Campanha: {lead.utm_source or '?'}/{lead.utm_medium or '?'}/{lead.utm_campaign or '?'}")
    return "\n".join(linhas)


def _analisar_qualificacao(texto: str) -> tuple[str, str]:
    """Extrai (prioridade, observacao). Prioridade fora do vocabulário
    esperado ou marcadores ausentes caem em "media" -- nunca derruba o job
    por um modelo pequeno não seguir o formato à risca."""
    if MARCADOR_PRIORIDADE not in texto or MARCADOR_OBSERVACAO not in texto:
        return "media", texto.strip()
    _, resto = texto.split(MARCADOR_PRIORIDADE, 1)
    prioridade_bruta, observacao = resto.split(MARCADOR_OBSERVACAO, 1)
    prioridade = prioridade_bruta.strip().lower()
    if prioridade not in PRIORIDADES_VALIDAS:
        prioridade = "media"
    return prioridade, observacao.strip()


async def gerar_qualificacao_lead(
    session: AsyncSession, lead: Lead, *, chamar_ia: ChamadaIA = chamar_ollama
) -> QualificacaoIALead:
    """Gera (e persiste, sem commit) a qualificação de captação de um lead.
    Nunca propaga exceção de chamada ao modelo."""
    settings = get_settings()
    qualificacao = QualificacaoIALead(
        organizacao_id=lead.organizacao_id,
        lead_id=lead.id,
        modelo=settings.ia_sombra_modelo,
        prioridade="media",
        observacao="",
        status="pendente",
    )
    try:
        contexto = montar_contexto_captacao_lead(lead)
        texto = await chamar_ia(f"{_prompt_sistema_qualificacao_captacao()}\n\nDados do lead:\n{contexto}")
        qualificacao.prioridade, qualificacao.observacao = _analisar_qualificacao(texto)
    except Exception as exc:  # noqa: BLE001 -- job não pode quebrar por falha do modelo
        qualificacao.erro = f"{type(exc).__name__}: {exc}"
    session.add(qualificacao)
    return qualificacao


async def enfileirar_qualificacao_ia_se_ativa(session: AsyncSession, lead: Lead) -> None:
    """Enfileira o job de qualificação de captação (leads.qualificar_ia) só
    se a IA em sombra estiver ativa -- kill-switch global E opt-in da
    organização -- para não empilhar jobs mortos no Redis quando a
    funcionalidade está desligada (comportamento padrão). Chamado nos dois
    pontos de captação de lead: formulário público (app.api.leads.criar_lead)
    e conversão do Radar de Prospecção (app.api.prospeccao.converter_prospect_em_lead).

    Diferente dos dois jobs periódicos acima, NÃO é restrito ao horário fora
    do comercial -- é um evento único disparado no momento da captação, e
    adiar a qualificação para a madrugada tiraria o sentido de "prioridade
    para o comercial já ao abrir o lead" (é 1 chamada por lead, não uma
    varredura de até 20 registros)."""
    settings = get_settings()
    if not settings.ia_sombra_enabled:
        return
    politica = await obter_politica_crm(session, lead.organizacao_id)
    if not politica.ia_sombra_ativa:
        return
    await enfileirar(
        "leads.qualificar_ia",
        {"lead_id": lead.id, "organizacao_id": lead.organizacao_id},
        idempotency_key=f"{lead.id}:qualificacao_ia",
    )
